# Receipts

**A session flight recorder for Hermes.** One command turns any Hermes session — or a date range, or a kanban card's evidence chain — into a single self-contained HTML document: what the agent was asked, every tool call as a timed node, failures and retries exactly as recorded, per-model token spend, and the cost Hermes recorded.

Nobody ships session *documents*. Logs are for the person who ran them; a receipt is something you can hand to someone else.

```
hermes receipts latest
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
```

Options: `-o/--out <path>`, `--db <path>`, `--json`, `--open`, `-q/--quiet`.

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

## Tests

```bash
python -m unittest discover -s tests -v
```

30 tests, standard library only. The fixture is generated, not committed as a binary:

```bash
python fixtures/make_demo_db.py fixtures/demo-state.db
```

It contains a fictional checkout bug with two recovered retry chains and one failure that never resolved — sanitized by construction, which is what the demo artifact is built from.

## Requirements

- Python 3.9+ (stdlib only)
- A Hermes session store with at least one session
- Nothing else

## Limitations, stated plainly

- **Durations are wall-clock between the dispatch message and its result.** For parallel calls dispatched in one assistant message, all of them share a start time, so their durations include waiting. The artifact labels parallel groups.
- **Cost is only as good as the recorded figures.** If a store has no `session_model_usage` rows, the spend section says so rather than estimating.
- **Compaction can orphan results.** A tool result whose dispatch row was compacted away is rendered as an `orphan result` node instead of being dropped.
- **No cross-session aggregation beyond a date range.** A date-range receipt lists sessions and their timelines; it is not a roll-up dashboard.

## Layout

```
plugin.yaml              plugin manifest (provides_tools: receipts_build)
__init__.py              loader entry point → receipts.plugin.register
receipts/
  dbio.py                read-only store access, resolution, provenance
  manifest.py            normalisation, failure classification, retry chains, reconciliation
  render.py              the single-file HTML document
  cli.py                 `hermes receipts` / `python -m receipts`
  plugin.py              tool + CLI registration
fixtures/make_demo_db.py synthetic session generator
tests/test_receipts.py   30 acceptance tests
samples/                 generated artifact
```

## License

MIT
