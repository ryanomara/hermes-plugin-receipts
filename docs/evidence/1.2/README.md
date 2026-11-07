# Evidence — 1.2 samples: share-safe redaction + client theme

Two claims, both checkable from this directory: a receipt can be turned into a
share-safe copy without touching the original, and the same receipt reads as a
paper document rather than a console.

## Files

| file | what it proves |
|---|---|
| `samples/receipt-original-instrument.html` | the **before**: the share store's receipt, instrument theme, nothing removed |
| `samples/receipt-redacted-instrument.html` + `.redaction.json` | the **after**, same manifest and theme — diffing it against the file above must show placeholders and the band, nothing else |
| `samples/receipt-redacted-client.html` + `.redaction.json` | the artefact you actually send: same data, client (paper) theme, credentials removed |
| `samples-check.json` | machine-recorded checks: no seeded literal survives, band names every class, both diff proofs, the CLI reproduces the sample, sidecar arithmetic |
| `offline-check.json` | headless Chrome on all three files: **0 console messages, 0 external references**, 12 call nodes each, band where expected |
| `client-theme.png` | the client receipt, full page (1280 CSS px wide, 1.5× device scale) |
| `redaction-band.png` | the notice in the client theme, cropped at 2× |
| `redaction-band-instrument.png` | the same notice in the instrument theme — it is styled in both, not only the paper one |
| `make_share_fixture.py` | builds the synthetic store the samples come from |
| `make_samples.py` | renders the three documents and the sidecars, then runs the checks and writes `samples-check.json` |
| `check_offline.py` | the Chrome offline/console check behind `offline-check.json` |
| `redact-words.txt` | the word list used for these samples (one invented client name) |

## Why a purpose-built store

The product fixture (`fixtures/make_demo_db.py`) is sanitized *by
construction* — it deliberately contains nothing a client must not see, so
redacting it removes exactly one account path and proves almost nothing. The
samples therefore use `make_share_fixture.py`: a fictional campaign launch whose
tool calls carry **a secret of each class** the engine claims — a Stripe
webhook secret, a webhook secret in an env file, URL credentials, a query token,
a JWT, e-mail, phone, a private IP, three account-path spellings — plus a client
name from the word list. Every credential is fabricated: `example.com` hosts,
demo-only provider values, private-range addresses, a 555-style number.

Two planted values — a Slack bot token and a bearer token — never reach the
document at all: the manifest already masks credential-shaped strings when it
is built. `samples-check.json` records `reached_document` per value so the
evidence cannot claim a removal the redaction pass did not make.

Provider shapes the security scanner treats as a leaked credential (`sk-`,
`AKIA`, `ghp_`, PEM blocks) are deliberately **not** committed here: a shipped
file with a key-shaped literal fails `hermes plugins validate`. Those rules are
proven by `tests/test_privacy.py`, where the guard expects hostile fixtures.
The sample fires twelve of the engine's seventeen rule names, `api_key`
included.

## Reproduce

```sh
python docs/evidence/1.2/make_share_fixture.py          # fixtures/share-state.db
python docs/evidence/1.2/make_samples.py                # samples/ + samples-check.json
python docs/evidence/1.2/check_offline.py               # offline-check.json (needs Chrome)

# the full-page shot: 1.5x, window height = the page's measured height
# (3446 CSS px for this sample; `document.documentElement.scrollHeight`)
chrome --headless=new --disable-gpu --hide-scrollbars --force-device-scale-factor=1.5 \
  --window-size=1280,3446 --screenshot=docs/evidence/1.2/client-theme.png \
  file:///absolute/path/samples/receipt-redacted-client.html
# band crops: 2x full-page shot, then crop the .redact-band box plus 12px padding
# (or DevTools → select .redact-band → Capture node screenshot at 2x)
```

`make_samples.py` re-runs the redaction engine over the **rendered original**
and requires the result to equal the rendered redacted document once the band
and its stylesheet rule are removed (placeholder numbering normalised, because
the engine numbers manifest fields while the document prints a subset). That is
what "the diff is only placeholders and the band" means here: not an eyeball
diff, an equality check. The same check is run for the client theme using an
in-memory original that is not committed.

Re-running the two Python steps on a clean checkout reproduces the committed
bytes: the render clock, the manifest's `generated_at` and the synthetic store's
mtime (which the provenance block prints) are all pinned to the instant the
samples were first rendered, so regenerating is a no-op diff. The clock is
formatted in local time, so a machine in another timezone will print different
clock text and differ there — nothing else in the documents depends on the
environment.

## Verified results

- **Redaction:** 51 manifest fields removed — secret ×22, e-mail ×4, phone ×4,
  IP ×4, user path ×13, word ×4. Nine of the eleven planted values reach the
  document (the Slack and bearer tokens are masked when the manifest is built);
  none of them, and no word-list entry, appears in either redacted document or
  in the sidecar; `residual` is `{}`.
- **Diff proof:** instrument `equal: true`, client `equal: true`; the CLI
  render of the same store is byte-identical to the committed client sample and
  writes the same sidecar once the two clock readings are masked.
- **Offline:** all three files load from `file://` with zero console messages
  (uncaught errors included), zero resource requests and zero external
  references in the markup.
- **Theme:** the client sample carries `class="theme-client"`, no scanline
  texture and no masthead sweep; the instrument sample is unchanged apart from
  the band.

## Two honest footnotes

- **The band counts manifest fields, not ink.** The manifest stores each call
  twice (timeline event + flow node) and its arguments twice (summary + detail),
  so 51 removed fields print as 27 placeholders. Both numbers are in
  `samples-check.json`; the sidecar describes the data, not the page.
- **`size 0.0 MB` in the provenance line** is the synthetic store being 36 KB
  and the formatter rounding to one decimal — not a failed read. A real store
  renders its real size.
