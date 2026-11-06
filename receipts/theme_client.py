"""Client-facing theme for receipts.

The instrument theme is for the person who ran the agent: dense, monospace,
dark, with scanlines. A client reads differently — they want a document, not a
console. Same data, same honesty rules, quieter surface: paper background,
serif headings, no animation, print-friendly.

Applied by adding `class="theme-client"` to <body> and appending this sheet
after the instrument stylesheet (so it only overrides tokens and a few
components, never the layout).
"""

CLIENT_CSS = r"""
body.theme-client{
  --bg:#fbfaf7; --panel:#ffffff; --panel2:#f6f5f1; --raise:#efede6;
  --line:#e2e0d8; --line2:#cfccc2;
  --ink:#181a1f; --dim:#5c6470; --faint:#8d95a1;
  --amber:#8a5a00; --cyan:#1f5f6e; --red:#a5281e; --green:#1d6b45; --violet:#5145a8;
  --sans:"Inter",system-ui,-apple-system,"Segoe UI",Roboto,Helvetica,Arial,sans-serif;
  --serif:"Iowan Old Style",Georgia,"Times New Roman",serif;
  background-image:none; background:var(--bg); color:var(--ink);
  font-family:var(--sans); font-size:14px; line-height:1.62;
}
body.theme-client .mono,body.theme-client pre,body.theme-client code{font-family:var(--mono);font-size:12.5px}
body.theme-client .wrap{max-width:920px;padding:44px 30px 80px}
body.theme-client .mast{border-bottom:1px solid var(--line);padding-bottom:18px}
body.theme-client .brand h1{font-family:var(--serif);font-size:20px;letter-spacing:0;text-transform:none;font-weight:600}
body.theme-client .brand .sub{letter-spacing:.04em;text-transform:none;font-size:12px;color:var(--dim)}
body.theme-client .mark{background:#fff;border-color:var(--line2)}
body.theme-client .mark::after{animation:none;background:none}
body.theme-client h2.title{font-family:var(--serif);font-size:27px;font-weight:600;letter-spacing:-.01em;line-height:1.3}
body.theme-client h3{font-family:var(--serif);font-size:16px;letter-spacing:0;text-transform:none;color:var(--dim)}
body.theme-client .ask,body.theme-client .card,body.theme-client .panel{background:var(--panel);border-color:var(--line)}
body.theme-client .ask pre{font-size:13px;line-height:1.6}
body.theme-client .lede{font-size:14px;color:var(--dim)}
body.theme-client .redact-band{
  background:#fff7e8;border:1px solid #e8d9b8;color:#6b4e00;
  border-radius:4px;padding:10px 14px;margin:0 0 20px;font-size:12.5px}
body.theme-client a{color:var(--cyan);border-bottom:1px solid rgba(31,95,110,.25)}
@media print{
  body.theme-client{background:#fff}
  body.theme-client .wrap{padding:0}
  body.theme-client .card,body.theme-client .panel{break-inside:avoid}
}
"""
