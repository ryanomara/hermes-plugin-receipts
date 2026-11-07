# Receipts

**A session flight recorder for Hermes.** One command turns any Hermes session — or a date range, or a kanban card's evidence chain — into a single self-contained HTML document: what the agent was asked, every tool call as a timed node, failures and retries exactly as recorded, per-model token spend, and the cost Hermes recorded.

A second command rolls a whole week of sessions up into a **process digest**: which
failures recur, what they cost by model and by card, which tools are slow, which
cards burn the most retries, and what moved since last week.

Nobody ships session *documents*. Logs are for the person who ran them; a receipt is something you can hand to someone else.

```
hermes receipts latest
hermes receipts rollup --since 7d
```

→ `receipts-a1b2c3d4e5f6.html` — opens offline, no server, no network, no dependencies.

---

## Why it exists

Every agent user has unshowable work. The terminal scrollback is gone, the session file is a JSONL nobody will read, and the summary you write yourself is unauditable. A receipt is the artefact you send to a client, a reviewer, or your future self:

- **What was asked** — the original prompt, verbatim, before any summarising.
- **The arc of work** — every tool call, in order, with wall-clock duration measured from dispatch to result.
- **Failures, honestly** — an errored call stays an errored call. Retries are drawn as chains (`attempt 2 of 3`, `recovered`, or `never recovered`), never collapsed into the success that followed.
- **Spend** — tokens per model, and the cost Hermes recorded at call time.
- **A flow diagram** — the shape of the work, with failure-bearing runs marked.

## Install

```bash
hermes plugins install <owner>/hermes-plugin-receipts
```

Or from a local clone:

```bash
git clone <repo> ~/Projects/hermes-plugin-receipts
hermes plugins install ~/Projects/hermes-plugin-receipts
```

Nothing else. No API keys, no config, no pip dependencies — the plugin is standard library only and reads the store Hermes already maintains.

## Use

### CLI

```bash
hermes receipts                      # newest session
hermes receipts latest               # same
hermes receipts 2026-11-01           # every session that day
hermes receipts 2026-11-01..2026-11-03
hermes receipts t_ab12cd34           # a kanban card's evidence chain
hermes receipts a1b2c3d4             # session id prefix
hermes receipts --list               # recent sessions

hermes receipts rollup --since 7d              # weekly process digest (HTML + JSON)
hermes receipts rollup --since 7d --board agency
hermes receipts rollup --since 7d --as-of 2026-11-06   # pin the window (byte-stable)

hermes receipts latest --redact --theme client # share-safe paper copy + sidecar
```

Options: `-o/--out <path>`, `--db <path>`, `--theme {instrument,client}`, `--redact`,
`--redact-words <path>`, `--json`, `--open`, `-q/--quiet`.
Rollup adds `--since` (7d, 36h, 2w, or a date), `--board <slug>`, `--as-of`, `--kanban-home`.

### As an agent tool

`receipts_build(target="latest", out="receipt.html")` — registered tool `receipts_build`, so the agent can produce a receipt when asked ("show me what you did", "make me a receipt for this session"). It takes the same theme and redaction options: `theme="instrument"|"client"`, `redact=True|False`, and `redact_words=[...]` (a list of words, or a path to a word-list file). Defaults are unchanged — instrument theme, no redaction.

### Standalone, without Hermes

```bash
python -m receipts --db path/to/state.db latest -o receipt.html
```

## What it reads

Read-only, from `state.db` (`$HERMES_HOME/state.db`, profile-aware):

| Table | Used for |
|---|---|
| `sessions` | title, source, model, cwd, counters, recorded cost |
| `messages` | the ask, every tool dispatch and result, timestamps |
| `session_model_usage` | per-model tokens and recorded cost |

Rollup mode also reads the kanban boards under `<hermes home>/kanban/boards/<slug>/kanban.db`
(card id, title, status, assignee) to attribute sessions to cards. Board databases are
opened read-only too; no board is ever written.

The database is opened with `mode=ro` and `PRAGMA query_only`. If the store refuses a read-only attachment (a WAL file held on a locked filesystem), Receipts snapshots the file and its `-wal`/`-shm` siblings to scratch and reads the copy — it never opens the live store for writing.

## Honesty rules

These are product rules, enforced by tests in `tests/test_receipts.py`:

