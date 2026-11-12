# Changelog

All notable changes to Receipts. The plugin is stdlib-only and offline by
design; nothing below adds a network call, a credential or a dependency.

## 1.2.1 — 2026-11-12

PR #152953 security review (Teknium) — the tool boundary is now confined.

### Changed

- `receipts_build` `out`: absolute, `~` and `..`-traversal paths are no
  longer honoured at their target — the filename is reused inside
  `plugin-data/session-receipts/` under the profile-correct Hermes home.
  Relative names keep the CLI's cwd behaviour. A platform file-safety
  denial (`agent.file_safety.is_write_denied`) refuses the write when a
  safety layer exists.
- `redact_words` from the tool accepts ONLY a list of strings. Word-list
  file paths are a CLI-only form and are a clean error from the tool.
- `hermes_home()` consults `hermes_constants.get_hermes_home()` FIRST;
  `$HERMES_HOME` is a standalone fallback only, so a multi-profile gateway
  can no longer render another profile's sessions.
- The tool resolves the session store as the active profile's
  `state.db` — `RECEIPTS_DB` redirection is CLI/tests-only.
- `demo/gen_audio.py` no longer reads the Hermes `.env` from the shipped
  tree; `FAL_KEY` must be exported by the caller.

### Added

- `tests/test_tool_confinement.py` — out-path confinement, word-list form
  rejection, resolver ordering, and tool-db non-redirection.

## 1.2.0 — 2026-11-07

Share-safe and client-facing: a receipt can now be sent to the person it is
about, and it reads as a document rather than a console.

### Added

- **`--redact`** writes a share-safe *copy*: provider keys, JWTs, bearer/
  authorization tokens, URL credentials and query secrets, sensitive `KEY=value`
  assignments, e-mails, phone numbers, IPv4 addresses and account paths
  (`C:\Users\…`, `/home/…`, `/c/Users/…`, UNC) are replaced with numbered
  placeholders (`[secret-1]`, `[email-1]`, `[user]`, …). The default output name gains
  `.redacted`, so the plain receipt keeps its name.
- **`.redaction.json` sidecar** beside every redacted copy: per-class, per-rule
  and per-field counts, the word-list size, the manifest timestamp and a
  post-pass residual scan. The same report is embedded in `--json` output.
- **`--redact-words PATH`** removes extra whole words (client names, account
  handles) case-insensitively; blank lines and `#` comments are ignored. It
  requires `--redact`, and `rollup --redact` is refused instead of silently
  emitting an unredacted digest.
- **`--theme client`** renders the same receipt as a paper document: `#fbfaf7`
  page, serif headings, system-sans body, monospace reserved for code, no
  scanline texture, no masthead sweep, print rules for a clean white sheet.
  `instrument` stays the default and its output is **byte-identical** to 1.1.0
  for the same manifest.
- **`receipts_build` tool options:** the agent tool takes the same `theme`,
  `redact` and `redact_words` options as the CLI (defaults unchanged —
  instrument, no redaction). A redacted call writes the sidecar and returns its
  path with the per-class counts.
- **Redaction notice band** on every redacted copy, in both themes: the class
  counts are declared in the document, never silent.
- `samples/receipt-original-instrument.html`, `receipt-redacted-instrument.html`
  and `receipt-redacted-client.html` (+ sidecars) built from the synthetic
  share store in `docs/evidence/1.2/`, with a check that the redacted document
  differs from the original only by placeholders and the band.

### Changed

- `receipts` now reports its version in the footer as `1.2.0`.
- CLI help states the default redacted-copy name and the sidecar.

### Notes

- Redaction is pattern-based, not a guarantee: a secret that matches no rule, a
  bare account handle (`whoami`, `ls -l` owner column) or a client name is not
  removed unless it is in the word list. A manual read of the copy still
  applies. This is stated in the README's "Sharing a receipt" section.
- The sidecar counts *manifest fields*; the document prints a subset of them
  (the manifest stores each call and its arguments more than once for the flow
  chart), so the band's total can exceed the number of placeholders a reader
  can count.
- `samples/rollup-demo.html` / `.json` were rebuilt for this release: the rollup
  digest covers `tool_version`, so a version bump changes the digest even when
  no measurement moved. The committed sample is the 1.2.0 payload.

## 1.1.0 — 2026-11-06

Weekly process digest.

### Added

- `receipts rollup --since 7d [--board <slug>] [--as-of <moment>]`: one
  deterministic HTML + JSON digest per window — failure taxonomy from an
  ordered rule table, cost by model and by card reconciled to session rows and
  receipts, slowest tools by p50/p95 with stated exclusions, retry hotspots,
  and week-over-week deltas.
- `samples/rollup-demo.html` / `.json`, rebuilt from the committed fixture
  inside the test suite (the sample is a checked claim, not a screenshot).
- Cron-friendly output: a pre-formatted `desk_line`, byte-stable for a closed
  window.

## 1.0.0 — 2026-11-05

Session flight recorder.

### Added

- `hermes receipts <session|date|range|card>` → one self-contained, offline
  HTML receipt: the original ask, every tool call as a timed node, failures and
  retries exactly as recorded, per-model token spend, the recorded cost with a
  reconciliation delta, a work-flow chart and a provenance footer.
- `receipts_build` agent tool and the `hermes receipts` subcommand share one
  argument surface.
- Instrument theme (dark console), client-side filters and jump-to-first-failure.
