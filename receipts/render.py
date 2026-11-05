"""Render a receipt manifest as one self-contained HTML document.

No network, no CDN, no external fonts, no build step.  Every dynamic string is
escaped; the only JavaScript is a progressive enhancement that is safe to fail
because the document is fully readable with scripting off.
"""

from __future__ import annotations

import html
import json

from . import __version__

__all__ = ["render_html"]

# --------------------------------------------------------------------------- #
# formatting helpers
# --------------------------------------------------------------------------- #


def esc(value) -> str:
    if value is None:
        return ""
    return html.escape(str(value), quote=True)


def dur(seconds) -> str:
    if seconds is None:
        return "—"
    try:
        seconds = float(seconds)
    except (TypeError, ValueError):
        return "—"
    if seconds < 1:
        return f"{seconds * 1000:.0f}ms"
    if seconds < 60:
        return f"{seconds:.1f}s"
    if seconds < 3600:
        m, s = divmod(int(seconds), 60)
        return f"{m}m {s:02d}s"
    h, rem = divmod(int(seconds), 3600)
    return f"{h}h {rem // 60:02d}m"


def num(value) -> str:
    if value is None:
        return "—"
    try:
        return f"{int(value):,}"
    except (TypeError, ValueError):
        return esc(value)


def money(value, status: str | None = None) -> str:
    if value is None:
        return "—"
    try:
        value = float(value)
    except (TypeError, ValueError):
        return "—"
    if value == 0 and status in (None, "unavailable", "unknown"):
        return "—"
    if value == 0:
        return "$0.00"
    if value < 0.01:
        return f"${value:.6f}"
    if value < 1:
        return f"${value:.4f}"
    return f"${value:,.2f}"


def clock(ts) -> str:
    if not ts:
        return "—"
    from datetime import datetime

    try:
        return datetime.fromtimestamp(float(ts)).strftime("%Y-%m-%d %H:%M:%S")
    except (ValueError, OSError, OverflowError):
        return "—"


def _now_ts() -> float:
    import time

    return time.time()


# --------------------------------------------------------------------------- #
# stylesheet
# --------------------------------------------------------------------------- #