1. **A failure is never laundered.** Every attempted call appears; an error stays an error.
2. **Unverifiable is not success.** A tool result with no structured status is reported as `unverified`, not assumed fine.
3. **Retries are real chains.** A retry must look like the same intent — same tool *and* comparable arguments. A failed `git push` followed by `git status` is not a retry, and the test suite asserts that.
4. **Unresolved failures are named.** A banner lists the calls that failed and were never retried, by index and command.
5. **Cost is recorded, not recomputed.** Receipts reports the figures Hermes wrote at call time and shows its own arithmetic against the session row — delta included, never smoothed.
6. **Provenance is on the page.** Store path, size, write time, session id, schema version, render time.
7. **Secrets are masked at build time.** Credential-shaped strings are replaced before rendering; `--redact` goes further for sharing (identity data, word list).

## Privacy — read before you share

A receipt contains the session's actual content: your prompts, the agent's replies, tool arguments, and command output. Secrets are masked (rule 7), but that masking is pattern-based — it is a safety net, not a guarantee.

- **Review before sharing.** Open the receipt and search it for names, file paths, customer data or anything you would not publish.
- **The plugin never transmits anything.** There is no network code in it; the artifact is written to disk and opened locally.
- **`--json` is the machine-readable form**; the HTML is written for a human reader, so it carries the full text.

If a receipt is going to a client, a reviewer or a ticket, write a share-safe copy instead of sending the original. `--redact` is the pass; the next section is exactly what it does and does not cover.

## Sharing a receipt

A receipt is evidence, so `--redact` does not hide that something was removed — it removes what must not travel and leaves a numbered placeholder in its place. Write a copy whenever the receipt leaves your machine: a client, a reviewer, a public post, an attached-but-visible ticket.

```bash
hermes receipts latest --redact --theme client
# → receipts-<session>.redacted.html + receipts-<session>.redacted.redaction.json
```

**The default output is a new file.** With no `-o`, the name gains `.redacted` before the extension, so a redacted run cannot land on the plain receipt of the same session. With `-o foo.html`, that path is the copy and the sidecar is `foo.redaction.json` — point it somewhere new.

### What each class covers

| placeholder | what it removes |
|---|---|
| `[secret-N]` | private-key blocks (PEM), JWTs, authorization values (`Bearer`, `Basic` and the auth header), provider API-key shapes (OpenAI/Anthropic, Stripe, GitHub, Slack, AWS, Google, PyPI, GitLab, Docker, Hugging Face), credentials inside URLs, secrets in `?token=` / `&key=` query parameters, and sensitive `KEY=value` assignments |
| `[email-N]` | e-mail addresses |
| `[phone-N]` | phone numbers — 8 to 15 digits, international and common local formats |
| `[ip-N]` | IPv4 addresses |
| `[user]` | the account segment of a user path; the prefix is kept, so `C:\Users\ryoma\x` becomes `C:\Users\[user]\x` — the same for `/home/…`, `/c/Users/…` and UNC paths |
| `[word-N]` | entries from your word list, case-insensitive and whole-word |

Each secret, e-mail, phone, IP and word occurrence gets its own number; `[user]` is the same token at every account segment. Equal values are not linked to one another. Numbering is deterministic — no clock, hash or set order reaches it — so the same session and the same word list produce the same placeholders, whatever order the words were given in.

`--redact-words FILE` adds what no pattern can know: client names, project code names, a bare account handle (`whoami`, the owner column of `ls -l`). One entry per line, blank lines and `#` comments ignored; matching is case-insensitive and whole-word, and the longest entry wins (`Acme Industries` before `Acme`). The option requires `--redact`, and `rollup --redact` is refused rather than quietly emitting an unredacted digest.

### What the document already masks

The receipt masks credential-shaped strings **when the manifest is built**, before any redaction: PEM private-key blocks, `sk-` keys, GitHub tokens, AWS access-key ids, Slack tokens, and 12+-character values assigned to names such as `api_key`, `access_token`, `secret`, `password` or `bearer` become `«redacted:…»` on the page. A document that never contained your OpenAI key does not "lose" it to `--redact`.

`--redact` is the second layer: identity data (e-mails, phones, IPs, account paths), your word list, and the credential shapes the build-time masker does not know — Stripe `whsec_` secrets, passwords inside URLs, query-string tokens, JWTs, and `TOKEN=…` environment lines.

### The sidecar

`receipts-<session>.redacted.redaction.json` describes the copy without revealing it:

