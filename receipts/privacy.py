"""Redaction for share-safe receipts.

Deterministic, offline, manifest-producing. The point is not to hide that
something was removed — a receipt is evidence — but to remove what must not
travel and to say, on the page and in a sidecar, exactly what was removed.

Placeholder numbering is stable per class in document order, so re-running the
same receipt produces the same text.
"""
from __future__ import annotations

import re
from typing import Any

# Ordered: the most specific patterns run first so a key is not eaten by the
# generic phone rule, and a JWT is not half-eaten by the API-key rule.
RULES: list[tuple[str, re.Pattern, str]] = [
    ("private_key", re.compile(
        r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?-----END [A-Z ]*PRIVATE KEY-----", re.S), "secret"),
    ("jwt", re.compile(r"\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\b"), "secret"),
    ("bearer", re.compile(r"(?i)\b(bearer|authorization:\s*bearer)\s+[A-Za-z0-9._\-]{16,}"), "secret"),
    ("api_key", re.compile(
        r"(?i)\b(?:sk|pk|rk|fal|ghp|gho|ghs|ghu|xoxb|xoxp|xoxa|AKIA)[-_][A-Za-z0-9_\-]{12,}\b"), "secret"),
    ("url_creds", re.compile(r"(https?://)[^/\s:@]+:[^/\s@]+@"), "secret"),
    ("win_user_path", re.compile(r"(?i)\b([A-Z]:\\Users\\)([^\\\s\"'<>|]+)"), "path"),
    ("posix_user_path", re.compile(r"(?<!\w)(/(?:home|Users)/)([^/\s\"'<>|]+)"), "path"),
    ("email", re.compile(r"\b[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}\b"), "email"),
    ("phone", re.compile(r"(?<![\w.])(?:\+\d{1,3}[\s.-]?)?(?:\(\d{1,4}\)[\s.-]?)?\d{2,4}[\s.-]\d{2,4}[\s.-]?\d{2,6}(?![\w.])"), "phone"),
    ("ipv4", re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b"), "ip"),
    ("env_assign", re.compile(r"(?m)^([A-Z][A-Z0-9_]{2,})=(.+)$"), "secret"),
]

CLASS_LABEL = {
    "secret": "secret", "path": "path", "email": "email",
    "phone": "phone", "ip": "ip", "word": "word",
}

_WORD_RE_CACHE: dict[str, re.Pattern] = {}


def _load_words(path: str | None) -> list[str]:
    if not path:
        return []
    from pathlib import Path
    words = []
    for line in Path(path).expanduser().read_text(encoding="utf-8").splitlines():
        w = line.strip()
        if w and not w.startswith("#"):
            words.append(w)
    # longest first so "Acme Corp" wins over "Acme"
    return sorted(set(words), key=len, reverse=True)


def _word_re(word: str) -> re.Pattern:
    if word not in _WORD_RE_CACHE:
        _WORD_RE_CACHE[word] = re.compile(r"(?<!\w)" + re.escape(word) + r"(?!\w)", re.I)
    return _WORD_RE_CACHE[word]


def redact_text(text: str, counters: dict[str, int], words: list[str] | None = None) -> tuple[str, dict[str, int]]:
    """Redact one string. `counters` carries running per-class counts so numbering
    is continuous across the whole document."""
    hits: dict[str, int] = {}
    if not isinstance(text, str) or not text:
        return text, hits

    def bump(cls: str) -> str:
        counters[cls] = counters.get(cls, 0) + 1
        hits[cls] = hits.get(cls, 0) + 1
        return "[%s-%d]" % (CLASS_LABEL.get(cls, cls), counters[cls])

    for _name, pattern, cls in RULES:
        def _sub(m: re.Match, cls=cls) -> str:
            placeholder = bump(cls)
            # keep the structural prefix so the text stays readable
            if cls == "path" and m.lastindex and m.lastindex >= 2:
                return m.group(1) + "[user]"
            if _name == "url_creds":
                return m.group(1) + placeholder + "@"
            if _name == "bearer":
                return m.group(1) + " " + placeholder
            if _name == "env_assign":
                return m.group(1) + "=" + placeholder
            return placeholder

        text = pattern.sub(_sub, text)

    for w in words or []:
        text = _word_re(w).sub(lambda m: bump("word"), text)

    return text, hits


def redact_manifest(manifest: dict, words: list[str] | None = None) -> tuple[dict, dict]:
    """Deep-copy a manifest with every string redacted.

    Returns (new_manifest, report) where report carries per-class counts plus a
    per-field breakdown so the reader can see where removals happened without
    seeing the originals.
    """
    counters: dict[str, int] = {}
    per_field: dict[str, dict[str, int]] = {}

    def walk(node: Any, path: str) -> Any:
        if isinstance(node, dict):
            return {k: walk(v, f"{path}.{k}" if path else k) for k, v in node.items()}
        if isinstance(node, list):
            return [walk(v, f"{path}[{i}]") for i, v in enumerate(node)]
        if isinstance(node, str):
            new, hits = redact_text(node, counters, words)
            if hits:
                bucket = per_field.setdefault(path, {})
                for cls, n in hits.items():
                    bucket[cls] = bucket.get(cls, 0) + n
            return new
        return node

    out = walk(manifest, "")
    report = {
        "total": sum(counters.values()),
        "classes": dict(sorted(counters.items())),
        "fields": dict(sorted(per_field.items())),
        "words": len(words or []),
        "generated_at": None,  # filled by the caller so this stays testable
    }
    return out, report


def redact_json_text(payload: Any, words: list[str] | None = None) -> tuple[Any, dict]:
    """Same as redact_manifest but for arbitrary JSON-able payloads (rollups)."""
    return redact_manifest(payload, words) if isinstance(payload, dict) else (payload, {"total": 0})