_CSS = r"""
:root{
  --bg:#07080a; --panel:#0d1013; --panel2:#12171c; --raise:#171e25;
  --line:#1e262e; --line2:#2a343d;
  --ink:#dbe2e8; --dim:#8593a0; --faint:#5a6873;
  --amber:#ffb020; --cyan:#3ad6cf; --red:#ff5252; --green:#5bd98d; --violet:#a78bfa;
  --mono:ui-monospace,"Cascadia Mono","SF Mono",Menlo,Consolas,"DejaVu Sans Mono",monospace;
}
*{box-sizing:border-box}
html,body{margin:0;padding:0}
body{
  background:var(--bg); color:var(--ink); font-family:var(--mono);
  font-size:13px; line-height:1.55; -webkit-font-smoothing:antialiased;
  background-image:
    repeating-linear-gradient(0deg,rgba(255,255,255,.014) 0 1px,transparent 1px 3px),
    radial-gradient(1200px 600px at 20% -10%,rgba(58,214,207,.06),transparent 60%),
    radial-gradient(900px 500px at 90% 0%,rgba(255,176,32,.05),transparent 55%);
  background-attachment:fixed;
}
.wrap{max-width:1060px;margin:0 auto;padding:28px 22px 64px}
a{color:var(--cyan);text-decoration:none}
.mono{font-family:var(--mono)}
.dim{color:var(--dim)}
.faint{color:var(--faint)}
.amber{color:var(--amber)}
.red{color:var(--red)}
.green{color:var(--green)}
.cyan{color:var(--cyan)}

/* ---- masthead ---- */
.mast{display:flex;justify-content:space-between;align-items:flex-start;gap:20px;
  border-bottom:1px solid var(--line);padding-bottom:16px;margin-bottom:22px;flex-wrap:wrap}
.brand{display:flex;align-items:center;gap:11px}
.mark{width:26px;height:26px;flex:0 0 26px;border:1px solid var(--line2);border-radius:3px;
  display:grid;place-items:center;background:linear-gradient(160deg,#141b21,#0a0d10);position:relative;overflow:hidden}
.mark::after{content:"";position:absolute;inset:0;
  background:linear-gradient(90deg,transparent 45%,rgba(58,214,207,.5) 50%,transparent 55%);
  animation:sweep 4.5s linear infinite}
@keyframes sweep{from{transform:translateX(-120%)}to{transform:translateX(120%)}}
.mark svg{width:14px;height:14px;position:relative;z-index:1}
.brand h1{font-size:15px;margin:0;letter-spacing:.14em;text-transform:uppercase;font-weight:600}
.brand .sub{font-size:10.5px;color:var(--faint);letter-spacing:.1em;text-transform:uppercase}
.mast-meta{text-align:right;font-size:11px;color:var(--dim);line-height:1.7}
.mast-meta b{color:var(--ink);font-weight:500}

/* ---- hero ---- */
h2.title{font-size:21px;line-height:1.35;margin:0 0 6px;font-weight:600;letter-spacing:-.01em;font-family:var(--mono)}
.subtitle{color:var(--dim);font-size:12px;margin-bottom:20px}

/* ---- instrument strip ---- */
.strip{display:grid;grid-template-columns:repeat(auto-fit,minmax(112px,1fr));gap:1px;
  background:var(--line);border:1px solid var(--line);border-radius:4px;overflow:hidden;margin-bottom:26px}
.cell{background:var(--panel);padding:11px 13px 10px}
.cell .k{font-size:9.5px;letter-spacing:.13em;text-transform:uppercase;color:var(--faint);margin-bottom:3px}
.cell .v{font-size:18px;font-weight:600;letter-spacing:-.02em;line-height:1.2}
.cell .v small{font-size:10px;font-weight:400;color:var(--dim);letter-spacing:0}
.cell.bad .v{color:var(--red)}
.cell.good .v{color:var(--green)}
.cell.warn .v{color:var(--amber)}

/* ---- sections ---- */
section{margin:0 0 30px}
h3{font-size:10.5px;letter-spacing:.18em;text-transform:uppercase;color:var(--dim);
  margin:0 0 12px;font-weight:600;display:flex;align-items:center;gap:10px}
h3::after{content:"";flex:1;height:1px;background:linear-gradient(90deg,var(--line),transparent)}

/* ---- the ask ---- */
.ask{border:1px solid var(--line);border-left:2px solid var(--cyan);background:var(--panel);
  border-radius:3px;padding:14px 16px}
.ask .who{font-size:9.5px;letter-spacing:.14em;text-transform:uppercase;color:var(--cyan);margin-bottom:7px}
.ask pre{margin:0;white-space:pre-wrap;word-break:break-word;font-size:12.5px;line-height:1.62;color:var(--ink)}
details.more{margin-top:8px}
details.more summary{cursor:pointer;color:var(--faint);font-size:11px;list-style:none;user-select:none}
details.more summary::-webkit-details-marker{display:none}
details.more summary::before{content:"▸ ";color:var(--faint)}
details.more[open] summary::before{content:"▾ "}
details.more summary:hover{color:var(--cyan)}

/* ---- controls ---- */
.controls{display:flex;gap:7px;flex-wrap:wrap;margin-bottom:14px;align-items:center}
button.f{background:var(--panel);border:1px solid var(--line);color:var(--dim);
  font-family:var(--mono);font-size:10.5px;letter-spacing:.06em;text-transform:uppercase;
  padding:5px 10px;border-radius:2px;cursor:pointer;transition:.12s}
button.f:hover{border-color:var(--line2);color:var(--ink)}
button.f.on{background:var(--raise);border-color:var(--cyan);color:var(--cyan)}
button.f.jump{border-color:rgba(255,82,82,.4);color:var(--red)}
button.f.jump:hover{background:rgba(255,82,82,.1)}

/* ---- status band ---- */
.band{border:1px solid var(--line);border-left:2px solid var(--line2);border-radius:3px;
  background:var(--panel);padding:10px 14px;margin:0 0 10px;font-size:11.5px}
.band.bad{border-left-color:var(--red);background:linear-gradient(90deg,rgba(255,82,82,.07),transparent 60%)}
.band.warn{border-left-color:var(--amber);background:linear-gradient(90deg,rgba(255,176,32,.05),transparent 60%)}
.band-h{font-size:10.5px;letter-spacing:.1em;text-transform:uppercase;color:var(--ink);
  display:flex;align-items:center;gap:8px;font-weight:600}
.band.bad .band-h{color:#ffc9c9}
.band.warn .band-h{color:#ffe0b0}
.band-h .glyph{display:inline-grid;place-items:center;width:15px;height:15px;border-radius:2px;
  font-size:10px;border:1px solid currentColor;line-height:1}
.band ul{margin:7px 0 0;padding-left:18px;color:var(--dim)}
.band li{margin:2px 0}
.band li code{color:var(--ink)}
.band-b{margin-top:6px;color:var(--dim)}
.band-b code{color:var(--ink)}

/* ---- timeline ---- */
.tl{position:relative;padding-left:0}
.node{position:relative;border:1px solid var(--line);border-radius:3px;background:var(--panel);
  margin-bottom:7px;overflow:hidden}
.node[hidden]{display:none}
.node .row{display:grid;grid-template-columns:44px 1fr auto;gap:12px;align-items:baseline;
  padding:9px 12px;cursor:default}
.node .idx{color:var(--faint);font-size:11px;font-variant-numeric:tabular-nums}
.node .tool{font-weight:600;color:var(--ink);font-size:12.5px}
.node .tool .par{color:var(--faint);font-weight:400;font-size:10.5px;margin-left:6px}
.node .args{grid-column:2/4;color:var(--dim);font-size:11.5px;word-break:break-word;
  padding:0 12px 8px 56px;margin-top:-2px}
.node .args code{color:var(--dim)}
.node .meta{text-align:right;font-size:10.5px;color:var(--faint);white-space:nowrap}
.bar{height:2px;background:var(--line);position:relative;margin:0 12px 0 56px}
.bar i{position:absolute;left:0;top:0;bottom:0;background:var(--cyan);opacity:.55;border-radius:2px}
.node.ok{border-left:2px solid rgba(91,217,141,.55)}
.node.ok .bar i{background:var(--green)}
.node.unverified{border-left:2px solid rgba(255,176,32,.55)}
.node.unverified .bar i{background:var(--amber)}
.node.failed{border-left:2px solid var(--red);background:linear-gradient(180deg,rgba(255,82,82,.055),transparent 70%)}
.node.failed .bar i{background:var(--red)}
.node.failed .tool{color:#ffd6d6}
.node.unterminated{border-left:2px dashed rgba(255,176,32,.6)}
.chip{display:inline-block;font-size:9.5px;letter-spacing:.09em;text-transform:uppercase;
  padding:1.5px 6px;border-radius:2px;border:1px solid currentColor;line-height:1.5;vertical-align:1px}
.chip.ok{color:var(--green)}
.chip.failed{color:var(--red)}
.chip.unverified{color:var(--amber)}
.chip.unterminated{color:var(--amber);border-style:dashed}
.chip.retry{color:var(--violet);border-color:rgba(167,139,250,.5)}
.chip.par{color:var(--faint);border-color:var(--line2)}
.err{margin:0 12px 10px 56px;padding:8px 10px;border-left:2px solid var(--red);
  background:rgba(255,82,82,.07);color:#ffc9c9;font-size:11.5px;white-space:pre-wrap;
  word-break:break-word;border-radius:0 2px 2px 0}
.err .lbl{color:var(--red);font-size:9.5px;letter-spacing:.12em;text-transform:uppercase;display:block;margin-bottom:3px}
.res{margin:0 12px 9px 56px;color:var(--faint);font-size:11px;word-break:break-word}
.res::before{content:"↳ ";color:var(--line2)}
.node details{margin:0 12px 10px 56px}
.node details pre{margin:6px 0 0;padding:8px;background:#080a0c;border:1px solid var(--line);
  border-radius:2px;font-size:10.5px;color:var(--dim);white-space:pre-wrap;word-break:break-word;
  max-height:320px;overflow:auto}

/* ---- ask/answer interleave ---- */
.ev-ask{border:1px solid var(--line);border-left:2px solid var(--cyan);background:var(--panel2);
  border-radius:3px;padding:11px 14px;margin:16px 0 10px}
.ev-ask .who{font-size:9.5px;letter-spacing:.14em;text-transform:uppercase;color:var(--cyan);margin-bottom:5px}
.ev-ask pre{margin:0;white-space:pre-wrap;word-break:break-word;font-size:12px;color:var(--ink)}
.ev-answer{border-left:2px solid rgba(133,147,160,.5);padding:8px 14px;margin:12px 0;
  color:var(--dim);font-size:11.5px;white-space:pre-wrap;word-break:break-word}
.ev-answer .who{font-size:9.5px;letter-spacing:.14em;text-transform:uppercase;color:var(--faint);margin-bottom:4px}

/* ---- flow ---- */
.flowbox{border:1px solid var(--line);background:var(--panel);border-radius:3px;padding:16px 14px 10px;overflow-x:auto}
.flow{display:flex;flex-wrap:wrap;gap:8px;align-items:center}
.fnode{border:1px solid var(--line2);background:var(--panel2);border-radius:3px;padding:7px 10px;
  min-width:82px;position:relative}
.fnode .n{font-size:11.5px;font-weight:600;color:var(--ink);display:block;white-space:nowrap;
  overflow:hidden;text-overflow:ellipsis;max-width:130px}
.fnode .c{font-size:9.5px;color:var(--faint);letter-spacing:.06em}
.fnode.hasfail{border-color:rgba(255,82,82,.65);background:rgba(255,82,82,.06)}
.fnode.hasfail .n{color:#ffd6d6}
.fnode .fb{position:absolute;top:-6px;right:-6px;background:var(--red);color:#1a0505;
  font-size:9px;font-weight:700;border-radius:8px;padding:0 5px;line-height:13px}
.arrow{color:var(--line2);font-size:12px;user-select:none}
.legend{margin-top:12px;font-size:10.5px;color:var(--faint);display:flex;gap:16px;flex-wrap:wrap}
.legend i{display:inline-block;width:8px;height:8px;border-radius:2px;margin-right:5px;vertical-align:0}

/* ---- tables ---- */
table{width:100%;border-collapse:collapse;font-size:11.5px}
th{text-align:left;font-size:9.5px;letter-spacing:.11em;text-transform:uppercase;color:var(--faint);
  font-weight:600;padding:7px 9px;border-bottom:1px solid var(--line2);white-space:nowrap}
td{padding:7px 9px;border-bottom:1px solid var(--line);color:var(--dim)}
td.n,th.n{text-align:right;font-variant-numeric:tabular-nums}
tr.total td{color:var(--ink);font-weight:600;border-top:1px solid var(--line2);border-bottom:none}
tr:hover td{background:rgba(255,255,255,.014)}
.tbl{border:1px solid var(--line);border-radius:3px;overflow:hidden;background:var(--panel)}

/* ---- reconciliation ---- */
.recon{border:1px solid var(--line);background:var(--panel);border-radius:3px;padding:13px 15px}
.recon .line{display:flex;justify-content:space-between;gap:14px;padding:4px 0;font-size:11.5px;
  border-bottom:1px dotted var(--line)}
.recon .line:last-child{border-bottom:none}
.recon .ok{color:var(--green)}
.recon .drift{color:var(--amber)}
.recon .note{margin-top:9px;font-size:10.5px;color:var(--faint);line-height:1.6}

/* ---- footer ---- */
footer{margin-top:44px;padding-top:16px;border-top:1px solid var(--line);font-size:10.5px;
  color:var(--faint);line-height:1.8}
footer .grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(220px,1fr));gap:14px 26px}
footer b{color:var(--dim);font-weight:500}
.kv{display:flex;gap:8px}
.kv .k{color:var(--faint);min-width:74px;display:inline-block}
.kv .v{color:var(--dim);word-break:break-all}
.stamp{margin-top:18px;letter-spacing:.16em;text-transform:uppercase;font-size:9px}

/* ---- session index ---- */
.sess{border:1px solid var(--line);border-radius:3px;background:var(--panel);margin-bottom:26px}
.sess .hd{padding:10px 14px;border-bottom:1px solid var(--line);display:flex;justify-content:space-between;
  gap:12px;align-items:baseline;flex-wrap:wrap}
.sess .hd .t{font-weight:600;font-size:12.5px}
.sess .bd{padding:14px}

@media print{
  body{background:#fff;color:#111;background-image:none}
  .mark::after{display:none}
  button.f,.controls{display:none}
  .node,.ask,.flowbox,.tbl,.recon,.cell{break-inside:avoid}
  a{color:#111}
}
@media (max-width:640px){
  .wrap{padding:18px 12px 40px}
  .node .row{grid-template-columns:34px 1fr;gap:8px}
  .node .meta{grid-column:2;text-align:left;margin-top:2px}
  .node .args,.node .err,.res,.node details,.bar{margin-left:12px;padding-left:0}
  h2.title{font-size:17px}
}
"""

