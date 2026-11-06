# Rollup mode — design contract

`receipts rollup` answers a different question from a receipt. A receipt is what
happened in **one session**; a rollup is what keeps happening **across sessions**,
and whether it is getting better or worse.

```
hermes receipts rollup --since 7d [--board <slug>] [-o base/path] [--as-of <moment>]
```

Writes `base.html` and `base.json` (default `rollup-<start>_<end>.html/.json`).
`--json` prints the payload and writes nothing. No network; the only inputs are
`state.db` and the kanban board databases, both opened read-only.

## The five answers

| Question | Where it comes from | Definition |
|---|---|---|
| What fails, ranked? | `messages` → failed tool nodes | ordered regex rules over the recorded error text; class counts, first/last seen, tools, retried/recovered/unresolved |
| What did it cost? | `session_model_usage` | recorded USD, summed by model and attributed by card; never re-priced |
| What is slow? | dispatch → result timestamps | p50 / p95 / max / total per tool, linear interpolation at `(n-1)p` |
| Which cards burn retries? | retry chains × card attribution | retries, failures, recovered chains, unresolved, cost per card |
| What changed? | current vs previous window | same metrics over the immediately preceding window of equal length |

## Determinism contract

A digest that changes between runs without a change in the data is not a
measurement. The rollup enforces:

- **Explicit window.** `--as-of` pins the end; `--since` fixes the length. The
  output contains only window-derived timestamps, all UTC (`...Z`).
- **No render clock.** Nothing in the payload or the HTML is `now()`.
- **Total ordering.** Every list sorts on its metric with an explicit tiebreaker
  (name, id), never on insertion or filesystem order.
- **One rounding point.** Money to 6 dp, durations and percentages to 3 dp, at
  the boundary where they are emitted.
- **Content digest.** `stability.digest` is SHA-256 over the canonical payload
  (`json/sort_keys/utf-8`) **excluding** `source` (store path, size, mtime) and
  `stability` itself. Copying the store or rebuilding an identical fixture does
  not change the digest; changing a count does.
- **Sessions are assigned by start time.** A session belongs to the window it
  started in, so adjacent windows never double-count it.

`samples/rollup-demo.json` is rebuilt from `fixtures/make_rollup_db.py` in
`tests/test_rollup.py`; if the fixture or the aggregation changes, that test
fails until the sample is regenerated. The sample is a claim, not decoration.

## Failure taxonomy

Rules are applied in order to the **first 600 characters** of the recorded error
text. First match wins, and the order is part of the contract:

| # | class | matches (abridged) | refines |
|---|---|---|---|
| 1 | `tool_contract` | missing required argument, failed argument validation, the tool was not invoked, re-send without, unsupported image | — |
| 2 | `guard_refusal` | refusing to overwrite/write/delete, blocked by SSRF protection, blocked (hardline) | — |
| 3 | `timeout` | timed out, timeout after, TimeoutError, deadline exceeded | — |
| 4 | `permission` | permission denied, EACCES, EPERM | — |
| 5 | `rate_limit` | rate limit, too many requests, HTTP/status/error 429 | — |
| 6 | `auth` | authentication failed, unauthorized, HTTP/status/error 401/403, invalid api key | — |
| 7 | `disk` | no space left, disk full, ENOSPC | — |
| 8 | `network` | connection refused/reset/aborted/error/failed, network unreachable, ECONN | — |
| 9 | `environment` | ModuleNotFoundError, No module named, cannot import, wrong interpreter | — |
| 10 | `missing_binary` | command not found, is not recognized as, is not installed or not on PATH | — |
| 11 | `command_usage` | invalid argument/option, unknown option, `^usage: ` | — |
| 12 | `patch_context` | old_string not found/does not match, found N matches for old_string, escape-drift | `not_found` |
| 13 | `test_failure` | FAILURES, AssertionError, "N failed, M passed", assert … == | `nonzero_exit` |
| 14 | `not_found` | FileNotFoundError, ENOENT, no such file, cannot access, does not exist | — |
| 15 | `exception` | Traceback, JSONDecodeError, TypeError/ValueError/…, SyntaxError | — |
| 16 | `not_found` | bare "not found" / "cannot find" | — |
| 17 | `exit_nonzero` | no pattern matched, but the exit code is non-zero | `nonzero_exit` |
| 18 | `tool_error` | a structured error with text, no pattern | — |
| 19 | `unknown` | failed with no text at all | — |

