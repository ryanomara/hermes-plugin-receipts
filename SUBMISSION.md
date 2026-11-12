# Submission packet — Receipts

This packet is complete except for the steps that require a human: creating the
public repository, pinning its SHA, opening the catalog PR, posting the demo and
filing the form. Everything else — install line, captions, beat sheet, catalog
entry, PR body — is written and verified.

---

## 1. One-line install

```bash
hermes plugins install <owner>/hermes-plugin-receipts
```

## 2. The command shown in the demo

```bash
hermes receipts latest
```

→ `receipts-<session>.html`, opens offline.

## 3. Caption copy

**Variant A — the problem first (recommended)**

> Every agent user has unshowable work.
>
> So I built Receipts: one command turns a Hermes session into a single HTML document — the ask, every tool call as a timed node, every failure *as a failure*, retries as chains, per-model spend, recorded cost.
>
> No network. No server. Send it to a person.
>
> `hermes receipts latest`
>
> @NousResearch #HermesAgent

**Variant B — the demo first**

> This is a Hermes session as a document, not a log.
>
> 18 tool calls. 4 failures. 3 retries. One failure that never resolved — named on the page, not buried.
>
> Receipts, a Hermes plugin. One command, one self-contained HTML file, opens offline.
>
> @NousResearch #HermesAgent

Both are under 280 characters when the code line is counted as 23.

## 4. Demo — as delivered (`demo/receipts-demo-v2.mp4`, 52.9 s)