# --------------------------------------------------------------------------- #
# JavaScript (progressive enhancement only)
# --------------------------------------------------------------------------- #

_JS = r"""
(function(){
  "use strict";
  var nodes = Array.prototype.slice.call(document.querySelectorAll("[data-node]"));
  var buttons = Array.prototype.slice.call(document.querySelectorAll("button.f[data-filter]"));
  if (!buttons.length) return;

  function apply(filter){
    nodes.forEach(function(n){
      var show = true;
      if (filter === "fail") show = n.getAttribute("data-status") !== "ok"
                                 && n.getAttribute("data-status") !== "unverified";
      else if (filter === "slow") show = parseFloat(n.getAttribute("data-dur") || "0") >= 5;
      else if (filter === "retry") show = n.getAttribute("data-retry") === "1";
      n.hidden = !show;
    });
    buttons.forEach(function(b){ b.classList.toggle("on", b.getAttribute("data-filter") === filter); });
  }

  buttons.forEach(function(b){
    b.addEventListener("click", function(){ apply(b.getAttribute("data-filter")); });
  });

  var jump = document.querySelector("button.f.jump");
  if (jump) {
    jump.addEventListener("click", function(){
      apply("all");
      var first = document.querySelector('[data-node][data-status="failed"]');
      if (first) {
        first.scrollIntoView({behavior:"smooth", block:"center"});
        first.style.outline = "1px solid #ff5252";
        setTimeout(function(){ first.style.outline = ""; }, 2200);
      }
    });
  }
})();
"""