```jsonc
{
  "version": 1,
  "generated_at": "…",          // the receipt's own timestamp, not a second clock reading
  "total": 51,                  // == sum(classes) == sum(rules)
  "classes": {"secret": 22, "email": 4, "phone": 4, "ip": 4, "user": 13, "word": 4},
  "rules": {"url_creds": 4, "url_query_secret": 8, "jwt": 4, "api_key": 2, "env_secret": 4,
            "win_user_path": 5, "posix_user_path": 4, "msys_user_path": 4, "email": 4,
            "ipv4": 4, "phone": 4, "word": 4},
  "fields": {"$.sessions[0].timeline[1].payload.result_excerpt": {"secret": 3}},
  // fields has one entry per manifest path that lost something; abridged here
  "words": 1,                   // entries loaded from the word list
  "residual": {}                // post-pass re-scan; {} means nothing structured was left
}
```

(The numbers above are the 1.2 committed sample, not an illustration.)

Two things worth knowing before you quote those numbers:

- **`total` counts manifest fields, not placeholders you can count on the page.** The manifest stores each call twice (timeline event and flow node) and its arguments twice (summary and detail), so one removal can print once or twice. The committed 1.2 sample removes 51 fields and prints 27 placeholders; `docs/evidence/1.2/samples-check.json` records both. `fields` is the map to audit — it names the document path each removal came from.
- **`residual` is a re-scan, not a proof.** It runs the structured rules again over the copy, excluding the path rules and the word rule (a kept path prefix may legitimately contain either). `{}` means none of those fired again.

### The caveat, plainly

Redaction is **pattern-based, not a guarantee**. A secret that matches none of the rules — stored as a bare number, split across lines, or in an unrecognised format — survives. A bare account handle and a client name survive unless they are in the word list. `residual: {}` means the pass found nothing *it knows how to look for*; it is not a promise that the copy is safe.

So the last step is still a human one: open the copy, search it for names, paths, customer data and anything you would not publish, then send it. When you want to inspect the data rather than the page, `--json --redact` prints the redacted manifest with the same report embedded and writes no files.

The committed worked example is `samples/receipt-original-instrument.html` next to `receipt-redacted-instrument.html` and `receipt-redacted-client.html` (+ sidecars), with the proofs in [`docs/evidence/1.2/`](docs/evidence/1.2/README.md).

## The artifact

Single HTML file, ~20–220 KB depending on session length:

- masthead + provenance
- six-cell instrument strip (calls, failures, retries, wall clock, tokens, cost)
- status bands for unresolved / unverified / unterminated calls
- the ask, verbatim
- timeline: every tool call, interleaved with user turns and agent replies, with duration bars
- work-flow diagram (consecutive same-tool calls grouped, failure runs marked)
- per-model spend table + reconciliation
- filters: all / failures / slow ≥5s / retries · jump to first failure

Inline CSS and ~40 lines of vanilla JS for the filters. With scripting off, the document is still complete.

`--theme client` renders the same document as a paper sheet — no scanline texture, no masthead sweep, serif headings and print rules that give a clean white page when you print it. `--redact` adds a notice band at the top naming the classes it removed; the band is styled in both themes.

## Rollup mode

```
hermes receipts rollup --since 7d [--board <slug>] [-o base/path] [--as-of <moment>]
```

Writes **two files** — `rollup-<start>_<end>.html` and `.json` — that answer, for the window:

1. **Failure taxonomy** — every failed call classified by ordered pattern rules (no model, no embedding), ranked by frequency, with first seen, last seen, the tools it hits, and how many were retried / recovered / left unresolved.
2. **Cost by model and by card** — summed from `session_model_usage`, attributed to the card each session worked on, and reconciled against the session rows and against per-session receipts. Sessions with no usage rows are counted and named; their cost is *unknown*, never zero.
3. **Slowest tools** — p50 / p95 / max / total per tool, with the percentile method stated on the page. Tools that wait on a human (`clarify`) and tools with fewer than five timed calls are excluded from the ranking, and the exclusions are listed.
4. **Retry hotspots** — cards ranked by retries, with failures, recovered chains, unresolved failures and cost.
5. **Week over week** — the same metrics against the immediately preceding window of equal length, with deltas and percentages.

**Determinism.** The window is explicit (`--as-of` pins the end), all timestamps are UTC, every ordering has a tiebreaker, money is rounded once, and nothing derives from render time. A window covers sessions that **started in it and had closed by its end**; sessions still open at the cutoff are excluded and counted (pass `--include-open` to include them, which is flagged as not reproducible). Two runs over the same closed window produce byte-identical JSON and HTML. The payload carries a SHA-256 digest of its own measurements (excluding file metadata), and `samples/rollup-demo.json` is verified to rebuild from the committed fixture in the test suite.

