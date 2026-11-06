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
```

Options: `-o/--out <path>`, `--db <path>`, `--json`, `--open`, `-q/--quiet`.
Rollup adds `--since` (7d, 36h, 2w, or a date), `--board <slug>`, `--as-of`, `--kanban-home`.

### As an agent tool

`receipts_build(target="latest", out="receipt.html")` — registered tool `receipts_build`, so the agent can produce a receipt when asked ("show me what you did", "make me a receipt for this session").

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
7. **Secrets are masked.** Credential-shaped strings are redacted before rendering.

## Privacy — read before you share

A receipt contains the session's actual content: your prompts, the agent's replies, tool arguments, and command output. Secrets are masked (rule 7), but that masking is pattern-based — it is a safety net, not a guarantee.

- **Review before sharing.** Open the receipt and search it for names, file paths, customer data or anything you would not publish.
- **The plugin never transmits anything.** There is no network code in it; the artifact is written to disk and opened locally.
- **`--json` is the machine-readable form**; the HTML is written for a human reader, so it carries the full text.

If you plan to send receipts to clients or attach them to tickets, generate them from a session you are comfortable exposing, and keep a redaction pass in your workflow.

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

75 tests, standard library only. Two fixtures are generated, not committed as binaries:

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

## Layout

```
plugin.yaml                 plugin manifest (provides_tools: receipts_build)
__init__.py                 loader entry point → receipts.plugin.register
receipts/
  dbio.py                   read-only store access, resolution, provenance
  manifest.py               normalisation, failure classification, retry chains, reconciliation
  render.py                 the single-file HTML document (+ the shared document shell)
  rollup.py                 window analysis, taxonomy, cost attribution, deltas, digest
  render_rollup.py          the digest document
  cli.py                    `hermes receipts [rollup]` / `python -m receipts`
  plugin.py                 tool + CLI registration
fixtures/make_demo_db.py    synthetic single-session store
fixtures/make_rollup_db.py  synthetic two-week, two-board store
tests/test_receipts.py      v1 acceptance tests
tests/test_rollup.py        v1.1 acceptance tests (75 total)
samples/                    committed artifacts: the receipt and the rollup digest
docs/ROLLUP.md              rollup design contract + gazette integration
```

## License

MIT