Three ordering decisions earn their place:

- `tool_contract` is first because a call rejected *before it ran* is the agent's
  mistake, not the world's — and the parameter schema quoted by the error often
  contains the word `timeout`, which would otherwise mis-file it.
- `patch_context` precedes `not_found` so a stale edit context is not reported as
  a missing file.
- `not_found` (specific file patterns) precedes `exception`, but `exception`
  precedes the bare "not found" catch-all, so `FileNotFoundError` stays a missing
  file while `ValueError: substring not found` stays an exception.

`refines` records which v1 receipt class a rollup class splits off, so the digest
can be traced back to the per-session artifact. Classes are finite on purpose: a
novel failure files under `tool_error` until a rule is added, rather than being
labelled differently every week.

## Cost and attribution

Cost is the figure Hermes recorded per model row. The rollup reports:

- `usage_usd` — sum of `session_model_usage.estimated_cost_usd` for the window;
- `recorded_usd` — sum of `sessions.estimated_cost_usd`, the same comparison a
  receipt performs, across every session;
- `attributed_usd` + `unattributed_usd` — must equal `usage_usd` (checked, with
  the delta shown);
- `sessions_without_usage` — counted and named; their cost is unknown, not zero;
- `receipt_mismatches` — sessions where the per-model rows and the session row
  disagree, with the largest delta named.

Card attribution is evidence, in order:

1. **transcript** — `# Kanban task t_xxxxxxxx` in the session's first user
   message. The dispatcher writes this header, so it identifies the card the
   session worked on rather than a card that was mentioned.
2. **parent** — `parent_session_id` inheritance (sub-agent work belongs to the
   card its parent worked on; parents outside the window are resolved too).
3. **title** — an exact, unique match against the board index, for legacy
   sessions written before the header existed.

Nothing else is inferred. Sessions that resolve to no card are reported as
`unattributed`, with their sessions, calls, failures, retries and cost.

## The gazette contract

The weekly cron writes the digest into the Lifehack repo:

```
lifehack/docs/ops/receipts-rollup/<YYYY-Www>/rollup.html
lifehack/docs/ops/receipts-rollup/<YYYY-Www>/rollup.json
lifehack/docs/ops/receipts-rollup/latest.html      (copy of the newest)
lifehack/docs/ops/receipts-rollup/latest.json      (copy of the newest)
lifehack/docs/ops/receipts-rollup/run.log          (one line per run)
```

`latest.json` carries a single pre-formatted `desk_line`, for example:

```
2026-W45 · top failure exit_nonzero ×301 · retries 814 (93% of chains recovered)
· slowest tool_call p95 3m 04s · cost $66.0870 · cost +13% vs prior window
```

The gazette's CONTENT DESK section renders that string; it does no formatting and
knows nothing about the rollup's schema beyond `headline`, `desk_line` and
`window.week`. Adding an edition therefore needs no change to the digest script
beyond the one section that reads `latest.json`.

## Weekly cron

- Script: `~/.hermes/scripts/receipts_rollup_weekly.py` (thin launcher) →
  `lifehack/scripts/receipts_rollup.py` (the logic, versioned).
- Schedule: `30 6 * * 1` (Mondays 06:30, local), `--no-agent` so the run is a
  script and its stdout is the delivered summary line.
- Each run appends to `run.log`: UTC start, window, sessions, calls, failures,
  retries, cost, digest, output paths, and any error.
- The job id and its durable execution history are visible in
  `hermes cron list` / `hermes cron runs <id>`.

## Known edges

- A session that spans a window boundary is counted in the window it started in.
- A session with no usage rows understates per-model cost; the reconciliation
  names it rather than estimating.
- A tool with fewer than five timed calls cannot rank as slow; `clarify` never
  ranks (it measures a human's response time).
- `--board` keeps only sessions attributed to that board's cards. Unattributed
  sessions are excluded by definition of the filter.
- Boards are discovered from `<hermes home>/kanban/boards/*/kanban.db`; an
  unreadable board is listed in the payload with its error, not silently dropped.
