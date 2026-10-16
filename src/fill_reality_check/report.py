"""Findings and report rendering (Markdown + self-contained HTML, no external assets)."""
from __future__ import annotations

import html
import json
import math
from pathlib import Path

import numpy as np
import pandas as pd

DISCLAIMER = ("This report is a technical review of a trade simulation. It is not investment advice, "
              "not a recommendation to trade any asset and not a forecast of returns. Simulated or past "
              "results do not guarantee future results.")
NOT_CHECKED = ["order book depth and market impact (fills assume your size trades at the bar prices)",
               "funding payments on perpetual futures", "exchange outages, rejected orders, partial fills",
               "anything that happens inside a 1-minute bar beyond its open/high/low/close",
               "your signal logic itself (only the fills and the statistics are checked)"]


def _f(x, nd=3, pct=False):
    if x is None or (isinstance(x, float) and (math.isnan(x))):
        return "n/a"
    if isinstance(x, float) and math.isinf(x):
        return "inf"
    s = f"{x:+.{nd}f}" if pct else f"{x:.{nd}f}"
    return s + (" %" if pct else "")


def findings(res: dict) -> list[dict]:
    t, p = res["trades"], res["trades"][res["trades"].in_stats]
    wf = {w["key"]: w for w in res["waterfall"]}
    out = []

    def add(code, cat, title, n, evidence, impact, col=None):
        out.append({"code": code, "category": cat, "title": title, "n": int(n), "evidence": evidence,
                    "impact": impact, "flag_column": col})

    n = int(t.get("entry_outside_range", pd.Series(dtype=bool)).fillna(False).sum())
    if n:
        add("C1", "certain", "Entry price outside the bar's high/low range", n,
            "The declared entry price is not inside [low, high] of the 1-minute bar at the entry time.",
            "Such fills cannot have happened at that minute (wrong timestamp, wrong timezone, or a price "
            "taken from another bar).", "entry_outside_range")
    n = int(t.get("exit_outside_range", pd.Series(dtype=bool)).fillna(False).sum())
    if n:
        add("C2", "certain", "Exit price outside the bar's high/low range", n,
            "The declared exit price is not inside [low, high] of the 1-minute bar at the exit time.",
            "Such fills cannot have happened at that minute.", "exit_outside_range")
    n = int(t.get("entry_before_signal_close", pd.Series(dtype=bool)).fillna(False).sum())
    if n:
        add("C3", "certain", "Entry before the signal candle closed (look-ahead)", n,
            "entry_time is earlier than signal_time + 1 minute and the entry price is not the signal bar's close: "
            "the order was filled before the bar that produced the signal had closed.", "The backtest used information it did not have yet.",
            "entry_before_signal_close")
    n = int(t.get("stop_fill_better_than_gap_open", pd.Series(dtype=bool)).fillna(False).sum())
    if n:
        add("C4", "certain", "Stop exits filled better than the bar's open", n,
            "Exit reason is a stop, but the exit bar opened beyond the declared fill: a resting stop "
            "would have filled at that open (gap) or earlier.", "Losses on gaps are understated.",
            "stop_fill_better_than_gap_open")
    match = res["convention_match"]
    changed = res["n_changed_research_vs_intrabar"]
    if match.get("research", 0) >= 0.5 and changed:
        add("C5", "certain", "Stops checked on closes but filled at the stop level", changed,
            f"Re-simulating your declared rule with stops checked on 1-minute closes and filled at the level "
            f"reproduces {100 * match['research']:.1f} % of your declared returns to 1e-6. "
            f"(Exit at the close: {100 * match['close']:.1f} %; resting stop: {100 * match['intrabar']:.1f} %.) "
            "That convention cannot be executed: a resting stop fires on intrabar wicks, and a rule that only "
            "looks at closes can only exit at the close.",
            f"PF {_f(wf['research']['pf'])} -> {_f(wf['close']['pf'])} (exit at the close) -> "
            f"{_f(wf['intrabar']['pf'])} (resting stop). Mean net per trade "
            f"{_f(wf['research']['mean_net_pct'], pct=True)} -> {_f(wf['close']['mean_net_pct'], pct=True)} -> "
            f"{_f(wf['intrabar']['mean_net_pct'], pct=True)}. {changed} trades change outcome.")
    n = int(t.get("stop_touched_before_exit", pd.Series(dtype=bool)).fillna(False).sum())
    if n:
        add("C6", "certain", "Stop level traded through before the declared exit", n,
            "The bar lows (highs for shorts) reached the declared stop level at least one minute before the "
            "declared exit, yet the position stayed open.",
            "If your stop is a resting order, these trades should have been stopped earlier.",
            "stop_touched_before_exit")
    op = res["one_position"]
    if op["n_overlapping"]:
        cat = "certain" if op["declared_one_position_per_symbol"] else "risk"
        imp = ""
        if "exec_kept" in op:
            imp = (f"Keeping only the first of overlapping trades ({op['kept_trades']} trades): declared PF "
                   f"{_f(op['declared_kept']['pf'])}, executable PF {_f(op['exec_kept']['pf'])}.")
        add("C7" if cat == "certain" else "R7", cat, "Overlapping positions on the same symbol",
            op["n_overlapping"],
            f"Trades open while an earlier trade on the same symbol is still open; up to "
            f"{op['max_concurrent_all_symbols']} positions open at once across all symbols."
            + (" Your rules say one position per symbol." if cat == "certain" else ""), imp,
            "overlap_same_symbol")
    for pr in res["problems"]:
        if pr["n"]:
            add("C8", "certain", f"Input problem: {pr['problem']}", pr["n"], "Rows excluded from the statistics.", "")
    bad = {k: v for k, v in res["statuses"].items() if k not in ("ok", "invalid_input")}
    if bad:
        add("C9", "certain", "Trades that could not be located on the bars", sum(bad.values()),
            ", ".join(f"{k}: {v}" for k, v in bad.items()), "Excluded from the statistics.")
    # look-ahead hints
    n = int(t.get("stamped_at_bar_open_filled_at_close", pd.Series(dtype=bool)).fillna(False).sum())
    if n:
        add("H1", "hint", "Entries stamped at a bar's open time but filled at its close", n,
            "The entry price equals the close of the bar whose open time is the entry time. If the signal "
            "uses that close, the backtest enters at the very price that created the signal.",
            f"Entering at the next bar's open instead: PF {_f(wf['intrabar']['pf'])} -> {_f(wf['exec']['pf'])}, "
            f"mean net {_f(wf['intrabar']['mean_net_pct'], pct=True)} -> {_f(wf['exec']['mean_net_pct'], pct=True)}.",
            "stamped_at_bar_open_filled_at_close")
    n = int(t.get("entry_at_signal_close", pd.Series(dtype=bool)).fillna(False).sum()) if t.signal_time.notna().any() else 0
    if n:
        add("H2", "hint", "Entries filled at the signal bar's close", n,
            "The entry price equals the close of the signal bar.", "Not executable if the signal needs that close.",
            "entry_at_signal_close")
    n = int(t.get("gap_in_window", pd.Series(dtype=bool)).fillna(False).sum())
    if n:
        add("H3", "hint", "Missing minutes inside the trade window", n,
            "The exchange data has a gap between the signal and the exit. Signals computed across a data gap "
            "are often artefacts; fills across a gap are uncertain.", "Included in the statistics, flagged.",
            "gap_in_window")
    # risks
    fee = res["fees"]
    if fee["below_base_taker"]:
        add("R1", "risk", "Declared fees below the public base-tier taker fee", 0,
            f"Declared {fee['declared_fee_per_side_pct']:.3f} % per side; Binance {fee['market']} base tier "
            f"(hard-coded, verify the current schedule): maker {fee['maker']:.3f} %, taker {fee['taker']:.3f} %.",
            "Market and stop orders pay taker fees unless you have a discount." +
            (" Your figure is even below the maker fee." if fee["below_base_maker"] else ""))
    return out


