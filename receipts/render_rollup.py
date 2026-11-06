"""Render a rollup payload as one self-contained HTML digest.

Same visual language as a receipt — the instrument strip, the section rules, the
reconciliation box — because the digest is a receipt for a *week*, not a
dashboard.  No network, no fonts, no JavaScript, no render clock: two runs over
the same closed window produce the same bytes.
"""

from __future__ import annotations

from .render import dur, esc, money, num, render_document

__all__ = ["render_rollup", "EXTRA_CSS"]

EXTRA_CSS = r"""
/* rollup-only */
.tbl{overflow-x:auto;overflow-y:hidden}
.mast-meta .digest{color:var(--faint);font-size:10px;letter-spacing:.04em}
.tbl td .sub{display:block;color:var(--faint);font-size:10.5px;margin-top:2px;line-height:1.45;
  white-space:normal;max-width:520px}
.tbl td.cls{color:var(--ink);font-weight:600;white-space:nowrap}
.tbl td.cls .refined{color:var(--faint);font-weight:400;font-size:10px;margin-left:6px}
.up{color:var(--red)}
.down{color:var(--green)}
.flat{color:var(--faint)}
.wow td.delta{white-space:nowrap}
"""


def _clip_text(text, limit: int) -> str:
    text = text or ""
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _days(value) -> str:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return "—"
    return f"{int(number)}-day" if number.is_integer() else f"{number:.1f}-day"


def _m(v) -> str:
    """Money with a real zero — a $0.00 row is information, not a missing value."""
    return money(v, "estimated")


def _pct(v) -> str:
    if v is None:
        return "—"
    return f"{v:.2f}%"


def _delta_class(delta, better_when: str) -> str:
    if delta is None or delta == 0:
        return "flat"
    improved = (delta < 0) if better_when == "lower" else (delta > 0) if better_when == "higher" else None
    if improved is None:
        return "flat"
    return "down" if improved else "up"


def _arrow(delta) -> str:
    if delta is None or delta == 0:
        return "→"
    return "↑" if delta > 0 else "↓"


def _mast(payload: dict) -> str:
    window = payload["window"]
    board = payload.get("board") or "all boards"
    digest = (payload.get("stability") or {}).get("digest", "")
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
      <div class="sub">Process digest · rollup</div>
    </div>
  </div>
  <div class="mast-meta">
    <div>window <b>{esc(window['label'])}</b> · {_days(window['days'])}</div>
    <div>scope <b>{esc(board)}</b> · as of <b>{esc(window['end'])}</b></div>
    <div class="digest">{esc(digest)}</div>
  </div>
</div>"""


def _hero(payload: dict) -> str:
    cur = payload["current"]
    prev = payload["previous"]
    cost = cur["cost"]
    recovered = (f"{cur['chains_recovered']}/{cur['retry_chains']} chains"
                 if cur["retry_chains"] else "no chains")

    cells = [
        ("sessions", f'{num(cur["sessions"])}<small> · prev {num(prev["sessions"])}</small>', ""),
        ("tool calls", f'{num(cur["tool_calls"])}<small> · {num(cur["distinct_tools"])} tools</small>', ""),
        ("failures", f'<span aria-hidden="true">✕</span> {num(cur["failures"])}'
                     f'<small> · {_pct(round(cur["failures"] / cur["tool_calls"] * 100, 1) if cur["tool_calls"] else 0)}</small>',
         "bad" if cur["failures"] else "good"),
        ("retries", f'<span aria-hidden="true">↻</span> {num(cur["retries"])}<small> · {esc(recovered)}</small>',
         "warn" if cur["retries"] else ""),
        ("unresolved", f'<span aria-hidden="true">!</span> {num(cur["unresolved"])}',
         "bad" if cur["unresolved"] else "good"),
        ("cost", _m(cost["usage_usd"]), ""),
    ]
    strip = "".join(
        f'<div class="cell {cls}"><div class="k">{esc(k)}</div><div class="v">{v}</div></div>'
        for k, v, cls in cells
    )
    return f"""