# --------------------------------------------------------------------------- #
# section builders
# --------------------------------------------------------------------------- #


def _masthead(m: dict) -> str:
    sess = m["sessions"][0] if m.get("sessions") else {}
    model = esc(sess.get("model") or "—")
    return f"""
<div class="mast">
  <div class="brand">
    <div class="mark">
      <svg viewBox="0 0 16 16" fill="none" stroke="#3ad6cf" stroke-width="1.3">
        <path d="M2 12.5V8M6 12.5V3.5M10 12.5V6M14 12.5V1.5"/>
      </svg>
    </div>
    <div>
      <h1>Receipts</h1>
      <div class="sub">Session flight recorder</div>
    </div>
  </div>
  <div class="mast-meta">
    <div>recorded <b>{esc(clock(m['sessions'][0]['started_at']) if m.get('sessions') else '—')}</b></div>
    <div>model <b>{model}</b> · source <b>{esc(sess.get('source') or '—')}</b></div>
    <div>rendered <b>{esc(clock(_now_ts()))}</b></div>
  </div>
</div>"""


def _hero(m: dict) -> str:
    agg = m["aggregates"]
    recon = m["reconciliation"]
    failures = agg["failures"]

    cells = [
        ("tool calls", f'{num(agg["tool_calls"])}<small> · {num(agg["distinct_tools"])} tools</small>', ""),
        ("failures", f'<span aria-hidden="true">✕</span> {num(failures)}', "bad" if failures else "good"),
        ("retries", f'<span aria-hidden="true">↻</span> {num(agg["retries"])}'
                    f'<small> · {num(agg["retry_chains"])} chains</small>',
         "warn" if agg["retries"] and not agg["failures_unrecovered"] == 0 else ""),
        ("wall clock", dur(agg["wall_clock_s"]), ""),
        ("tokens", f'{num(agg["usage"]["input"] + agg["usage"]["output"])}<small> in + out</small>', ""),
        ("est. cost", money(recon["usage_cost"] or recon["recorded_cost"], recon.get("cost_status")), ""),
    ]

    strip = "".join(
        f'<div class="cell {cls}"><div class="k">{esc(k)}</div><div class="v">{v}</div></div>'
        for k, v, cls in cells
    )

    return f"""
<h2 class="title">{esc(m['title'])}</h2>
{f'<div class="subtitle">{esc(m["subtitle"])}</div>' if m.get('subtitle') else ''}
<div class="strip">{strip}</div>
{_status_band(m)}"""