def _md_table(rows: list[list], head: list[str]) -> str:
    lines = ["| " + " | ".join(head) + " |", "|" + "|".join("---" for _ in head) + "|"]
    lines += ["| " + " | ".join(str(c) for c in r) + " |" for r in rows]
    return "\n".join(lines)


def _sections(res: dict, title: str) -> dict:
    """Shared content for both renderers."""
    r = res["rules"]
    wf = res["waterfall"]
    fd = findings(res)
    a, b = res["period"]
    period = f"{a:%Y-%m-%d} -> {b:%Y-%m-%d}" if a is not None else "n/a"
    decl, ex = wf[0], next(w for w in wf if w["key"] == "exec")
    verdict = (f"On these {res['n_in_stats']} trades, the profit factor goes from {_f(decl['pf'])} as declared to "
               f"{_f(ex['pf'])} when entries fill at the next bar's open and stops are resting orders "
               f"(round-trip cost {100 * r.cost_rt:.3f} %).")
    wf_rows = [[w["label"], w["n"], _f(w["pf"]), _f(w.get("mean_gross_pct"), pct=True),
                _f(w["mean_net_pct"], pct=True), _f(w["win_rate_pct"], 1)] for w in wf]
    lats = sorted(set(int(x) for x in r.latency_bars) | {0})
    grid = pd.DataFrame([g for g in res["grid"] if g.get("mode") is None])
    sens_rows = []
    for e in r.extra_cost_per_side_pct:
        row = [f"+{e:.2f} %"]
        for lat in lats:
            g = grid[(grid.extra_cost_per_side_pct == e) & (grid.latency_bars == lat)].iloc[0]
            row.append(f"{_f(g.pf, 2)} / {_f(g.mean_net_pct, 3, pct=True)}")
        sens_rows.append(row)
    st = res["stats"]
    risk_rows = []
    be = ex["breakeven_rt_cost_pct"]
    if be > 0:
        txt = (f"Executable mean gross {_f(be, pct=True)} per trade: the mean trade breaks even at a round-trip cost "
               f"of {_f(be)} % (you declared {100 * r.cost_rt:.3f} %).")
    else:
        txt = f"Executable mean gross {_f(be, pct=True)} per trade: negative before any cost, no cost level breaks even."
    risk_rows.append(["Cost break-even", txt])
    for key, name in (("bootstrap_declared", "declared"), ("bootstrap_exec", "executable")):
        bs = st[key]
        if "total_p05_pct" in bs:
            risk_rows.append([f"Block bootstrap ({name})",
                              f"{bs['samples']} resamples of {bs['block_days']}-day blocks over {bs['n_days']} days: "
                              f"sum of net returns p5 {_f(bs['total_p05_pct'], 1)} %, median {_f(bs['total_p50_pct'], 1)} %, "
                              f"p95 {_f(bs['total_p95_pct'], 1)} %; P(sum <= 0) = {bs['p_total_le_0']:.2f}; "
                              f"max drawdown median {_f(bs['maxdd_p50_pct'], 1)} %, p95 {_f(bs['maxdd_p95_pct'], 1)} % "
                              "(additive, one unit of notional per trade)."])
    for key, name in (("dsr_declared", "declared"), ("dsr_exec", "executable")):
        d = st[key]
        txt = (f"Per-trade Sharpe {_f(d.get('SR'), 4)} over {d.get('T')} trades (skew {_f(d.get('skew'), 2)}, "
               f"kurtosis {_f(d.get('kurt'), 1)}); PSR(0) = {_f(d.get('PSR0'), 3)}.")
        if "DSR" in d:
            txt += (f" With {d['n_trials']} trials, expected best-of-noise Sharpe {_f(d['SR0'], 4)}; "
                    f"Deflated Sharpe Ratio = {_f(d['DSR'], 3)}"
                    + (" (trial Sharpe variance assumed = 1/(T-1), i.e. trials differ only by noise: this is an "
                       "UPPER bound; real variant sets usually disperse more, which lowers the DSR. Pass "
                       "trials_sr_variance if you measured it)." if d["variance_assumed"] else "."))
        else:
            txt += " DSR not computed: give n_trials (number of variants you tried)."
        risk_rows.append([f"Sharpe statistics ({name})", txt])
    if "pbo" in st:
        pb = st["pbo"]
        risk_rows.append(["PBO (CSCV)", f"{pb['n_variants']} variants, S = {pb['S']} ({pb['n_combinations']} splits): "
                          f"PBO = {pb['PBO']:.2f}; P(out-of-sample Sharpe of the in-sample best < 0) = "
                          f"{pb['P_oos_sr_of_is_best_below_0']:.2f}."])
    for key, name in (("concentration_declared", "declared"), ("concentration_exec", "executable")):
        c = st[key]
        if c:
            risk_rows.append([f"Concentration ({name})",
                              f"Sum of net returns {_f(c['sum_pct'], 1)} %; without the best {c['n_top1pct']} trades "
                              f"(1 %): {_f(c['sum_without_top1pct_trades_pct'], 1)} %; without the best "
                              f"{c['n_top5pct']} (5 %): {_f(c['sum_without_top5pct_trades_pct'], 1)} %; without the "
                              f"5 best days: {_f(c['sum_without_top5_days_pct'], 1)} %."])
    yr = [[y["year"], y["n"], _f(y["declared_mean_net_pct"], pct=True), _f(y["research_mean_net_pct"], pct=True),
           _f(y["intrabar_mean_net_pct"], pct=True), _f(y["exec_mean_net_pct"], pct=True)] for y in res["yearly"]]
    exit_rule = []
    if r.stop_pct:
        exit_rule.append(f"stop {r.stop_pct} %")
    if res["trades"].stop_d.notna().any():
        exit_rule.append("per-trade stop from the CSV")
    if r.target_pct or res["trades"].target_d.notna().any():
        exit_rule.append("take-profit")
    if r.trail_activation_pct is not None:
        exit_rule.append(f"trailing {r.trail_activation_pct} % activation / {r.trail_distance_pct} % distance")
    exit_rule.append(f"time exit after {r.max_bars} bars" if isinstance(r.max_bars, int)
                     else "time exit at each trade's declared holding time")
    return {"title": title, "verdict": verdict, "period": period, "findings": fd, "wf_rows": wf_rows,
            "sens_rows": sens_rows, "lats": lats, "risk_rows": risk_rows, "yearly": yr, "exit_rule": "; ".join(exit_rule)}


