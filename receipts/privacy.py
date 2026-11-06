"""Deterministic redaction for share-safe receipts.

The point is not to hide that something was removed — a receipt is evidence —
but to remove what must not travel, and to declare exactly what was removed so
the reader can see the shape of the document without seeing the secret.

Contract
--------
``redact_manifest(manifest, words=None) -> (redacted_manifest, report)``

The walk is a deep copy over dict / list / str; tuples come back as lists
because the output is JSON-shaped. Dict keys are redacted too (a manifest keyed
by an e-mail address must not leak it); non-string scalars pass through
unchanged. ``words`` is either a list of extra words or a path to a word-list
file (see :func:`load_words`).

Placeholders, one family per class, numbered in document order::

    [secret-1]   private keys, JWTs, bearer/authorization values, provider API
                 keys, URL credentials, sensitive KEY=value assignments
    [email-1]    e-mail addresses
    [phone-1]    phone numbers (8-15 digits)
    [ip-1]       IPv4 addresses
    [user]       the account segment of a user path; the prefix is kept, so
                 ``C:\\Users\\ryoma\\x`` becomes ``C:\\Users\\[user]\\x``
    [word-1]     entries from the user word list (case-insensitive)

Every *occurrence* gets its own number, so the report's totals are exactly the
number of placeholders in the redacted document. Identical literals are not
linked to one another. Placeholders already present in the input are preserved
(they are inert) and are not counted in the report.

Guarantees
----------
* **Deterministic.** No clocks, hashes, uuids or set iteration reach the output:
  the same manifest and the same word list produce identical bytes.
* **Longest secret wins.** Rules claim spans in priority order; once a span is
  claimed no lower-priority rule may read it. A URL credential is therefore
  never half-eaten by the e-mail rule, and a JWT is never split by the
  API-key rule.
* **Placeholders are inert.** A second pass over an already-redacted document
  changes nothing and reports zero.
* **No silent leftovers.** After the pass the document is re-scanned with the
  structured rules; ``report["residual"]`` is empty when the pass is clean.

Report shape (only classes/rules that fired are present)::

    {
      "version": 1,
      "generated_at": None,          # the caller may stamp it; the engine never does
      "total": 7,                    # == sum(classes.values())
      "classes": {"secret": 3, "email": 1, ...},
      "rules": {"api_key": 1, "jwt": 1, ...},
      "fields": {"$.source.path": {"user": 1}, ...},   # document order
      "words": 2,                    # entries loaded from the word list
      "residual": {}                 # rule -> leftovers; {} means clean
    }

Limits. This is pattern matching, not a guarantee: a secret that matches none
of the rules, or one stored as a bare number, is not removed. A bare account
handle (an ``ls -l`` owner column, a ``whoami`` line) is not a path and is not
matched — put it in the word list. Use the word list for client names and
anything domain-specific.
"""
from __future__ import annotations

import json
import re
from bisect import bisect_left
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Iterable, Iterator

__all__ = [
    "CLASSES",
    "RULES",
    "PLACEHOLDER_TOKEN_RE",
    "Redactor",
    "canonical_json",
    "count_placeholders",
    "leaks",
    "load_words",
    "redact_json_text",
    "redact_manifest",
    "redact_payload",
    "redact_text",
]

REPORT_VERSION = 1

#: Report order for the placeholder families (secrets first — they matter most).
CLASSES = ("secret", "email", "phone", "ip", "user", "word")

#: A placeholder already in the document. Never redacted, and claimed before any
#: rule so that ``[secret-1]`` cannot be re-read by the word list.
PLACEHOLDER_TOKEN_RE = re.compile(r"\[(?:secret|email|phone|ip|word)-\d+\]|\[user\]")

_PLACEHOLDER_FULL_RE = re.compile(r"\A(?:\[(?:secret|email|phone|ip|word)-\d+\]|\[user\])\Z")
_ENV_REFERENCE_RE = re.compile(r"\A\$(?:\{[^}]+\}|[A-Za-z_][A-Za-z0-9_]*)\Z")
_NON_SECRET_VALUES = frozenset({"true", "false", "null", "none", "yes", "no", "on", "off", ""})
_NUMBER_RE = re.compile(r"\A[+-]?\d+(?:\.\d+)?\Z")