def _status_band(m: dict) -> str:
    """Name the things a summary would rather hide.

    A count in a KPI cell is easy to skim past. The band states, in words, which
    calls failed and were never resolved, and which results carry no structured
    status at all.
    """
    agg = m["aggregates"]
    bands = []

    unresolved = [n for n in m["nodes"] if n["status"] == "failed" and n["retried_by"] is None]
    if unresolved:
        items = "".join(
            f'<li><b>#{n["index"]:02d}</b> <code>{esc(n["tool"])}</code> '
            f'<span class="dim">{esc(_clip_text(n["args_summary"], 90))}</span>'
            f'{" · <span class=faint>exit " + str(n["exit_code"]) + "</span>" if n.get("exit_code") not in (None, 0) else ""}</li>'
            for n in unresolved[:6]
        )
        more = f'<li class="faint">+{len(unresolved) - 6} more</li>' if len(unresolved) > 6 else ""
        bands.append(f"""
<div class="band bad">
  <div class="band-h"><span class="glyph" aria-hidden="true">✕</span>
    {num(len(unresolved))} failure{'' if len(unresolved) == 1 else 's'} never resolved</div>
  <ul>{items}{more}</ul>
</div>""")

    unverified = [n for n in m["nodes"] if n["status"] == "unverified"]
    if unverified:
        names = ", ".join(f'#{n["index"]:02d} {esc(n["tool"])}' for n in unverified[:8])
        bands.append(f"""
<div class="band warn">
  <div class="band-h"><span class="glyph" aria-hidden="true">?</span>
    {num(len(unverified))} result{'' if len(unverified) == 1 else 's'} with no structured status</div>
  <div class="band-b">{names} — recorded as unverified rather than assumed successful.</div>
</div>""")

    unterminated = [n for n in m["nodes"] if n["status"] == "unterminated"]
    if unterminated:
        names = ", ".join(f'#{n["index"]:02d} {esc(n["tool"])}' for n in unterminated[:8])
        bands.append(f"""
<div class="band warn">
  <div class="band-h"><span class="glyph" aria-hidden="true">…</span>
    {num(len(unterminated))} call{'' if len(unterminated) == 1 else 's'} never returned</div>
  <div class="band-b">{names} — the session ended before a result was recorded.</div>
</div>""")

    return "".join(bands)


def _clip_text(text: str, limit: int) -> str:
    text = text or ""
    return text if len(text) <= limit else text[: limit - 1] + "…"



def _ask_block(payload: dict, label: str = "the ask") -> str:
    text = payload.get("text") or ""
    head = text if len(text) <= 700 else text[:700].rstrip() + "…"
    extra = ""
    if len(text) > 700:
        extra = (
            '<details class="more"><summary>full prompt · '
            f'{num(payload.get("chars"))} chars</summary><pre>{esc(text)}</pre></details>'
        )
    return f"""
<div class="ev-ask">
  <div class="who">{esc(label)} · {esc(clock(payload.get('timestamp')))}</div>
  <pre>{esc(head)}</pre>
  {extra}
</div>"""


