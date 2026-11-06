"""Client-facing theme for receipts.

The instrument theme is for the person who ran the agent: dense, monospace,
dark, with scanlines and a sweeping masthead. A client reads differently — they
want a document, not a console. Same data, same honesty rules, quieter surface:
paper background, serif headings, system sans body, no motion, print rules.

The theme is the *same* instrument stylesheet with its console surface removed
(scanline texture and the masthead sweep), then ``CLIENT_CSS`` appended. Nothing
else in the shared sheet changes, so layout, content and the honesty styling
stay in one place and the instrument theme's own output is untouched.

Applied by adding ``class="theme-client"`` to <body> and using
``client_sheet(render._CSS)`` in place of the bare instrument sheet.
"""

# Exact slices of ``receipts.render._CSS``. They are removed for the client
# document, never rewritten, so the instrument sheet remains the single source
# of truth. ``client_sheet`` verifies each slice is present: if someone edits
# the instrument sheet, the client render fails loudly instead of silently
# shipping scanlines or a sweep into a document that is meant to be still.
CONSOLE_SURFACE = (
    # scanline texture + pointer-lit radial glows, painted on <body>
    """  background-image:
    repeating-linear-gradient(0deg,rgba(255,255,255,.014) 0 1px,transparent 1px 3px),
    radial-gradient(1200px 600px at 20% -10%,rgba(58,214,207,.06),transparent 60%),
    radial-gradient(900px 500px at 90% 0%,rgba(255,176,32,.05),transparent 55%);
  background-attachment:fixed;
""",
    # masthead sweep: the animated sheen and its keyframes
    """.mark::after{content:"";position:absolute;inset:0;
  background:linear-gradient(90deg,transparent 45%,rgba(58,214,207,.5) 50%,transparent 55%);
  animation:sweep 4.5s linear infinite}
@keyframes sweep{from{transform:translateX(-120%)}to{transform:translateX(120%)}}
""",
)