<h2 class="title">{esc(payload['window']['week'])} · {_days(payload['window']['days'])} rollup</h2>
<div class="subtitle">{esc(payload['headline'])} · previous window {esc(payload['window']['previous_week'])}
({esc(payload['window']['previous_label'])})</div>
<div class="strip">{strip}</div>
{_what_changed(payload)}"""


def _what_changed(payload: dict) -> str:
    """Name the movements instead of making the reader diff two tables."""
    moves = [d for d in payload["deltas"]
             if d["metric"] in ("failure rate", "failures", "retries", "unresolved failures",
                                "cost", "p95 tool duration")
             and d["delta"] not in (None, 0)]
    if not moves:
        return ('<div class="band"><div class="band-h"><span class="glyph">=</span>'
                'no measurable change against the previous window</div></div>')

    def phrase(d):
        kind = d.get("kind") or "count"
        if kind == "money":
            cur, prev = _m(d["current"]), _m(d["previous"])
        elif kind == "percent":
            cur, prev = _pct(d["current"]), _pct(d["previous"])
        elif kind == "duration":
            cur, prev = dur(d["current"]), dur(d["previous"])
        else:
            cur, prev = num(d["current"]), num(d["previous"])
        tail = f" ({d['delta_pct']:+.1f}%)" if d["delta_pct"] is not None else ""
        return (f'<li><b>{esc(d["metric"])}</b> {esc(prev)} → {esc(cur)}{esc(tail)} '
                f'<span class="dim">· {esc(d["unit"])}</span></li>')

    moved = "".join(phrase(d) for d in moves)
    bad = [d for d in moves if _delta_class(d["delta"], d["better_when"]) == "up"]
    cls = "warn" if bad else "good"
    head = "what moved against the previous window"
    return f"""
<div class="band {cls}">
  <div class="band-h"><span class="glyph" aria-hidden="true">↕</span>{esc(head)}</div>
  <ul>{moved}</ul>
  <div class="band-b faint">“up” in red means the direction is worse for the process, not merely larger.</div>
</div>"""


def _taxonomy(payload: dict) -> str:
    rows = payload["current"]["taxonomy"]
    if not rows:
        return """<section><h3>failure taxonomy</h3>
<div class="ask"><pre>No failed tool calls in this window. Nothing to classify.</pre></div></section>"""
    body = []
    for row in rows:
        tools = ", ".join(f'{esc(t["tool"])} ×{t["count"]}' for t in row["tools"][:4])
        if len(row["tools"]) > 4:
            tools += f' <span class="faint">+{len(row["tools"]) - 4}</span>'
        refined = (f'<span class="refined">refines {esc(row["refined_from"])}</span>'
                   if row.get("refined_from") else "")
        example = row.get("example") or {}
        ex = ""
        if example.get("text"):
            ex = (f' · e.g. <span class="faint">{esc(example["tool"])}: {esc(example["text"])}</span>')
        meta = (f'{tools or "—"} · first {esc(row["first_seen"] or "—")} · '
                f'last {esc(row["last_seen"] or "—")}{ex}')
        body.append(
            f'<tr><td class="cls">{esc(row["class"])}{refined}<span class="sub">{meta}</span></td>'
            f'<td class="n">{num(row["count"])}</td><td class="n">{_pct(row["share_pct"])}</td>'
            f'<td class="n">{num(row["retried"])}</td><td class="n">{num(row["recovered"])}</td>'
            f'<td class="n">{num(row["unresolved"])}</td></tr>'
        )
    return f"""
<section><h3>failure taxonomy · ranked by frequency</h3>
<div class="tbl"><table>
<thead><tr><th>class</th><th class="n">count</th><th class="n">share</th>
<th class="n">retried</th><th class="n">recovered</th><th class="n">unresolved</th></tr></thead>
<tbody>{''.join(body)}</tbody></table></div>
<div class="legend"><span class="faint">Classes come from ordered pattern rules over the recorded error text
(no model, no embedding). “refines” names the v1 receipt class the rollup class splits off.</span></div>
</section>"""


def _by_model(payload: dict) -> str:
    rows = payload["current"]["by_model"]
    if not rows:
        return """<section><h3>cost · by model</h3>