def _answer_block(payload: dict) -> str:
    text = payload.get("text") or ""
    head = text if len(text) <= 500 else text[:500].rstrip() + "…"
    extra = ""
    if len(text) > 500:
        extra = f'<details class="more"><summary>full reply</summary><pre>{esc(text)}</pre></details>'
    return f"""
<div class="ev-answer">
  <div class="who">agent reply · {esc(clock(payload.get('timestamp')))}</div>{esc(head)}{extra}
</div>"""


_STATUS_CHIP = {
    "ok": '<span class="chip ok">ok</span>',
    "failed": '<span class="chip failed">failed</span>',
    "unverified": '<span class="chip unverified">unverified</span>',
    "unterminated": '<span class="chip unterminated">no result</span>',
}


def _node_html(node: dict, max_dur: float) -> str:
    status = node["status"]
    width = 0.0
    if node["duration_s"] is not None and max_dur > 0:
        width = min(100.0, max(2.0, node["duration_s"] / max_dur * 100.0))

    chips = [_STATUS_CHIP.get(status, "")]
    if node["retry_of"] is not None:
        chips.append(
            f'<span class="chip retry">retry · attempt {node["attempt"]} of {node["chain_length"]}</span>'
        )
    elif node["chain_length"] > 1 and node["retried_by"] is not None:
        chips.append(f'<span class="chip retry">retried ×{node["chain_length"] - 1}</span>')
    if node.get("chain_recovered") is True:
        chips.append('<span class="chip ok">recovered</span>')
    elif node.get("chain_recovered") is False:
        chips.append('<span class="chip failed">never recovered</span>')
    if node.get("repeat_of") is not None and node["retry_of"] is None:
        chips.append(f'<span class="chip par">repeat of #{node["repeat_of"]:02d}</span>')
    if node.get("parallel_group"):
        chips.append(f'<span class="chip par">×{node["parallel_group"]} parallel</span>')
    if node.get("exit_code") not in (None, 0):
        chips.append(f'<span class="chip failed">exit {node["exit_code"]}</span>')
    if node.get("orphan"):
        chips.append('<span class="chip par">orphan result</span>')

    err_html = ""
    if status == "failed" and node.get("error"):
        klass = f' · {esc(node["failure_class"])}' if node.get("failure_class") else ""
        err_html = (
            f'<div class="err"><span class="lbl">failure{klass}</span>{esc(node["error"])}</div>'
        )
    elif status == "unterminated":
        err_html = (
            '<div class="err"><span class="lbl">no result</span>'
            "the session ended before this call returned</div>"
        )

    res_html = ""
    if node.get("result_summary") and status != "failed":
        res_html = f'<div class="res">{esc(node["result_summary"])}</div>'

    detail = ""
    if node.get("args_detail") or node.get("result_excerpt"):
        args = node.get("args_detail") or ""
        excerpt = node.get("result_excerpt") or ""
        pre = []
        if args:
            pre.append("── arguments ──\n" + esc(args))
        if excerpt:
            pre.append("── result excerpt · " + num(node.get("result_chars")) + " chars ──\n" + esc(excerpt))
        joined = "\n\n".join(pre)
        detail = f'<details><summary>raw</summary><pre>{joined}</pre></details>'

    dur_txt = dur(node["duration_s"])
    par_note = ""
    if node.get("parallel_group"):
        par_note = f'<span class="par">parallel · dispatch #{node.get("index"):02d}</span>'

    return f"""
<div class="node {status}" data-node data-status="{status}" data-dur="{node['duration_s'] if node['duration_s'] is not None else -1}" data-retry="{1 if node['retry_of'] is not None else 0}">
  <div class="row">
    <div class="idx">#{node['index']:02d}</div>
    <div class="tool">{esc(node['tool'])}{par_note}</div>
    <div class="meta">{dur_txt} {' '.join(chips)}</div>
  </div>
  {f'<div class="args"><code>{esc(node["args_summary"])}</code></div>' if node.get('args_summary') else ''}
  <div class="bar"><i style="width:{width:.2f}%"></i></div>
  {err_html}
  {res_html}
  {detail}
</div>"""


