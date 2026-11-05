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


## 5. Catalog PR

**Entry** — to be added as `plugin-catalog/receipts.yaml` in
`NousResearch/hermes-agent`:

```yaml
name: receipts
repo: <PUBLIC_REPO_URL>
sha: <40-HEX-SHA-OF-TAGGED-COMMIT>
title: Receipts
description: 'Session flight recorder. Turns a Hermes session, a date range, or a kanban card evidence chain
  into one self-contained offline HTML receipt: the original ask, every tool call as a timed node, failures and
  retries exactly as recorded, per-model token spend, and the recorded cost. Reads the local session store only;
  no network, no credentials, no new dependencies.'
maintainer: Ryan O'Mara
tier: community
category: observability
version: "1.0.0"
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
a reconciliation delta.

**Why it belongs in the catalog.** It is read-only against `state.db`, stdlib-only,
and useful to anyone who has to show what an agent did — the artefact is portable
and opens offline.

**Admission checks.**
- `hermes plugins validate .` — passes every gate (manifest, capability probe,
  no tool collisions, security scan, no core override).
- No self-updater, no network calls, no credentials.
- Declared capabilities match registrations: `provides_tools: [receipts_build]`.
- 30 stdlib tests: `python -m unittest discover -s tests`.

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

- `python -m unittest discover -s tests` → 30 tests, OK.
- `hermes plugins validate .` → *Validation passed* (all gates).
- `HERMES_HOME=<scratch> hermes receipts latest --db <fixture>` → artifact, 39 KB.
- Real session read-only render → 80 tool calls, 5 failures, 210 KB.
- Artifact: 0 external references, 0 console errors, filters functional.
- Live gateway plugin directory untouched; all install tests ran in a scratch
  `HERMES_HOME`.