# ---------------------------------------------------------------------------
# rules
# ---------------------------------------------------------------------------

def _whole(_match: re.Match, placeholder: str) -> str:
    """Replace the whole match — the default."""
    return placeholder


def _keep_group(group: int, tail: str = "") -> Callable[[re.Match, str], str]:
    """Keep a literal prefix (by group) and swap the rest for the placeholder."""
    def render(match: re.Match, placeholder: str) -> str:
        return match.group(group) + placeholder + tail
    return render


def _render_env(match: re.Match, placeholder: str) -> str:
    quote = match.group("q") or ""
    return "%s%s%s%s%s%s" % (
        match.group("lead") or "",
        match.group("key"),
        match.group("sep"),
        quote,
        placeholder,
        quote,
    )


def _render_authorization(match: re.Match, placeholder: str) -> str:
    return match.group(1) + (match.group(2) or "") + placeholder + (match.group(4) or "")


# --- validators ------------------------------------------------------------

def _env_is_secret(match: re.Match) -> bool:
    if not _SENSITIVE_KEY_RE.search(match.group("key")):
        return False
    value = match.group("qval") if match.group("qval") is not None else match.group("val")
    if not value:
        return False
    if _PLACEHOLDER_FULL_RE.match(value) or _ENV_REFERENCE_RE.match(value):
        return False
    if value.casefold() in _NON_SECRET_VALUES:
        return False
    # small numbers are settings (`AUTH_TIMEOUT=30`); long ones are account
    # numbers / PINs / IDs and do get removed
    if _NUMBER_RE.match(value) and len(value) <= 8:
        return False
    return True


def _auth_token_ok(match: re.Match) -> bool:
    """A bare ``Bearer <token>`` needs a token that looks like one.

    Keeps prose such as "Bearer authentication" out: a real opaque token carries
    a digit, a special character, or is long mixed case.
    """
    token = match.group(3)
    if len(token) < 12 or not re.search(r"[A-Za-z]", token):
        return False
    if re.search(r"\d", token) or re.search(r"[._~+/\-]", token):
        return True
    return bool(re.search(r"[a-z]", token) and re.search(r"[A-Z]", token) and len(token) >= 16)


def _authorization_value_ok(match: re.Match) -> bool:
    return not _PLACEHOLDER_FULL_RE.match(match.group(3))


def _query_value_ok(match: re.Match) -> bool:
    value = match.group(2)
    return len(value) >= 6 and not _PLACEHOLDER_FULL_RE.match(value)


def _ipv4_ok(match: re.Match) -> bool:
    parts = match.group(0).split(".")
    return len(parts) == 4 and all(part.isdigit() and 0 <= int(part) <= 255 for part in parts)


def _phone_ok(match: re.Match) -> bool:
    text = match.group(0)
    if ":" in text or "://" in text:
        return False
    digits = re.sub(r"\D", "", text)
    if not 8 <= len(digits) <= 15:
        return False
    # a dotted quad is an address (or a version), never a phone
    if re.fullmatch(r"\d+(?:\.\d+)+", text):
        return False
    # 2026-11-07 and friends
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", text):
        return False
    return True


# --- patterns --------------------------------------------------------------

_PEM_COMPLETE_RE = re.compile(
    r"-----BEGIN (?:[A-Z0-9]+ )*PRIVATE KEY(?: BLOCK)?-----"
    r".*?"
    r"-----END (?:[A-Z0-9]+ )*PRIVATE KEY(?: BLOCK)?-----",
    re.S,
)
#: A BEGIN without an END (truncated tool output). Runs after the complete rule.
_PEM_DANGLING_RE = re.compile(
    r"-----BEGIN (?:[A-Z0-9]+ )*PRIVATE KEY(?: BLOCK)?-----[\s\S]*\Z"
)

_URL_CREDS_RE = re.compile(
    r"(?i)\b([a-z][a-z0-9+.\-]{1,15}://)([^\s/?#:@]{0,128}):([^\s/?#]+)@"
)

_URL_QUERY_SECRET_RE = re.compile(
    r"(?i)([?&](?:access_token|api_key|apikey|auth|key|password|secret|sig|signature|token)=)"
    r"([^&\s#]+)"
)