def _timeline(m: dict) -> str:
    nodes = [n for n in m["nodes"]]
    if not nodes:
        return '<section><h3>timeline</h3><div class="ask"><pre>No tool calls in this session.</pre></div></section>'

    max_dur = max((n["duration_s"] or 0) for n in nodes) or 1.0
    parts = []
    per_session = {s["id"]: s for s in m.get("sessions", [])}

    if m.get("multi"):
        cursor = 0
        for s in m.get("per_session", []):
            chunk = nodes[cursor:cursor + s["tool_calls"]]
            cursor += s["tool_calls"]
            parts.append(
                f'<div class="sess"><div class="hd"><span class="t">{esc(s["title"])}</span>'
                f'<span class="faint">{esc(s["short_id"])} · {num(s["tool_calls"])} calls · '
                f'{num(s["failures"])} failures · {dur(_span(s))}</span></div>'
            )
            body = "".join(_node_html(n, max_dur) for n in chunk)
            parts.append(f'<div class="bd">{body}</div></div>')
    else:
        meta = m["sessions"][0] if m.get("sessions") else {}
        events = meta.get("timeline") or []
        if events:
            for ev in events:
                kind = ev["kind"]
                if kind == "ask":
                    parts.append(_ask_block(ev["payload"], "the ask"))
                elif kind == "answer":
                    parts.append(_answer_block(ev["payload"]))
                elif kind == "tool":
                    parts.append(_node_html(ev["payload"], max_dur))
        else:
            parts.extend(_node_html(n, max_dur) for n in nodes)

    controls = f"""
<div class="controls">
  <button class="f on" data-filter="all" type="button">all {num(len(nodes))}</button>
  <button class="f" data-filter="fail" type="button">failures {num(m['aggregates']['failures'])}</button>
  <button class="f" data-filter="slow" type="button">slow ≥5s</button>
  <button class="f" data-filter="retry" type="button">retries {num(m['aggregates']['retries'])}</button>
  <button class="f jump" type="button">↓ jump to first failure</button>
</div>"""

    return f"<section><h3>timeline</h3>{controls}<div class=\"tl\">{''.join(parts)}</div></section>"


def _span(s: dict) -> float:
    try:
        return max(0.0, float(s.get("ended_at") or 0) - float(s.get("started_at") or 0))
    except (TypeError, ValueError):
        return 0.0


def _flow(m: dict) -> str:
    flow = m["aggregates"]["flow"]
    if not flow:
        return ""
    total_fail = m["aggregates"]["failures"]
    items = []
    for i, step in enumerate(flow):
        cls = "fnode hasfail" if step["failures"] else "fnode"
        badge = f'<span class="fb">{step["failures"]}</span>' if step["failures"] else ""
        count = f'×{step["count"]}' if step["count"] > 1 else "1 call"
        secs = dur(step["seconds"]) if step["seconds"] else ""
        items.append(
            f'<div class="{cls}">{badge}<span class="n">{esc(step["tool"])}</span>'
            f'<span class="c">{count}{" · " + secs if secs else ""}</span></div>'
        )
        if i < len(flow) - 1:
            items.append('<span class="arrow">→</span>')

    return f"""
<section><h3>work flow</h3>
<div class="flowbox">
  <div class="flow">{''.join(items)}</div>
  <div class="legend">
    <span><i style="background:var(--line2)"></i>tool run</span>
    <span><i style="background:var(--red)"></i>run containing a failure{'' if not total_fail else f' · {total_fail} total'}</span>
    <span class="faint">consecutive calls to the same tool are grouped</span>
  </div>
</div></section>"""


def _spend(m: dict) -> str:
    usage = m["usage"]
    recon = m["reconciliation"]

    if not usage:
        return """
<section><h3>spend</h3>
<div class="recon"><div class="line"><span class="dim">No per-model usage rows recorded for this session.</span>
<span class="faint">nothing to reconcile</span></div>
<div class="note">The store keeps token and cost accounting in <code>session_model_usage</code>. A session written by an older
Hermes build, or one that never completed an API call, can be missing these rows. Receipts reports what is there rather than
estimating a number.</div></div></section>"""

    rows = []
    for u in usage:
        rows.append(
            f"<tr><td>{esc(u['model'])}</td><td>{esc(u['provider'] or '—')}</td>"
            f"<td class='n'>{num(u['api_calls'])}</td>"
            f"<td class='n'>{num(u['input'])}</td><td class='n'>{num(u['output'])}</td>"
            f"<td class='n'>{num(u['cache_read'])}</td><td class='n'>{num(u['reasoning'])}</td>"
            f"<td class='n'>{money(u['estimated_cost'], u.get('cost_status'))}</td>"
            f"<td class='faint'>{esc(u.get('cost_source') or '—')}</td></tr>"
        )
    totals = m["aggregates"]["usage"]
    rows.append(
        f"<tr class='total'><td>total</td><td></td><td class='n'>{num(totals['api_calls'])}</td>"
        f"<td class='n'>{num(totals['input'])}</td><td class='n'>{num(totals['output'])}</td>"
        f"<td class='n'>{num(totals['cache_read'])}</td><td class='n'>{num(totals['reasoning'])}</td>"
        f"<td class='n'>{money(recon['usage_cost'])}</td><td></td></tr>"
    )

    return f"""
<section><h3>spend · per model</h3>
<div class="tbl"><table>
<thead><tr><th>model</th><th>provider</th><th class="n">calls</th><th class="n">input</th>
<th class="n">output</th><th class="n">cache read</th><th class="n">reasoning</th>
<th class="n">est. cost</th><th>cost source</th></tr></thead>
<tbody>{''.join(rows)}</tbody></table></div></section>"""