Card attribution is evidence-based, in this order: the worker transcript's own `# Kanban task t_xxxxxxxx` header, then inheritance from `parent_session_id` (sub-agent work belongs to the parent's card), then an exact unique title match. Anything else is reported as unattributed rather than guessed.

See [`docs/ROLLUP.md`](docs/ROLLUP.md) for the design contract and the gazette integration.

## Tests

```bash
python -m unittest discover -s tests -v
```

173 tests, standard library only. One manifest test skips if PyYAML is not installed (it is present in the Hermes venv, so the suite here runs 0 skipped). Two fixtures are generated, not committed as binaries:

```bash
python fixtures/make_demo_db.py fixtures/demo-state.db          # one session, the demo
python fixtures/make_rollup_db.py fixtures/.rollup-fixture      # two weeks + two boards
```

The first contains a fictional checkout bug with two recovered retry chains and one failure that never resolved — sanitized by construction, which is what the demo artifact is built from. The second is a two-week, two-board store whose every count, duration and dollar is asserted exactly by `tests/test_rollup.py`.

## Requirements

- Python 3.9+ (stdlib only)
- A Hermes session store with at least one session
- Nothing else (rollup mode additionally reads kanban boards when they exist)

## Limitations, stated plainly

- **Durations are wall-clock between the dispatch message and its result.** For parallel calls dispatched in one assistant message, all of them share a start time, so their durations include waiting. The artifact labels parallel groups.
- **Cost is only as good as the recorded figures.** If a store has no `session_model_usage` rows, the spend section says so rather than estimating.
- **Compaction can orphan results.** A tool result whose dispatch row was compacted away is rendered as an `orphan result` node instead of being dropped.
- **A rollup assigns each session to the window it *started* in, and only if it had closed by the window end.** A session that spans a window boundary is counted once; a session still running at the cutoff is excluded and counted separately (`--include-open` includes it, at the cost of reproducibility).
- **Card attribution is evidence, not inference.** If a worker transcript carries no card header, no parent chain and no unique title match, its cost lands in `unattributed` rather than being assigned to a plausible card.
- **Failure classes are finite.** The taxonomy is a fixed, ordered rule table; a genuinely new failure mode files under `tool_error` until a rule is added. That is deliberate — a class that changes meaning between weeks is not a measurement.
- **A p95 needs a sample.** Tools with fewer than five timed calls (and `clarify`, which waits on a human) are excluded from the slow-tool ranking and listed as excluded.
- **Byte-stability is for a closed window.** A running session is still being written; pin `--as-of` (or run after the week closes) for two runs to agree.
- **Redaction is pattern-based.** `--redact` removes what its rules and your word list can see; an unrecognised secret shape, a bare account handle or a client name not in the list survives, and `residual: {}` is not a guarantee. A manual read of the copy still applies — see [Sharing a receipt](#sharing-a-receipt).

## Layout

```
plugin.yaml                 plugin manifest (provides_tools: receipts_build)
__init__.py                 loader entry point → receipts.plugin.register
receipts/
  dbio.py                   read-only store access, resolution, provenance
  manifest.py               normalisation, failure classification, retry chains, reconciliation
  privacy.py                deterministic redaction: ordered rules, word list, report
  render.py                 the single-file HTML document (+ the shared document shell)
  rollup.py                 window analysis, taxonomy, cost attribution, deltas, digest
  render_rollup.py          the digest document
  cli.py                    `hermes receipts [rollup]` / `python -m receipts`
  plugin.py                 tool + CLI registration (`receipts_build`)
fixtures/make_demo_db.py    synthetic single-session store
fixtures/make_rollup_db.py  synthetic two-week, two-board store
tests/test_receipts.py      v1 acceptance tests
tests/test_rollup.py        v1.1 rollup acceptance tests
tests/test_privacy.py       redaction rules and determinism
tests/test_cli_redact.py    --redact / --theme CLI acceptance
tests/test_theme.py         client-theme acceptance
tests/test_plugin_tool.py   agent tool surface acceptance
samples/                    committed artifacts: receipts (before/after), rollup digest
docs/ROLLUP.md              rollup design contract + gazette integration
docs/evidence/1.2/          share-fixture generator, sample + offline checks, screenshots
```

## License

MIT