_JWT_RE = re.compile(
    r"(?<![A-Za-z0-9_\-])eyJ[A-Za-z0-9_\-]{4,}(?:\.[A-Za-z0-9_\-]{4,}){2,4}(?![A-Za-z0-9_\-])"
)

_API_KEY_RE = re.compile(
    r"""(?x)
    (?<![A-Za-z0-9_\-])
    (?:
        sk-(?:ant-|proj-)?[A-Za-z0-9_\-]{8,}
      | (?:sk|rk|pk)_(?:live|test)_[A-Za-z0-9]{8,}
      | whsec_[A-Za-z0-9]{8,}
      | (?:ghp|gho|ghu|ghs|ghr)_[A-Za-z0-9]{16,}
      | github_pat_[A-Za-z0-9_]{16,}
      | (?:xoxb|xoxp|xoxa|xoxr|xoxs)-[A-Za-z0-9\-]{8,}
      | (?:AKIA|ASIA|AIDA|AROA|AIPA|ANPA|ANVA)[A-Z0-9]{12,20}
      | AIza[0-9A-Za-z_\-]{20,}
      | pypi-[A-Za-z0-9_\-]{16,}
      | glpat-[A-Za-z0-9_\-]{16,}
      | dckr_pat_[A-Za-z0-9_\-]{16,}
      | hf_[A-Za-z0-9]{20,}
    )
    (?![A-Za-z0-9_\-])
    """
)

#: Bare scheme + token. The authorization-header rule below handles the rest.
_AUTH_SCHEME_RE = re.compile(
    r"(?i)(?<![\w\-])(Bearer|Basic|Token|Digest)(\s+)([A-Za-z0-9\-._~+/]{8,}={0,2})(?![\w\-])"
)

_AUTHORIZATION_RE = re.compile(
    r"(?i)(?<![\w\-])(Authorization\s*[:=]\s*)([\"']?)([A-Za-z0-9\-._~+/]{8,}={0,2})([\"']?)"
)

#: Env assignments whose *name* is sensitive. Values are kept only when they are
#: clearly not secrets (booleans, small numbers, ``$REFERENCES``, placeholders).
_ENV_ASSIGN_RE = re.compile(
    r"""(?imx)
    (?P<lead>(?:^|(?<=[\s;&|(\"']))(?:export\s+|env\s+)?)
    (?P<key>[A-Za-z][A-Za-z0-9_]{1,48})
    (?P<sep>[ \t]*=[ \t]*)
    (?:(?P<q>["'])(?P<qval>[^"'\r\n]*)(?P=q)|(?P<val>[^\s"'\r\n]+))
    """
)

_SENSITIVE_KEY_RE = re.compile(
    r"""(?ix)
    (?:^|_)
    (?:
        API_?KEY
      | (?:ACCESS|SECRET|PRIVATE|PUBLIC|ENCRYPTION|SIGNING|MASTER)_?KEY
      | SECRET | TOKEN | PASSWORD | PASSWD | PASS | PWD
      | CREDENTIALS? | AUTH | AUTHORIZATION | BEARER | COOKIE | SESSION
      | SALT | SIGNATURE | DSN | DATABASE_URL | CONNECTION_STRING
      | KEY
    )
    (?:$|_)
    """
)

#: User paths: keep the structural prefix, replace only the account segment.
_WIN_USER_PATH_RE = re.compile(
    r"(?i)(\b[A-Z]:[\\/]{1,2}Users[\\/]{1,2})([^\\/\s\"'<>|;\[]+)"
)
_POSIX_USER_PATH_RE = re.compile(
    r"(?<![\w.\-])(/(?:home|Users)/)([^/\s\"'<>|;\[]+)"
)
#: Git-bash / MSYS spelling: /c/Users/name
_MSYS_USER_PATH_RE = re.compile(
    r"(?i)(?<![\w.\-])(/[a-z]/Users/)([^/\s\"'<>|;\[]+)"
)
#: UNC: \\host\Users\name
_UNC_USER_PATH_RE = re.compile(
    r"(?i)(\\\\[^\\/\s\"'<>|;]+\\Users\\)([^\\/\s\"'<>|;\[]+)"
)

_EMAIL_RE = re.compile(
    r"(?<![\w.%+\-])"
    r"([A-Za-z0-9._%+\-]{1,64})"
    r"@((?:[A-Za-z0-9](?:[A-Za-z0-9\-]{0,61}[A-Za-z0-9])?\.)+[A-Za-z]{2,24})"
    r"(?![\w\-])"
)