<div class="ask"><pre>No per-model usage rows in this window.</pre></div></section>"""
    body = []
    for r in rows:
        body.append(
            f'<tr><td>{esc(r["model"])}</td><td>{esc(", ".join(r["providers"]) or "—")}</td>'
            f'<td class="n">{num(r["api_calls"])}</td><td class="n">{num(r["input"])}</td>'
            f'<td class="n">{num(r["output"])}</td><td class="n">{num(r["cache_read"])}</td>'
            f'<td class="n">{num(r["reasoning"])}</td><td class="n">{_m(r["cost_usd"])}</td>'
            f'<td class="faint">{esc(r.get("cost_status") or "—")}</td></tr>'
        )
    totals = payload["current"]["cost"]
    body.append(
        f'<tr class="total"><td>total</td><td></td>'
        f'<td class="n">{num(sum(r["api_calls"] for r in rows))}</td>'
        f'<td class="n">{num(sum(r["input"] for r in rows))}</td>'
        f'<td class="n">{num(sum(r["output"] for r in rows))}</td>'
        f'<td class="n">{num(sum(r["cache_read"] for r in rows))}</td>'
        f'<td class="n">{num(sum(r["reasoning"] for r in rows))}</td>'
        f'<td class="n">{_m(totals["usage_usd"])}</td><td></td></tr>'
    )
    return f"""
<section><h3>cost · by model</h3>
<div class="tbl"><table>
<thead><tr><th>model</th><th>providers</th><th class="n">calls</th><th class="n">input</th>
<th class="n">output</th><th class="n">cache read</th><th class="n">reasoning</th>
<th class="n">cost</th><th>status</th></tr></thead>
<tbody>{''.join(body)}</tbody></table></div></section>"""


def _by_card(payload: dict, limit: int = 15) -> str:
    cards = [c for c in payload["current"]["by_card"] if c["card"] is not None]
    unattr = next((c for c in payload["current"]["by_card"] if c["card"] is None), None)
    if not cards:
        return """<section><h3>cost · by card</h3>
<div class="ask"><pre>No session in this window could be attributed to a kanban card.</pre></div></section>"""
    shown = cards[:limit]
    body = []
    for c in shown:
        body.append(
            f'<tr><td>{esc(c["card"])}</td><td>{esc(c["board"])}</td><td>{esc(c["status"] or "—")}</td>'
            f'<td>{esc(_clip_text(c["title"], 64))}</td>'
            f'<td class="n">{num(c["sessions"])}</td><td class="n">{num(c["tool_calls"])}</td>'
            f'<td class="n">{num(c["failures"])}</td><td class="n">{num(c["retries"])}</td>'
            f'<td class="n">{_m(c["cost_usd"])}</td></tr>'
        )
    if unattr and (unattr["sessions"] or unattr["cost_usd"]):
        body.append(
            f'<tr class="total"><td>unattributed</td><td>{esc(unattr["board"])}</td><td>—</td>'
            f'<td class="dim">sessions with no card in the transcript, title or parent chain</td>'
            f'<td class="n">{num(unattr["sessions"])}</td><td class="n">{num(unattr["tool_calls"])}</td>'
            f'<td class="n">{num(unattr["failures"])}</td><td class="n">{num(unattr["retries"])}</td>'
            f'<td class="n">{_m(unattr["cost_usd"])}</td></tr>'
        )
    more = (f'<div class="legend"><span class="faint">showing {len(shown)} of {len(cards)} cards by cost; '
            f'the JSON carries every card.</span></div>' if len(cards) > len(shown) else "")
    return f"""
<section><h3>cost · by card</h3>
<div class="tbl"><table>
<thead><tr><th>card</th><th>board</th><th>status</th><th>title</th><th class="n">sessions</th>
<th class="n">calls</th><th class="n">failures</th><th class="n">retries</th><th class="n">cost</th></tr></thead>
<tbody>{''.join(body)}</tbody></table></div>{more}</section>"""


def _reconciliation(payload: dict) -> str:
    cur = payload["current"]
    lines = []
    for check in cur["checks"]:
        unit = check["unit"]
        if unit == "USD":
            recorded, derived = _m(check["recorded"]), _m(check["derived"])
            dtext = _m(abs(check["delta"])) if abs(check["delta"]) >= 0.0000005 else "$0.00"
        else:
            recorded, derived = num(check["recorded"]), num(check["derived"])
            dtext = num(abs(check["delta"]))
        ok = abs(check["delta"]) < (0.0005 if unit == "USD" else 1)
        cls = "ok" if ok else "drift"
        mark = "✓" if ok else "!"
        lines.append(
            f'<div class="line"><span>{esc(check["name"])} '
            f'<span class="faint">· {esc(check["note"])}</span></span>'
            f'<span class="{cls}">{derived} derived / {recorded} recorded · '
            f'Δ{dtext} {mark}</span></div>'
        )

    cost = cur["cost"]
    notes = []
    if cost["sessions_without_usage"]:
        ids = ", ".join(cost["sessions_without_usage_ids"])
        notes.append(f'{num(cost["sessions_without_usage"])} session(s) have no usage rows '
                     f'({esc(ids)}) — their cost is unknown, not zero.')
    if cost["receipt_mismatch_count"]:
        first = cost["receipt_mismatches"][0]
        notes.append(f'{num(cost["receipt_mismatch_count"])} session(s) where the per-model rows and the '
                     f'session row disagree (largest: {esc(first["session"])} Δ{_m(first["delta"])}); '
                     'the delta is shown, never smoothed.')
    if not notes:
        notes.append("Every session in the window reconciles: per-model rows equal the session rows.")
    return f"""
