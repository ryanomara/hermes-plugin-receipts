# Evidence — 1.2-c client theme

What the client theme is: the instrument stylesheet with its console surface
removed (scanline texture and the masthead sweep), then `CLIENT_CSS` appended,
and `<body class="theme-client">`. Layout, data and honesty styling are shared,
so the two themes cannot drift apart.

## Files

| file | what it proves |
|---|---|
| `receipt-120-client.html` | the client render of a 120-call receipt |
| `receipt-120-client.pdf` | its print-to-PDF (20 pages, A4) |
| `print-check.json` | every one of the 120 rows has its number and arguments on the same page; the work-flow chart has all 108 groups / 120 calls and its legend |
| `page-01.png`, `page-break-09.png`, `page-19-flow.png`, `page-20.png` | spot checks: first page, a clean break between timeline cards, the full flow chart, the final table/reconciliation/footer |
| `theme-check.json` | 24/24 structural checks: same body except the class, instrument sheet exactly `render._CSS`, client sheet exactly `client_sheet(_CSS)`, no sweep/scanlines/animation, paper/serif/sans/print/link rules present |
| `make_print_fixture.py` | builds the deterministic 120-call store (12 cycles, 4 failed-and-recovered retry chains) |
| `print_check.py` | PDF row/flow verifier (pypdfium2) |
| `verify_theme.py` | two-theme contract verifier |

## Reproduce

```sh
python docs/evidence/client-theme/make_print_fixture.py /tmp/print-120.db
python -m receipts --db /tmp/print-120.db --theme client \
  -o docs/evidence/client-theme/receipt-120-client.html

# print with Chrome. The committed PDF used CDP Page.printToPDF with
# printBackground=true and print media emulated; the headless CLI below produces
# the same pagination without backgrounds and also passes print_check.py.
chrome --headless=new --disable-gpu --no-sandbox --no-pdf-header-footer \
  --print-to-pdf=docs/evidence/client-theme/receipt-120-client.pdf \
  file:///absolute/path/receipt-120-client.html

uv run --no-project --with pypdfium2 --with pillow python \
  docs/evidence/client-theme/print_check.py \
  docs/evidence/client-theme/receipt-120-client.pdf /tmp/print-120.db \
  docs/evidence/client-theme

python docs/evidence/client-theme/verify_theme.py /tmp/print-120.db \
  docs/evidence/client-theme/theme-check.json
```

## Verified results

- `print_check.py`: 20 pages, 120/120 rows with number + arguments on the same
  page, 0 split/missing, flow 108/108 groups and 120/120 calls, legend present.
  The headless CLI path was also checked: 21 pages, all 120 rows, 108/108 flow
  groups, legend present.
- `verify_theme.py`: 24/24 checks, no failures.
- Instrument byte-identity: the same 120-call manifest rendered by `d541317`
  (pre-1.2) and by this tree is byte-for-byte identical —
  132,007 bytes, sha256 `199b911214a48e0976b490959ea10dfc27773fc9d1d736797549562f6aaaff09`.
- An unknown `theme=` value raises `ValueError` instead of silently rendering
  instrument.
- The redaction band now has an instrument base rule, appended only when a band
  is present; a non-redacted instrument receipt therefore keeps its exact bytes.

## Print notes

- `@page{margin:14mm 13mm}` plus `.wrap{padding:0}` gives a white sheet with no
  web padding; cards, cells, bands, reconciliation and table rows use
  `break-inside:avoid`.
- The work-flow chart is a flex container, and Blink does not fragment flex
  containers across pages. For print it is compacted (arrows hidden, tighter
  cards) so a 120-call chart fits one page complete. A chart taller than a page
  would still clip — the timeline is the authoritative call record.
- `theme_client.CONSOLE_SURFACE` holds exact slices of `render._CSS`. If that
  sheet changes, `client_sheet()` raises instead of letting scanlines or a sweep
  leak into a client document.