_IPV4_RE = re.compile(r"(?<![\w.])(?:\d{1,3}\.){3}\d{1,3}(?!\w)(?!\.\d)")

_PHONE_RES = (
    # +49 151 23456789 / +1 (415) 555-0132 / +44 20 7946 0958
    re.compile(r"(?<![\w+])\+\d{1,3}(?:[\s.\-]?\(?\d{1,4}\)?){2,5}(?!\w)"),
    # (415) 555-0132
    re.compile(r"(?<![\w.])(?:\(\d{2,4}\)[\s.\-]?\d{3,4}[\s.\-]\d{3,4})(?!\w)"),
    # 415-555-0132 / 415.555.0132
    re.compile(r"(?<![\w.])[2-9]\d{2}[\-.]\d{3}[\-.]\d{4}(?![\w.])"),
    # 0151 23456789 / 030/123456 / 020 7946 0958
    re.compile(r"(?<![\w.])0\d{1,4}[\s\-/]\d{3,4}(?:[\s\-/]?\d{2,6})?(?![\w.])"),
)


@dataclass(frozen=True)
class Rule:
    """One redaction rule.

    ``cls`` is the placeholder family; ``render`` receives the match and the
    already-numbered placeholder (or ``[user]``); ``validate`` can veto a
    candidate match (octets, digit counts, placeholders, ``$REFERENCES``).
    """

    name: str
    cls: str
    pattern: re.Pattern
    render: Callable[[re.Match, str], str] = _whole
    validate: Callable[[re.Match], bool] | None = None
    protected: bool = False


#: Priority order. Index = priority: the first rule to claim a span owns it.
#: A user word rule (one per pass, built from the word list) is appended last by
#: :class:`Redactor` — a short client name must never eat a longer secret.
RULES: tuple[Rule, ...] = (
    # Existing placeholders are claimed first so the pass is idempotent.
    Rule("placeholder", "", PLACEHOLDER_TOKEN_RE, protected=True),
    Rule("private_key", "secret", _PEM_COMPLETE_RE),
    Rule("private_key_open", "secret", _PEM_DANGLING_RE),
    Rule("url_creds", "secret", _URL_CREDS_RE, render=_keep_group(1, tail="@")),
    Rule("url_query_secret", "secret", _URL_QUERY_SECRET_RE, render=_keep_group(1),
         validate=_query_value_ok),
    Rule("jwt", "secret", _JWT_RE),
    Rule("api_key", "secret", _API_KEY_RE),
    Rule("auth_scheme", "secret", _AUTH_SCHEME_RE, render=_keep_group(1, tail=" "),
         validate=_auth_token_ok),
    Rule("authorization", "secret", _AUTHORIZATION_RE, render=_render_authorization,
         validate=_authorization_value_ok),
    Rule("env_secret", "secret", _ENV_ASSIGN_RE, render=_render_env, validate=_env_is_secret),
    Rule("win_user_path", "user", _WIN_USER_PATH_RE, render=_keep_group(1)),
    Rule("posix_user_path", "user", _POSIX_USER_PATH_RE, render=_keep_group(1)),
    Rule("msys_user_path", "user", _MSYS_USER_PATH_RE, render=_keep_group(1)),
    Rule("unc_user_path", "user", _UNC_USER_PATH_RE, render=_keep_group(1)),
    Rule("email", "email", _EMAIL_RE),
    Rule("ipv4", "ip", _IPV4_RE, validate=_ipv4_ok),
    Rule("phone", "phone", _PHONE_RES[0], validate=_phone_ok),
    Rule("phone", "phone", _PHONE_RES[1], validate=_phone_ok),
    Rule("phone", "phone", _PHONE_RES[2], validate=_phone_ok),
    Rule("phone", "phone", _PHONE_RES[3], validate=_phone_ok),
)

_RULE_NAMES = tuple(dict.fromkeys([rule.name for rule in RULES if not rule.protected] + ["word"]))