<section><h3>reconciliation · cost to receipts</h3>
<div class="recon">
  {''.join(lines)}
  <div class="note">{' '.join(notes)} Costs are the figures Hermes recorded at call time; the rollup re-prices nothing.</div>
</div></section>"""


def _slow_tools(payload: dict) -> str:
    tools = payload["current"]["slow_tools"]
    excluded = payload["current"].get("slow_tools_excluded") or []
    if not tools:
        return """<section><h3>slowest tools · p50 / p95</h3>
<div class="ask"><pre>No tool has enough timed calls to rank in this window.</pre></div></section>"""
    body = "".join(
        f'<tr><td>{esc(t["tool"])}</td><td class="n">{num(t["calls"])}</td>'
        f'<td class="n">{num(t["failures"])}</td><td class="n">{num(t["timed_calls"])}</td>'
        f'<td class="n">{dur(t["p50_s"])}</td><td class="n">{dur(t["p95_s"])}</td>'
        f'<td class="n">{dur(t["max_s"])}</td><td class="n">{dur(t["total_s"])}</td></tr>'
        for t in tools
    )
    ex = ""
    if excluded:
        ex = ('<div class="legend"><span class="faint">excluded from ranking: '
              + "; ".join(f'{esc(t["tool"])} ({esc(t["reason"])})' for t in excluded[:6])
              + (" …" if len(excluded) > 6 else "") + "</span></div>")
    return f"""
<section><h3>slowest tools · p50 / p95</h3>
<div class="tbl"><table>
<thead><tr><th>tool</th><th class="n">calls</th><th class="n">failures</th><th class="n">timed</th>
<th class="n">p50</th><th class="n">p95</th><th class="n">max</th><th class="n">total</th></tr></thead>
<tbody>{body}</tbody></table></div>
<div class="legend"><span class="faint">Durations are wall-clock dispatch → result; parallel calls share a start
time; human-wait tools (clarify) are excluded. Percentiles use linear interpolation at (n−1)p over observed durations.</span></div>{ex}</section>"""


def _hotspots(payload: dict) -> str:
    rows = payload["current"]["retry_hotspots"]
    unattr = payload["current"]["unattributed_retries"]
    if not rows:
        return f"""<section><h3>retry hotspots · by card</h3>
<div class="ask"><pre>No card-scoped retries in this window
({num(unattr)} retry(ies) ran outside any card).</pre></div></section>"""
    body = "".join(
        f'<tr><td>{esc(r["card"])}</td><td>{esc(r["board"])}</td>'
        f'<td>{esc(_clip_text(r["title"], 64))}</td>'
        f'<td class="n">{num(r["retries"])}</td><td class="n">{num(r["failures"])}</td>'
        f'<td class="n">{num(r["recovered"])}</td><td class="n">{num(r["unresolved"])}</td>'
        f'<td class="n">{_m(r["cost_usd"])}</td></tr>'
        for r in rows[:12]
    )
    note = (f'<div class="legend"><span class="faint">{num(unattr)} retry(ies) were not attributable to a card '
            f'and are counted in the window totals, not in this table.</span></div>' if unattr else "")
    return f"""