def _reconciliation(m: dict) -> str:
    recon = m["reconciliation"]
    lines = []

    for check in recon["checks"]:
        delta = check["delta"]
        unit = check["unit"]
        if unit == "USD":
            recorded = money(check["recorded"])
            derived = money(check["derived"])
            dtext = money(abs(delta)) if abs(delta) >= 0.0000005 else "0"
        else:
            recorded = num(check["recorded"])
            derived = num(check["derived"])
            dtext = num(abs(delta))
        ok = abs(delta) < (0.0005 if unit == "USD" else 1)
        cls = "ok" if ok else "drift"
        mark = "✓" if ok else "!"
        lines.append(
            f'<div class="line"><span>{esc(check["name"])} <span class="faint">· {esc(check["note"])}</span></span>'
            f'<span class="{cls}">{derived} derived / {recorded} recorded · Δ{dtext} {mark}</span></div>'
        )

    status_note = []
    if recon.get("cost_status"):
        status_note.append(f"cost status <b>{esc(recon['cost_status'])}</b>")
    if recon.get("cost_source"):
        status_note.append(f"source <b>{esc(recon['cost_source'])}</b>")
    if recon.get("pricing_version"):
        status_note.append(f"pricing <b>{esc(recon['pricing_version'])}</b>")

    return f"""
<section><h3>reconciliation</h3>
<div class="recon">
  {''.join(lines)}
  <div class="note">Costs are the figures Hermes recorded at call time, not a price table applied later — Receipts does not
  re-price a session. The delta above is the artifact's own arithmetic against the session row; a non-zero delta is shown
  rather than smoothed away. {' · '.join(status_note)}</div>
</div></section>"""


def _provenance(m: dict) -> str:
    src = m["source"]
    sess = m["sessions"][0] if m.get("sessions") else {}
    size = src.get("size_bytes")
    size_txt = f"{size / 1_048_576:.1f} MB" if isinstance(size, int) else "—"
    ids = ", ".join(s["short_id"] for s in m.get("per_session", []))
    return f"""
<footer>
  <div class="grid">
    <div>
      <div class="kv"><span class="k">store</span><span class="v">{esc(src.get('path'))}</span></div>
      <div class="kv"><span class="k">size</span><span class="v">{esc(size_txt)}</span></div>
      <div class="kv"><span class="k">written</span><span class="v">{esc(clock(src.get('mtime')))}</span></div>
    </div>
    <div>
      <div class="kv"><span class="k">session</span><span class="v">{esc(ids)}</span></div>
      <div class="kv"><span class="k">source</span><span class="v">{esc(sess.get('source') or '—')}</span></div>
      <div class="kv"><span class="k">cwd</span><span class="v">{esc(sess.get('cwd') or '—')}</span></div>
    </div>
    <div>
      <div class="kv"><span class="k">generated</span><span class="v">{esc(m['generated_at'])}</span></div>
      <div class="kv"><span class="k">schema</span><span class="v">v{esc(m['schema_version'])} / receipts {esc(__version__)}</span></div>
      <div class="kv"><span class="k">target</span><span class="v">{esc(m['target'])}</span></div>
    </div>
  </div>
  <div class="stamp">
    Rendered offline from the local session record · no network calls · credential-shaped strings redacted ·
    failures are reported as recorded, including retries that were later recovered
  </div>
</footer>"""


# --------------------------------------------------------------------------- #
# public
# --------------------------------------------------------------------------- #


def render_html(manifest: dict) -> str:
    """Return the complete standalone document."""
    body = "".join([
        _masthead(manifest),
        _hero(manifest),
        f'<section><h3>the ask</h3>{_ask_block(manifest["asks"][0]) if manifest.get("asks") else "<div class=\'ask\'><pre>No user message recorded.</pre></div>"}</section>',
        _timeline(manifest),
        _flow(manifest),
        _spend(manifest),
        _reconciliation(manifest),
        _provenance(manifest),
    ])

    title = manifest.get("title") or "Receipts"
    return (
        "<!doctype html>\n"
        '<html lang="en"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1">'
        f"<title>Receipts · {esc(title)}</title>"
        f"<style>{_CSS}</style></head><body><div class=\"wrap\">{body}</div>"
        f"<script>{_JS}</script></body></html>\n"
    )


def render_document(title: str, body: str, extra_css: str = "") -> str:
    """Wrap an arbitrary body in the same instrument-panel document shell.

    Used by the rollup digest so it shares the receipt's visual language (and
    its offline/no-network guarantees) without duplicating 200 lines of CSS.
    No JavaScript is attached: a digest is a document, not a control surface.
    """
    return (
        "<!doctype html>\n"
        '<html lang="en"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1">'
        f"<title>Receipts · {esc(title)}</title>"
        f"<style>{_CSS}{extra_css}</style></head><body><div class=\"wrap\">{body}</div></body></html>\n"
    )


def render_json(manifest: dict) -> str:
    """The same manifest as JSON, for anyone who wants the data rather than the document."""
    return json.dumps(manifest, indent=2, ensure_ascii=False, default=str)