#: Rules re-scanned after the pass to prove nothing structured was left behind.
#: Paths are excluded because their kept prefix is meant to survive, and words
#: because a benign entry ("Users") can legitimately appear in a kept prefix.
_RESIDUAL_RULES = tuple(
    rule.name for rule in RULES
    if not rule.protected
    and rule.name not in {"win_user_path", "posix_user_path", "msys_user_path", "unc_user_path"}
)


def _word_pattern(words: Iterable[str]) -> re.Pattern | None:
    """One alternation, longest first, so "Acme Industries" beats "Acme"."""
    if not words:
        return None
    parts = [re.escape(word) for word in words]
    return re.compile(r"(?<!\w)(?:" + "|".join(parts) + r")(?!\w)", re.I)


def load_words(path: str | Path | None) -> list[str]:
    """Read a word list: one entry per line, ``#`` comments, blanks ignored.

    Returns a deterministic order (longest first, then case-insensitive
    alphabetical) so the compiled alternation — and therefore the output — does
    not depend on hash or file order.
    """
    if not path:
        return []
    text = Path(path).expanduser().read_text(encoding="utf-8-sig")
    seen: dict[str, str] = {}
    for line in text.splitlines():
        word = line.strip()
        if not word or word.startswith("#"):
            continue
        seen.setdefault(word.casefold(), word)
    return sorted(seen.values(), key=lambda w: (-len(w), w.casefold(), w))


# ---------------------------------------------------------------------------
# the pass
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class _Span:
    start: int
    end: int
    rule: Rule
    match: re.Match


class Redactor:
    """Stateful redaction pass over one document.

    Counters are continuous across the whole walk — that is what makes the
    numbering document-ordered rather than field-ordered.
    """

    def __init__(self, words: Iterable[str] | str | Path | None = None):
        if isinstance(words, (str, Path)):
            self.words = load_words(words)
        else:
            self.words = [word for word in (words or []) if isinstance(word, str) and word]
        self.classes: dict[str, int] = {}
        self.rules: dict[str, int] = {}
        self.fields: dict[str, dict[str, int]] = {}
        word_pattern = _word_pattern(self.words)
        self._rules = RULES + ((Rule("word", "word", word_pattern),) if word_pattern else ())

    # -- single string ------------------------------------------------------

    def _claim(self, text: str) -> list[_Span]:
        """Claim spans in rule-priority order; a claimed span is untouchable."""
        spans: list[_Span] = []
        starts: list[int] = []
        for rule in self._rules:
            for match in rule.pattern.finditer(text):
                start, end = match.span()
                if end <= start:
                    continue
                if rule.validate is not None and not rule.validate(match):
                    continue
                index = bisect_left(starts, start)
                if index > 0 and spans[index - 1].end > start:
                    continue
                if index < len(spans) and spans[index].start < end:
                    continue
                spans.insert(index, _Span(start, end, rule, match))
                starts.insert(index, start)
        return spans

    def _next_placeholder(self, cls: str) -> str:
        if cls == "user":
            return "[user]"
        return "[%s-%d]" % (cls, self.classes.get(cls, 0) + 1)

    def redact_text(self, text: str, field: str = "$") -> str:
        """Redact one string. ``field`` is the report path this text belongs to."""
        if not isinstance(text, str) or not text:
            return text
        spans = self._claim(text)
        if not spans:
            return text

        pieces: list[str] = []
        cursor = 0
        for span in spans:
            pieces.append(text[cursor:span.start])
            if span.rule.protected:
                pieces.append(span.match.group(0))
            else:
                placeholder = self._next_placeholder(span.rule.cls)
                pieces.append(span.rule.render(span.match, placeholder))
                self._count(span.rule, field)
            cursor = span.end
        pieces.append(text[cursor:])
        return "".join(pieces)

    def _count(self, rule: Rule, field: str) -> None:
        cls = rule.cls
        self.classes[cls] = self.classes.get(cls, 0) + 1
        self.rules[rule.name] = self.rules.get(rule.name, 0) + 1
        bucket = self.fields.setdefault(field, {})
        bucket[cls] = bucket.get(cls, 0) + 1

    # -- whole document -----------------------------------------------------

    def redact(self, node: Any, path: str = "$") -> Any:
        """Deep-copy ``node`` with every string (keys included) redacted."""
        if isinstance(node, str):
            return self.redact_text(node, field=path)
        if isinstance(node, dict):
            out: dict[Any, Any] = {}
            for key, value in node.items():
                new_key = self.redact_text(key, field=path) if isinstance(key, str) else key
                if new_key in out:
                    # Two distinct originals can only collide through a shared
                    # placeholder family; keep both values, deterministically.
                    suffix = 2
                    while "%s#%d" % (new_key, suffix) in out:
                        suffix += 1
                    new_key = "%s#%d" % (new_key, suffix)
                out[new_key] = self.redact(value, _join(path, new_key))
            return out
        if isinstance(node, (list, tuple)):
            return [self.redact(value, "%s[%d]" % (path, index)) for index, value in enumerate(node)]
        return node

    def report(self) -> dict:
        return {
            "version": REPORT_VERSION,
            "generated_at": None,
            "total": sum(self.classes.values()),
            "classes": {cls: self.classes[cls] for cls in CLASSES if self.classes.get(cls)},
            "rules": {name: self.rules[name] for name in _RULE_NAMES if self.rules.get(name)},
            "fields": {field: dict(counts) for field, counts in self.fields.items()},
            "words": len(self.words),
            "residual": {},
        }

    def residual(self, payload: Any) -> dict[str, int]:
        """Re-scan a redacted payload; anything structured left is a leak."""
        counts: dict[str, int] = {}
        rules = [rule for rule in self._rules if rule.name in _RESIDUAL_RULES]
        for text in _iter_strings(payload):
            for rule in rules:
                for match in rule.pattern.finditer(text):
                    if rule.validate is not None and not rule.validate(match):
                        continue
                    counts[rule.name] = counts.get(rule.name, 0) + 1
        return {name: counts[name] for name in _RULE_NAMES if counts.get(name)}