def render_markdown(res: dict, title: str = "Fill-Reality Check report") -> str:
    s = _sections(res, title)
    r = res["rules"]
    L = [f"# {title}", "",
         f"*fill-reality-check {res['version']} - {res['n_input']} trades read, {res['n_in_stats']} analysed - "
         f"period {s['period']} - {res.get('bars_source', 'Binance ' + r.market)} 1-minute bars - round-trip cost {100 * r.cost_rt:.3f} %*", "",
         "## Bottom line", "", s["verdict"], "",
         "## Same trades, different fill conventions", "",
         f"Declared exit rule: {s['exit_rule']}.", "",
         _md_table(s["wf_rows"], ["Simulation", "Trades", "PF", "Mean gross", "Mean net", "Win %"]), ""]
    for cat, head in (("certain", "Certain errors (provable from your data and public bars)"),
                      ("hint", "Look-ahead hints (facts that are errors if your signal uses that bar)"),
                      ("risk", "Estimated risks: fees and positions")):
        items = [f for f in s["findings"] if f["category"] == cat]
        L += [f"## {head}", ""]
        if not items:
            L += ["None found.", ""]
            continue
        L += [_md_table([[f["code"], f["title"], f["n"] or "", f["evidence"], f["impact"]] for f in items],
                        ["Code", "Finding", "Trades", "Evidence", "Impact"]), ""]
    L += ["## Estimated risks: statistics", "", _md_table(s["risk_rows"], ["Check", "Result"]), "",
          "## Cost and latency sensitivity (executable fills)", "",
          "Cells: PF / mean net per trade. Rows: extra cost per side on top of the declared cost.", "",
          _md_table(s["sens_rows"], ["Extra cost / side"] + [f"latency {k} bar(s)" for k in s["lats"]]), "",
          "## By year of entry (mean net per trade)", "",
          _md_table(s["yearly"], ["Year", "Trades", "Declared", "Close-checked at level", "Resting stop", "Executable"]), "",
          "## Not checked", ""] + [f"- {x}" for x in NOT_CHECKED] + [
          "", "## Method", "",
          "Bars are labelled by their open time. 'Executable' enters at the open of the bar after the signal bar "
          "(plus latency), tests stops and trailing stops as resting orders on each bar's low/high, fills a gap "
          "through the level at the open, arms the trailing level from the peak observed up to the previous bar, "
          "fills a take-profit only if the bar trades through it (never better than the target), lets the stop win "
          "when both are hit in one bar, and exits on time at the bar's close. Stop and target distances are kept "
          "from your declared entry price. Costs are subtracted per trade as a round-trip fraction.", "",
          "Flagged trades are listed in `flagged_trades.csv`.", "", f"*{DISCLAIMER}*", ""]
    return "\n".join(L)