2160×1214, H.264 + AAC, 2.9 MB. Real captures of the rendered artifact (actual
scroll positions, the artifact's own *jump to first failure* control) plus title,
terminal, install and value-prop cards drawn from the real CLI output. Synthetic
fixture only. Narration is six recorded lines; the music bed is an original
big-beat composition generated for this demo (seed recorded in `demo/audio/*.gen.json`).

Scene order:

1. Title card — *Receipts, a session flight recorder*
2. Terminal: `hermes receipts latest` typed
3. CLI output: path, 18 tool calls, 4 failures, 3 retries, tokens, cost, 38 KB
4. Artifact opens: title, six-cell instrument strip, **"1 failure never resolved"** band
5. Scroll to the ask, verbatim
6. Timeline: parallel reads, then `#02 terminal` **FAILED / RETRIED ×2 / EXIT 1** in red
7. Retry chain through to the unrecovered `git push` failure
8. *Jump to first failure* → node highlighted, error panel with the 403
9. Spend table per model, reconciliation **Δ0 ✓**
10. Install line
11. **Value prop** — *Showable work · Honest failures · Recorded cost*
12. End card — *every agent user has unshowable work*

If a longer cut is wanted for the typeform, the raw captures in `demo/frames/`
regenerate it via `python demo/build_demo_v2.py`; raising the hold durations in
`seq` past ~90 s is the only thing that would breach the cap.

### 1.2 beat — before/after redaction (to insert in the next cut)

**Beat 13, before the value prop.** Split screen: `receipts-<session>.html`
(instrument, unredacted) beside `receipts-<session>.redacted.html`
(`--redact --theme client`). The same call list on both sides; the client name
and contact details on the right are `[word-1]` / `[email-1]` / `[phone-1]`,
the redaction band names every class it removed, and the `.redaction.json`
sidecar sits open alongside. Hold four seconds.

> Same work. Nothing that should not travel.

The delivered v2 cut predates 1.2 and does not contain this beat — it is listed
here so a re-record from `demo/frames/` includes it. The two frames are committed:
`samples/receipt-original-instrument.html` and
`samples/receipt-redacted-client.html` (+ `.redaction.json`).


## 5. Catalog PR

**Entry** — to be added as `plugin-catalog/receipts.yaml` in
`NousResearch/hermes-agent`:

```yaml
name: receipts
repo: <PUBLIC_REPO_URL>
sha: <40-HEX-SHA-OF-TAGGED-COMMIT>
title: Receipts
description: "Session flight recorder + weekly process digest. Turns a Hermes session, a date range, or a
  kanban card's evidence chain into one self-contained, offline HTML receipt: the original ask, every tool
  call as a timed node, failures and retries exactly as recorded, per-model token spend, and the recorded
  cost. `--redact` writes a share-safe copy with secrets, e-mails, phone numbers, IP addresses and account
  paths removed, plus a JSON sidecar naming exactly what was removed; `--theme client` renders the same
  receipt as a paper document for a client. `receipts rollup --since 7d` aggregates a window into a
  deterministic weekly digest: failure taxonomy, cost by model and card, slowest tools by p50/p95, retry
  hotspots, and week-over-week deltas. Reads the local session store and kanban boards only — no network,
  no credentials, no new dependencies."
maintainer: Ryan O'Mara
tier: community
category: observability
version: "1.2.0"
docs_url: <PUBLIC_REPO_URL>#readme
platforms: []
capabilities:
  provides_tools:
    - receipts_build
  provides_hooks: []
  provides_middleware: []
  requires_env: []
```

**PR body:**

```markdown
## Receipts — session flight recorder

Adds `receipts` to the plugin catalog.

**What it does.** `hermes receipts <session|date|card>` renders the local session
store into a single self-contained HTML document: the original ask, every tool
call as a timed node, failures and retries as recorded (never collapsed into the
success that followed), per-model token spend, and the cost Hermes recorded, with
a reconciliation delta. `--redact` writes a share-safe copy with a JSON sidecar
naming exactly what was removed; `--theme client` renders the same document as a
paper sheet for a client.

**Why it belongs in the catalog.** It is read-only against `state.db`, stdlib-only,
and useful to anyone who has to show what an agent did — the artefact is portable
and opens offline. `receipts rollup` extends the same store into a weekly process
digest: deterministic failure taxonomy, cost by model and card, slow tools,
retry hotspots, and week-over-week deltas.

**Admission checks.**
- `hermes plugins validate .` — passes every gate (manifest, capability probe,
  no tool collisions, security scan, no core override).
- No self-updater, no network calls, no credentials.
- Declared capabilities match registrations: `provides_tools: [receipts_build]`.
- 173 stdlib tests: `python -m unittest discover -s tests`.

Pinned at `<SHA>`.
```

## 6. Steps that need a human

- [ ] Create the public repo and push this tree (a catalog entry needs a public
      40-hex SHA pin; nothing here can supply that offline).
- [ ] Fill `repo` + `sha` above, open the PR against `NousResearch/hermes-agent`.
- [ ] Approve the demo file in `demo/` (or re-record from the scene order above).
- [ ] Post on X with one of the captions, tagging `@NousResearch`.
- [ ] File the typeform.

## 7. Blockers found (not guessed)

1. **No `gh` CLI on this machine** (`gh: command not found`). The GitHub API is
   reachable with the stored credential, so the PR can be opened via API once the
   public repo exists — `gh` is convenient, not required.
2. **No public repo yet**, so the mandatory 40-hex SHA pin for
   `plugin-catalog/receipts.yaml` cannot exist. The entry above is complete
   except for `repo` and `sha`.
3. **The hackathon typeform URL is not derivable from local state.** I did not
   invent one.

## 8. Verified state at handoff

- `python -m unittest discover -s tests` → 30 tests, OK (the v1.0.0 count; current count in §10).
- `hermes plugins validate .` → *Validation passed* (all gates).
- `HERMES_HOME=<scratch> hermes receipts latest --db <fixture>` → artifact, 39 KB.
- Real session read-only render → 80 tool calls, 5 failures, 210 KB.
- Artifact: 0 external references, 0 console errors, filters functional.
- Live gateway plugin directory untouched; all install tests ran in a scratch
  `HERMES_HOME`.

## 9. v1.1 — rollup mode (2026-11-06)

`receipts rollup --since 7d [--board <slug>]` writes one HTML + one JSON digest:
a deterministic failure taxonomy, cost by model and card reconciled to receipts,
slowest tools by p50/p95, retry hotspots, and week-over-week deltas.

Verified:

- `python -m unittest discover -s tests` → **79 tests, OK at v1.1.0** (1 skipped: PyYAML is
  absent from the bare interpreter; the test now skips as its docstring promised; current
  count in §10).
- `hermes plugins validate .` → *Validation passed* (all gates).
- **Byte-stability:** two runs over the pinned fixture window produce identical
  JSON and HTML; `samples/rollup-demo.json` is rebuilt from the fixture inside
  the test suite, so the committed sample is a checked claim.
- **Real store, pinned window** (`--as-of 2026-11-06T01:00:00`): 288 sessions,
  32,359 tool calls, 1,156 failures, 818 retries, $65.7338,
  digest `sha256:db367be7677467c4f071139b8eba1d9b9fea26b26b4f048942fca07f8e053a8e`.
- **Cron** `b2f1a5960d36` — `30 6 * * 1`, `--no-agent` script — ran through the
  live scheduler on 2026-11-06 01:04 (`hermes cron runs` → *completed*), and
  appended its line to `lifehack/docs/ops/receipts-rollup/run.log`.
- **Gazette:** CONTENT DESK renders the digest's pre-formatted `desk_line` from
  `lifehack/docs/ops/receipts-rollup/latest.json`. The digest script gained one
  section and no formatting logic; the screenshot is
  `docs/evidence/rollup/gazette-content-desk.png`.

Not verified here:

- A **fresh `HERMES_HOME` install** on this machine cannot bootstrap its Python
  environment: the runtime's source-update step fails copying vendor conformance
  files (`WinError 3`) before the plugin is reached. That is an environment
  fault, not a plugin fault — `hermes plugins validate .` is green, the plugin
  CLI parser path is covered by a test that parses `rollup …` through
  `receipts.plugin._setup_cli`, and the live profile was deliberately left
  unmodified (Receipts is not installed there).

## 10. v1.2 — share-safe redaction + client theme (2026-11-07)

Two additions, both offline and stdlib-only: a receipt can be turned into a
share-safe copy, and it can read as a paper document rather than a console.

- **Agent tool.** `receipts_build` takes `target`, `out`, `theme`
  (`instrument`|`client`), `redact` (bool) and `redact_words` (a list of words or
  a word-list path). Defaults are the v1.1.0 behaviour; the schema advertises all
  five properties.
- **CLI.** `--redact` writes a *copy* (the default name gains `.redacted`) plus a
  `<stem>.redaction.json` sidecar; `--redact-words FILE` extends the word list and
  requires `--redact`; `--theme client` renders the paper document.
  `rollup --redact` exits 1 rather than emitting an unredacted digest.
- **Docs.** README's "Sharing a receipt" states what each placeholder class
  covers, what the sidecar contains, and the pattern-matching caveat; the catalog
  entry above mirrors `plugin.yaml` at 1.2.0.

Verified now:

- `python -m unittest discover -s tests` → **173 tests, OK**, 0 skipped, ~2.8 s.
- `hermes plugins validate .` → *Validation passed* (security scan: safe).
- Samples offline: `samples/receipt-{original-instrument,redacted-instrument,redacted-client}.html`
  load with 0 console messages and 0 external references; the redacted pair carries
  the band in both themes. `docs/evidence/1.2/samples-check.json` records 51 manifest
  fields removed, 27 visible placeholders, `residual {}`, and that the CLI reproduces
  the committed client sample byte for byte (clock readings masked).
- **Release marker:** the annotated tag `v1.2.0` on this commit — the catalogue
  SHA is `git rev-parse v1.2.0^{commit}`.
- **Still owed:** the public push, which needs the public repo and its remote;
  nothing in this tree can supply either (§6, §7).

## Review round 1 (2026-11-12, PR #152953)

Teknium requested changes at c7df9d35: four tool-boundary blockers. All fixed
in v1.2.1 (sha 54088cdea3c808dda31406c18690ec27b7b882bc), catalog re-pinned,
181 tests green. Fixes: out-path confinement into plugin-data/session-receipts
+ file-safety denial; redact_words tool form is list-only (file form CLI-only);
hermes_home() resolves via hermes_constants first (env fallback standalone
only, tool ignores RECEIPTS_DB); demo/gen_audio.py no longer reads the Hermes
.env. See CHANGELOG 1.2.1 and tests/test_tool_confinement.py.