# ---------------------------------------------------------------------------
# public helpers
# ---------------------------------------------------------------------------

def _join(path: str, key: str) -> str:
    if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_\-]*", key):
        return "%s.%s" % (path, key)
    return "%s[%r]" % (path, key)


def _iter_strings(node: Any) -> Iterator[str]:
    if isinstance(node, str):
        yield node
    elif isinstance(node, dict):
        for key, value in node.items():
            if isinstance(key, str):
                yield key
            yield from _iter_strings(value)
    elif isinstance(node, (list, tuple)):
        for value in node:
            yield from _iter_strings(value)


def redact_manifest(manifest: Any, words: Iterable[str] | str | Path | None = None) -> tuple[Any, dict]:
    """Redact any JSON-shaped payload; returns ``(redacted, report)``."""
    redactor = Redactor(words)
    redacted = redactor.redact(manifest)
    report = redactor.report()
    report["residual"] = redactor.residual(redacted)
    return redacted, report


def redact_payload(payload: Any, words: Iterable[str] | str | Path | None = None) -> tuple[Any, dict]:
    """Alias for :func:`redact_manifest` — rollups are payloads too."""
    return redact_manifest(payload, words)


#: Kept for the v1.2 seed's call sites (CLI, tools).
redact_json_text = redact_payload
_load_words = load_words


def redact_text(text: str, words: Iterable[str] | str | Path | None = None) -> tuple[str, dict]:
    """Redact one string with a fresh numbering; returns ``(text, report)``."""
    redactor = Redactor(words)
    redacted = redactor.redact_text(text)
    report = redactor.report()
    report["residual"] = redactor.residual(redacted)
    return redacted, report


def canonical_json(payload: Any) -> str:
    """Deterministic JSON for comparison/hashing — same bytes for same input."""
    return json.dumps(payload, indent=2, ensure_ascii=False, default=str)


def count_placeholders(payload: Any) -> dict[str, int]:
    """Count placeholder tokens in a redacted payload, per class."""
    counts: dict[str, int] = {}
    for text in _iter_strings(payload):
        for match in PLACEHOLDER_TOKEN_RE.finditer(text):
            token = match.group(0)
            cls = "user" if token == "[user]" else token[1:token.index("-")]
            counts[cls] = counts.get(cls, 0) + 1
    return counts


def leaks(payload: Any, literals: Iterable[str]) -> list[str]:
    """Return the seeded literals that still appear in the JSON dump.

    Substring scan, so pass unique literals (a literal such as ``user`` would
    match the placeholder ``[user]`` by design).
    """
    dump = canonical_json(payload)
    return [literal for literal in literals if literal and literal in dump]