<section><h3>retry hotspots · by card</h3>
<div class="tbl"><table>
<thead><tr><th>card</th><th>board</th><th>title</th><th class="n">retries</th>
<th class="n">failures</th><th class="n">chains recovered</th><th class="n">unresolved</th>
<th class="n">cost</th></tr></thead>
<tbody>{body}</tbody></table></div>{note}</section>"""


def _wow(payload: dict) -> str:
    rows = []
    for d in payload["deltas"]:
        cls = _delta_class(d["delta"], d["better_when"])
        kind = d.get("kind") or "count"
        if kind == "money":
            cur, prev = _m(d["current"]), _m(d["previous"])
            delta = _m(abs(d["delta"])) if d["delta"] is not None else "—"
        elif kind == "percent":
            cur, prev = _pct(d["current"]), _pct(d["previous"])
            delta = _pct(abs(d["delta"])) if d["delta"] is not None else "—"
        elif kind == "duration":
            cur, prev = dur(d["current"]), dur(d["previous"])
            delta = dur(abs(d["delta"])) if d["delta"] is not None else "—"
        else:
            cur, prev = num(d["current"]), num(d["previous"])
            delta = num(abs(d["delta"])) if d["delta"] is not None else "—"
        pct = f'{d["delta_pct"]:+.1f}%' if d["delta_pct"] is not None else "new"
        rows.append(
            f'<tr class="wow"><td>{esc(d["metric"])}</td>'
            f'<td class="n">{esc(cur)}</td><td class="n">{esc(prev)}</td>'
            f'<td class="n delta {cls}">{esc(_arrow(d["delta"]))} {esc(delta)} · {esc(pct)}</td>'
            f'<td class="faint">{esc(d["unit"])}</td></tr>'
        )
    return f"""
<section><h3>week over week · {esc(payload['window']['week'])} vs {esc(payload['window']['previous_week'])}</h3>
<div class="tbl"><table>
<thead><tr><th>metric</th><th class="n">current</th><th class="n">previous</th>
<th class="n delta">delta</th><th>unit</th></tr></thead>
<tbody>{''.join(rows)}</tbody></table></div></section>"""


def _provenance(payload: dict) -> str:
    src = payload["source"]
    size = src.get("size_bytes")
    if isinstance(size, int):
        size_txt = f"{size / 1_048_576:.1f} MB" if size >= 1_048_576 else f"{size / 1024:.0f} KB"
    else:
        size_txt = "—"
    boards = payload.get("boards") or []
    bad = [b for b in boards if b.get("error")]
    board_txt = f'{num(len(boards))} indexed' + (f', {len(bad)} unreadable' if bad else "")
    stability = payload.get("stability") or {}
    return f"""
<footer>
  <div class="grid">
    <div>
      <div class="kv"><span class="k">store</span><span class="v">{esc(src.get('path'))}</span></div>
      <div class="kv"><span class="k">size</span><span class="v">{esc(size_txt)}</span></div>
      <div class="kv"><span class="k">canon</span><span class="v">{esc(src.get('kanban_home') or '—')}</span></div>
    </div>
    <div>
      <div class="kv"><span class="k">window</span><span class="v">{esc(payload['window']['start'])} → {esc(payload['window']['end'])}</span></div>
      <div class="kv"><span class="k">previous</span><span class="v">{esc(payload['window']['previous_start'])} → {esc(payload['window']['previous_end'])}</span></div>
      <div class="kv"><span class="k">boards</span><span class="v">{esc(board_txt)}</span></div>
    </div>
    <div>
      <div class="kv"><span class="k">digest</span><span class="v">{esc(stability.get('digest') or '—')}</span></div>
      <div class="kv"><span class="k">schema</span><span class="v">rollup v{esc(payload['schema_version'])} / receipts {esc(payload['tool_version'])}</span></div>
      <div class="kv"><span class="k">scope</span><span class="v">{esc(payload.get('board') or 'all boards')}</span></div>
    </div>
  </div>
  <div class="stamp">
    Deterministic digest · no network calls · no model labels · UTC timestamps · same closed window → same bytes ·
    failures are reported as recorded, including retries that were later recovered
  </div>
</footer>"""


def render_rollup(payload: dict) -> str:
    """Return the complete standalone rollup document."""
    body = "".join([
        _mast(payload),
        _hero(payload),
        _taxonomy(payload),
        _by_model(payload),
        _by_card(payload),
        _reconciliation(payload),
        _slow_tools(payload),
        _hotspots(payload),
        _wow(payload),
        _provenance(payload),
    ])
    return render_document(f"Rollup · {payload['window']['week']}", body, EXTRA_CSS)