# ------------------------------------------------------------------------------- HTML

CSS = """
:root{--bg:#fbfbfa;--fg:#1d1d1b;--muted:#6b6b66;--line:#e2e1dc;--card:#ffffff;--bad:#b4381f;--warn:#9a6a00;--ok:#2f6f4f;
--s1:#3b6fb6;--s2:#c0702a;--s3:#7a5aa6;--s4:#2f8f7f;--s5:#b0465e;--s6:#7d7d76}
@media (prefers-color-scheme:dark){:root:not([data-theme="light"]){--bg:#161615;--fg:#ecebe6;--muted:#a3a29b;--line:#34332f;
--card:#1f1f1d;--bad:#f08a6e;--warn:#e0b44c;--ok:#7cc4a0;--s1:#7fa8e8;--s2:#e89a55;--s3:#ad8fdb;--s4:#5cc2b0;--s5:#e57d95;--s6:#a3a29b}}
:root[data-theme="dark"]{--bg:#161615;--fg:#ecebe6;--muted:#a3a29b;--line:#34332f;--card:#1f1f1d;--bad:#f08a6e;--warn:#e0b44c;
--ok:#7cc4a0;--s1:#7fa8e8;--s2:#e89a55;--s3:#ad8fdb;--s4:#5cc2b0;--s5:#e57d95;--s6:#a3a29b}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--fg);font:15px/1.55 system-ui,-apple-system,"Segoe UI",sans-serif}
main{max-width:980px;margin:0 auto;padding:28px 16px 60px}h1{font-size:1.6rem;margin:0 0 4px}h2{font-size:1.15rem;margin:34px 0 10px}
.meta{color:var(--muted);font-size:.85rem}.verdict{background:var(--card);border:1px solid var(--line);border-left:4px solid var(--bad);
padding:14px 16px;border-radius:6px;font-size:1.05rem}.scroll{overflow-x:auto}table{border-collapse:collapse;width:100%;font-size:.88rem;background:var(--card)}
th,td{border-bottom:1px solid var(--line);padding:7px 9px;text-align:left;vertical-align:top}th{color:var(--muted);font-weight:600}
td.num{text-align:right;font-variant-numeric:tabular-nums;white-space:nowrap}.tag{display:inline-block;padding:1px 7px;border-radius:10px;font-size:.75rem;
font-weight:600;border:1px solid currentColor}.certain{color:var(--bad)}.hint{color:var(--warn)}.risk{color:var(--muted)}
svg text{fill:var(--muted);font-size:11px}.legend span{display:inline-block;margin-right:14px;font-size:.82rem;color:var(--muted)}
.legend i{display:inline-block;width:12px;height:3px;margin-right:5px;vertical-align:middle}footer{color:var(--muted);font-size:.8rem;margin-top:36px}
"""