CLIENT_CSS = r"""
body.theme-client{
  --bg:#fbfaf7; --panel:#ffffff; --panel2:#f7f6f2; --raise:#f0ede6;
  --line:#e3e0d7; --line2:#d1cdc2;
  --ink:#191b1f; --dim:#565d68; --faint:#8a9099;
  --amber:#8a5a00; --cyan:#1d5f6b; --red:#a3231b; --green:#1c6b46; --violet:#54479f;
  --sans:system-ui,-apple-system,"Segoe UI",Roboto,"Helvetica Neue",Arial,sans-serif;
  --serif:"Iowan Old Style","Palatino Linotype",Palatino,Georgia,"Times New Roman",serif;
  background:var(--bg); background-image:none; background-attachment:scroll;
  color:var(--ink); font-family:var(--sans); font-size:14px; line-height:1.62;
  -webkit-font-smoothing:auto;
}
/* A document is still: no animation, no transitions, whatever the base sheet says. */
body.theme-client *,body.theme-client *::before,body.theme-client *::after{
  animation:none!important; transition:none!important}

body.theme-client .wrap{max-width:920px;padding:44px 30px 88px}

/* Prose is sans; code, raw payloads and identifiers stay monospace. */
body.theme-client .mono,body.theme-client code,body.theme-client .node details pre{
  font-family:var(--mono);font-size:12.5px}

/* ---- masthead ---- */
body.theme-client .mast{border-bottom:1px solid var(--line2);padding-bottom:18px;margin-bottom:30px}
body.theme-client .brand{gap:12px}
body.theme-client .mark{width:28px;height:28px;flex:0 0 28px;background:#fff;
  border-color:var(--line2);border-radius:4px}
body.theme-client .mark::after{display:none}
body.theme-client .mark svg{width:15px;height:15px;stroke:var(--cyan)}
body.theme-client .brand h1{font-family:var(--serif);font-size:21px;font-weight:600;
  letter-spacing:0;text-transform:none}
body.theme-client .brand .sub{font-size:12px;letter-spacing:.02em;text-transform:none;color:var(--dim)}
body.theme-client .mast-meta{font-size:11.5px;color:var(--faint);line-height:1.75}
body.theme-client .mast-meta b{color:var(--dim);font-weight:600}

/* ---- headings ---- */
body.theme-client h2.title{font-family:var(--serif);font-size:28px;font-weight:600;
  letter-spacing:-.005em;line-height:1.28;margin:0 0 8px}
body.theme-client .subtitle{font-size:14px;color:var(--dim);margin-bottom:22px}
body.theme-client h3{font-family:var(--serif);font-size:17px;letter-spacing:0;
  text-transform:none;color:var(--ink);font-weight:600;margin:0 0 12px}
body.theme-client h3::after{background:var(--line)}

/* ---- instrument strip: still a data band, just paper ---- */
body.theme-client .strip{background:var(--line);border-color:var(--line);
  border-radius:6px;margin-bottom:24px}
body.theme-client .cell{background:var(--panel);padding:12px 14px 11px}
body.theme-client .cell .k{font-size:10px;letter-spacing:.08em;color:var(--faint)}
body.theme-client .cell .v{font-size:19px;letter-spacing:0;color:var(--ink)}
body.theme-client .cell .v small{font-size:11px;font-weight:400;color:var(--dim)}

/* ---- the ask ---- */
body.theme-client .ask,body.theme-client .ev-ask{background:var(--panel2);
  border:1px solid var(--line);border-left:2px solid var(--cyan);border-radius:4px}
body.theme-client .ask .who,body.theme-client .ev-ask .who{font-size:11px;
  letter-spacing:.06em;text-transform:none;color:var(--dim)}
body.theme-client .ask pre{font-family:var(--sans);font-size:14px;line-height:1.65;color:var(--ink)}
body.theme-client .ev-answer{font-size:13px;color:var(--dim)}

/* ---- bands: keep the honesty colours, drop the glow ---- */
body.theme-client .band{border-radius:4px;background:var(--panel);font-size:13px;color:var(--dim)}
body.theme-client .band.bad{border-left-color:var(--red);background:#fdf4f3}
body.theme-client .band.warn{border-left-color:var(--amber);background:#fdf9ef}
body.theme-client .band-h{font-size:13px;letter-spacing:.01em;text-transform:none;color:var(--ink)}
body.theme-client .band.bad .band-h{color:#7d1d16}
body.theme-client .band.warn .band-h{color:#6f4c00}
body.theme-client .band li code,body.theme-client .band-b code{color:var(--ink)}
body.theme-client .redact-band{background:#fdf8ec;border:1px solid #e5d7b4;
  border-left:2px solid var(--amber);border-radius:4px;padding:11px 14px;margin:0 0 22px;
  font-size:12.5px;color:#5d4300}
body.theme-client .redact-band b{color:#6b4c00}

/* ---- timeline ---- */
body.theme-client .node{border-color:var(--line);border-radius:4px;
  background:var(--panel);margin-bottom:6px}
body.theme-client .node .row{padding:9px 13px}
body.theme-client .node .idx{font-size:11px;color:var(--faint)}
body.theme-client .node .tool{font-size:13px;font-weight:600}
body.theme-client .node .tool .par{font-size:10.5px;color:var(--faint)}
body.theme-client .node .args{font-size:12px;color:var(--dim)}
body.theme-client .node .args code{font-size:11.5px;color:var(--dim)}
body.theme-client .node .meta{font-size:10.5px;color:var(--faint)}
body.theme-client .chip{font-size:9.5px;letter-spacing:.02em;padding:1px 5px;border-radius:3px}
body.theme-client .node.failed{background:#fffafa;border-left-color:var(--red)}
body.theme-client .node.failed .tool{color:var(--red)}
body.theme-client .err{background:#fdf4f3;color:#7d1d16;border-left-color:var(--red);
  font-size:12px;border-radius:0 3px 3px 0}
body.theme-client .err .lbl{color:var(--red);letter-spacing:.06em;text-transform:none}
body.theme-client .res{font-size:11.5px;color:var(--faint)}
body.theme-client .res::before{color:var(--line2)}
body.theme-client .node details pre{background:var(--panel2);border-color:var(--line);
  font-size:11.5px;color:var(--dim)}
body.theme-client .controls{gap:6px;margin-bottom:12px}
body.theme-client button.f{font-family:var(--sans);font-size:11px;letter-spacing:0;
  text-transform:none;padding:4px 9px;border-radius:3px;background:var(--panel);
  border-color:var(--line);color:var(--dim)}
body.theme-client button.f:hover{border-color:var(--line2);color:var(--ink)}
body.theme-client button.f.on{background:var(--panel2);border-color:var(--cyan);color:var(--cyan)}

/* ---- flow ---- */
body.theme-client .flowbox{background:var(--panel);border-color:var(--line);border-radius:4px}
body.theme-client .fnode{background:var(--panel2);border-color:var(--line2);border-radius:4px}
body.theme-client .fnode .n{font-size:12px;color:var(--ink)}
body.theme-client .fnode .c{font-size:10px;letter-spacing:.03em;color:var(--faint)}
body.theme-client .fnode.hasfail{background:#fdf4f3;border-color:rgba(163,35,27,.45)}
body.theme-client .fnode.hasfail .n{color:var(--red)}
body.theme-client .fnode .fb{background:var(--red);color:#fff}
body.theme-client .arrow{color:var(--line2)}
body.theme-client .legend{font-size:11px;color:var(--faint)}

/* ---- tables, reconciliation, footer ---- */
body.theme-client table{font-size:12.5px}
body.theme-client th{font-size:10px;letter-spacing:.06em;border-bottom-color:var(--line2)}
body.theme-client td{border-bottom-color:var(--line);color:var(--dim)}
body.theme-client tr.total td{color:var(--ink)}
body.theme-client tr:hover td{background:#faf8f3}
body.theme-client .tbl{background:var(--panel);border-color:var(--line);border-radius:4px}
body.theme-client .recon{background:var(--panel);border-color:var(--line);border-radius:4px}
body.theme-client .recon .line{font-size:12.5px;border-bottom-color:var(--line)}
body.theme-client .recon .note{font-size:11.5px;color:var(--faint)}
body.theme-client footer{border-top-color:var(--line);font-size:11px;color:var(--faint)}
body.theme-client .kv .k{min-width:84px;color:var(--faint)}
body.theme-client .kv .v{color:var(--dim)}
body.theme-client .stamp{letter-spacing:.1em;text-transform:none;font-size:10px}

/* ---- multi-session index ---- */
body.theme-client .sess{border-color:var(--line);border-radius:4px}
body.theme-client .sess .hd{border-bottom-color:var(--line);background:var(--panel2)}
body.theme-client .sess .hd .t{font-family:var(--serif);font-size:14px;font-weight:600}

/* ---- links: a document's underline, not a console's glow ---- */
body.theme-client a{color:var(--cyan);text-decoration:none;
  border-bottom:1px solid rgba(29,95,107,.28)}
body.theme-client a:hover{border-bottom-color:var(--cyan)}

/* ---- print: a white sheet, no web padding, no card cut in half ---- */
@page{margin:14mm 13mm}
@media print{
  body.theme-client{background:#fff;color:#111;background-image:none}
  body.theme-client .wrap{max-width:none;margin:0;padding:0}
  body.theme-client .mast{margin-bottom:18px}
  body.theme-client .controls{display:none}
  body.theme-client .node,
  body.theme-client .ask,
  body.theme-client .ev-ask,
  body.theme-client .band,
  body.theme-client .cell,
  body.theme-client .recon,
  body.theme-client tr{break-inside:avoid;page-break-inside:avoid}
  /* Containers longer than a page must fragment, not be forced onto one page:
     a 120-step flow chart or a multi-session index has to break between rows. */
  body.theme-client .node,
  body.theme-client .tbl,
  body.theme-client .flowbox,
  body.theme-client .sess{overflow:visible}
  body.theme-client .tbl,
  body.theme-client .sess{break-inside:auto}
  body.theme-client .fnode,body.theme-client .sess .hd{break-inside:avoid}
  /* Keep the flow's flex layout (Blink will not fragment it), but compact it
     enough that a 120-call chart fits one printed page. Hiding the arrows is
     what buys the width; reading order still carries the sequence. */
  body.theme-client .flow{gap:4px}
  body.theme-client .flowbox{padding:8px 10px 2px}
  body.theme-client .fnode{padding:3px 6px;min-width:74px}
  body.theme-client .fnode .n{font-size:10px;max-width:104px}
  body.theme-client .fnode .c{font-size:8px}
  body.theme-client .legend{margin-top:6px;font-size:9px;gap:10px}
  body.theme-client .arrow{display:none}
  body.theme-client h2.title,body.theme-client h3{break-after:avoid;page-break-after:avoid}
  body.theme-client a{color:#111;border-bottom-color:#b9b4a6}
  body.theme-client .node .meta{white-space:normal}
}
"""


def client_sheet(instrument_css: str) -> str:
    """Return the client stylesheet: instrument layout, console surface removed.

    Raises RuntimeError if a slice is missing — that means the instrument sheet
    changed and this module must be updated, not that the client should quietly
    inherit scanlines or an animation.
    """
    sheet = instrument_css
    for block in CONSOLE_SURFACE:
        if block not in sheet:
            raise RuntimeError(
                "theme_client: instrument stylesheet no longer contains a console-surface "
                "slice; update CONSOLE_SURFACE in receipts/theme_client.py"
            )
        sheet = sheet.replace(block, "", 1)
    return sheet + CLIENT_CSS