def _esc(x) -> str:
    return html.escape(str(x))


def _html_table(rows, head, num_cols=()) -> str:
    h = "".join(f"<th>{_esc(c)}</th>" for c in head)
    body = []
    for r in rows:
        cells = []
        for j, c in enumerate(r):
            cls = ' class="num"' if j in num_cols else ""
            cells.append(f"<td{cls}>{c if isinstance(c, _Raw) else _esc(c)}</td>")
        body.append("<tr>" + "".join(cells) + "</tr>")
    return f'<div class="scroll"><table><thead><tr>{h}</tr></thead><tbody>{"".join(body)}</tbody></table></div>'


class _Raw(str):
    pass


def _line_chart(series: dict, width=940, height=260, ylab="", zero=True) -> str:
    """series: name -> (x array, y array, css var)."""
    xs = np.concatenate([np.asarray(v[0], float) for v in series.values()])
    ys = np.concatenate([np.asarray(v[1], float) for v in series.values()])
    ys = ys[np.isfinite(ys)]
    if not len(ys):
        return ""
    x0, x1 = float(xs.min()), float(xs.max()) or 1.0
    y0, y1 = float(min(ys.min(), 0 if zero else ys.min())), float(max(ys.max(), 0 if zero else ys.max()))
    if y1 == y0:
        y1 = y0 + 1
    pl, pr, pt, pb = 56, 12, 10, 26
    X = lambda v: pl + (v - x0) / ((x1 - x0) or 1) * (width - pl - pr)  # noqa: E731
    Y = lambda v: pt + (y1 - v) / (y1 - y0) * (height - pt - pb)  # noqa: E731
    parts = [f'<svg viewBox="0 0 {width} {height}" width="100%" role="img" aria-label="{_esc(ylab)}">']
    for k in range(5):
        v = y0 + (y1 - y0) * k / 4
        parts.append(f'<line x1="{pl}" x2="{width - pr}" y1="{Y(v):.1f}" y2="{Y(v):.1f}" stroke="var(--line)"/>'
                     f'<text x="{pl - 6}" y="{Y(v) + 4:.1f}" text-anchor="end">{v:.3g}</text>')
    if zero and y0 < 0 < y1:
        parts.append(f'<line x1="{pl}" x2="{width - pr}" y1="{Y(0):.1f}" y2="{Y(0):.1f}" stroke="var(--muted)" stroke-dasharray="3 3"/>')
    for name, (x, y, color) in series.items():
        x, y = np.asarray(x, float), np.asarray(y, float)
        if len(x) > 1500:  # thin long series for file size
            k = np.linspace(0, len(x) - 1, 1500).astype(int)
            x, y = x[k], y[k]
        pts = " ".join(f"{X(a):.1f},{Y(b):.1f}" for a, b in zip(x, y) if np.isfinite(b))
        parts.append(f'<polyline fill="none" stroke="var({color})" stroke-width="2" points="{pts}"><title>{_esc(name)}</title></polyline>')
    parts.append(f'<text x="{pl}" y="{height - 6}">{_esc(ylab)}</text></svg>')
    legend = "".join(f'<span><i style="background:var({c})"></i>{_esc(n)}</span>' for n, (_, _, c) in series.items())
    return "".join(parts) + f'<div class="legend">{legend}</div>'


def render_html(res: dict, title: str = "Fill-Reality Check report") -> str:
    s = _sections(res, title)
    r = res["rules"]
    p = res["trades"][res["trades"].in_stats].sort_values("entry_time")
    colors = {"declared": "--s1", "research": "--s6", "close": "--s2", "intrabar": "--s3", "exec_L0": "--s5"}
    names = {"declared": "Declared", "research": "Close-checked, filled at level", "close": "Exit at the close",
             "intrabar": "Resting stop", "exec_L0": "Executable"}
    col = {"declared": "declared_gross", "research": "research", "close": "close", "intrabar": "intrabar", "exec_L0": "exec_L0"}
    n = np.arange(1, len(p) + 1)
    eq = {names[k]: (n, 100 * np.cumsum((p[c] - p.cost_rt).to_numpy()), colors[k]) for k, c in col.items()}
    grid = pd.DataFrame([g for g in res["grid"] if g.get("mode") is None])
    lat_colors = ["--s1", "--s2", "--s3", "--s5", "--s4", "--s6"]
    sens = {f"latency {lat} bar(s)": (grid[grid.latency_bars == lat].extra_cost_per_side_pct.to_numpy(),
                                      grid[grid.latency_bars == lat].pf.to_numpy(), lat_colors[i % 6])
            for i, lat in enumerate(s["lats"])}
    H = [f"<!doctype html><html lang='en'><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'>"
         f"<title>{_esc(title)}</title><style>{CSS}</style></head><body><main>",
         f"<h1>{_esc(title)}</h1><div class='meta'>fill-reality-check {res['version']} &middot; {res['n_input']} trades read, "
         f"{res['n_in_stats']} analysed &middot; period {_esc(s['period'])} &middot; {res.get('bars_source', 'Binance ' + r.market)} 1-minute bars &middot; "
         f"round-trip cost {100 * r.cost_rt:.3f} %</div>",
         "<h2>Bottom line</h2>", f"<p class='verdict'>{_esc(s['verdict'])}</p>",
         "<h2>Same trades, different fill conventions</h2>", f"<p>Declared exit rule: {_esc(s['exit_rule'])}.</p>",
         _html_table(s["wf_rows"], ["Simulation", "Trades", "PF", "Mean gross", "Mean net", "Win %"], (1, 2, 3, 4, 5)),
         "<h2>Cumulative net return per trade, by convention</h2>",
         _line_chart(eq, ylab="sum of per-trade net returns (%), trades in entry order")]
    for cat, head in (("certain", "Certain errors"), ("hint", "Look-ahead hints"), ("risk", "Estimated risks: fees and positions")):
        items = [f for f in s["findings"] if f["category"] == cat]
        H.append(f"<h2>{_esc(head)}</h2>")
        if not items:
            H.append("<p>None found.</p>")
            continue
        rows = [[_Raw(f"<span class='tag {cat}'>{_esc(f['code'])}</span>"), f["title"], f["n"] or "", f["evidence"], f["impact"]]
                for f in items]
        H.append(_html_table(rows, ["Code", "Finding", "Trades", "Evidence", "Impact"], (2,)))
    H += ["<h2>Estimated risks: statistics</h2>", _html_table(s["risk_rows"], ["Check", "Result"]),
          "<h2>Cost and latency sensitivity (executable fills)</h2>",
          "<p class='meta'>PF against extra cost per side on top of the declared cost. Table cells: PF / mean net per trade.</p>",
          _line_chart(sens, height=220, ylab="profit factor vs extra cost per side (%)", zero=False),
          _html_table(s["sens_rows"], ["Extra cost / side"] + [f"latency {k} bar(s)" for k in s["lats"]],
                      tuple(range(1, len(s["lats"]) + 1))),
          "<h2>By year of entry (mean net per trade)</h2>",
          _html_table(s["yearly"], ["Year", "Trades", "Declared", "Close-checked at level", "Resting stop", "Executable"],
                      (1, 2, 3, 4, 5)),
          "<h2>Not checked</h2><ul>" + "".join(f"<li>{_esc(x)}</li>" for x in NOT_CHECKED) + "</ul>",
          f"<footer>{_esc(DISCLAIMER)} Flagged trades: flagged_trades.csv.</footer></main></body></html>"]
    return "".join(H)


def flagged_trades(res: dict) -> pd.DataFrame:
    t = res["trades"]
    flags = [c for c in ("entry_outside_range", "exit_outside_range", "entry_before_signal_close",
                         "stop_fill_better_than_gap_open", "stop_touched_before_exit", "overlap_same_symbol",
                         "stamped_at_bar_open_filled_at_close", "gap_in_window") if c in t]
    any_flag = t[flags].fillna(False).astype(bool).any(axis=1) | (t.status != "ok")
    cols = ["trade_id", "symbol", "side", "entry_time", "exit_time", "entry_price", "exit_price", "status", "problem"] + flags
    cols += [c for c in ("declared_gross", "research", "close", "intrabar", "exec_L0") if c in t]
    return t.loc[any_flag, cols]


def write_reports(res: dict, out_dir: str | Path, title: str) -> dict:
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    paths = {"md": out / "report.md", "html": out / "report.html", "csv": out / "flagged_trades.csv",
             "json": out / "summary.json"}
    paths["md"].write_text(render_markdown(res, title), encoding="utf-8")
    paths["html"].write_text(render_html(res, title), encoding="utf-8")
    flagged_trades(res).to_csv(paths["csv"], index=False)
    paths["json"].write_text(json.dumps(summary_dict(res), indent=2, default=str), encoding="utf-8")
    return paths


def summary_dict(res: dict) -> dict:
    """Machine-readable summary (no per-trade data)."""
    r = res["rules"]
    return {"version": res["version"], "n_input": res["n_input"], "n_in_stats": res["n_in_stats"],
            "period": [str(x) for x in res["period"]], "rules": {k: getattr(r, k) for k in r.__dataclass_fields__},
            "waterfall": res["waterfall"], "convention_match": res["convention_match"],
            "n_changed_research_vs_intrabar": res["n_changed_research_vs_intrabar"],
            "findings": [{k: v for k, v in f.items() if k != "flag_column"} for f in findings(res)],
            "one_position": res["one_position"], "stats": res["stats"], "yearly": res["yearly"],
            "fees": res["fees"], "statuses": res["statuses"], "sensitivity": res["grid"]}
