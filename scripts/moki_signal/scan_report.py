from __future__ import annotations

from typing import Any


TREND_LABEL = {"bullish": "多头", "bearish": "空头", "range": "震荡", "insufficient-data": "数据不足"}
ZH_LABEL = {
    "risk-on": "偏多", "risk-off": "偏空", "mixed": "分化",
    "positive-gamma": "正 Gamma", "negative-gamma": "负 Gamma",
    "transition": "过渡", "holding": "持仓", "watch": "观察",
    "earnings": "财报", "investor-day": "投资者日",
    "BMO": "盘前", "AMC": "盘后", "intraday/TBD": "时间待定",
    "FOMC-minutes": "FOMC 纪要", "NFP": "非农",
}


def zh(value: Any) -> str:
    if value is None:
        return "-"
    text = str(value)
    return TREND_LABEL.get(text) or ZH_LABEL.get(text) or text.replace("equal-weight", "等权")


ACTION_LABEL = {"buy-call": "买 Call", "buy-put": "买 Put", "cash-secured-put": "现金担保卖 Put", "covered-call": "Covered Call"}


def _num(value: Any, digits: int = 2, suffix: str = "") -> str:
    if value is None:
        return "-"
    if isinstance(value, (int, float)):
        return f"{value:,.{digits}f}{suffix}"
    return str(value)


def _signed(value: Any, suffix: str = "%") -> str:
    if value is None:
        return "-"
    return f"{value:+.2f}{suffix}" if suffix == "%" else f"{value:+.1f}{suffix}"


def _table(headers: list[str], rows: list[list[str]]) -> list[str]:
    lines = ["| " + " | ".join(headers) + " |", "|" + "|".join("---" for _ in headers) + "|"]
    lines.extend("| " + " | ".join(row) + " |" for row in rows)
    return lines


def _trend_rows(items: dict[str, dict], windows: list[str], with_name: bool) -> list[list[str]]:
    rows = []
    for symbol, snap in items.items():
        marks = " ".join(("↑" if snap["above_sma"].get(window) else "↓") + window for window in windows)
        row = [symbol] + ([snap.get("name", "")] if with_name else [])
        row += [_num(snap["close"]), _signed(snap["ret_1d"]), _signed(snap["ret_5d"]), _signed(snap["ret_20d"]), marks, TREND_LABEL.get(snap["trend"], snap["trend"])]
        rows.append(row)
    return rows


def _anomaly_flag(anomaly: dict) -> str:
    if anomaly["status"] == "building-baseline":
        return f"积累基线({anomaly['baseline_days']}/{anomaly['required_days']})"
    flag = "**放量**" if anomaly["status"] == "spike" else anomaly["status"]
    return flag + (f" x{anomaly['ratio']}" if anomaly.get("ratio") is not None else "")


def _gex_flag(change: dict) -> str:
    if change["status"] == "changed":
        return "**" + "；".join(change["flags"]) + "**"
    return {"stable": "稳定", "no-prior-snapshot": "首个快照", "unavailable": "-", "chain-reset": "链已清零"}.get(change["status"], change["status"])


def _render_macro(result: dict[str, Any], out: list[str]) -> None:
    regime = result["regime"]
    out += ["## 1. 宏观环境（美债、原油）", ""]
    rows = []
    for symbol, item in result["macro"].items():
        unit = "bp" if item["change_unit"] == "bp" else "%"
        last = f"{item['last']:.3f}%" if unit == "bp" else _num(item["last"])
        rows.append([item["name"], symbol, last, _signed(item["chg_1d"], unit), _signed(item["chg_5d"], unit), _signed(item["chg_20d"], unit)])
    out += _table(["指标", "代码", "最新", "1日", "5日", "20日"], rows)
    out += ["", "宏观逆风：" + ("，".join(regime["headwinds"]) if regime["headwinds"] else "无（10Y 20日上行 <25bp，原油 20日涨幅 <10%）")]


def _render_market(result: dict[str, Any], windows: list[str], out: list[str]) -> None:
    regime = result["regime"]
    out += ["", "## 2. 大盘（三大指数 + SOXX + IGV）", ""]
    out += _table(["代码", "名称", "收盘", "1日", "5日", "20日", "均线位置", "趋势"], _trend_rows(result["indices"], windows, True))
    out += [
        "",
        f"市场状态：**{zh(regime['label'])}**（三大指数多头 {regime['bullish_indices']}/{regime.get('index_count', 3)}，"
        f"空头 {regime['bearish_indices']}/{regime.get('index_count', 3)}）",
    ]


def _render_events(result: dict[str, Any], out: list[str]) -> None:
    events = result["events"]
    out += ["", "## 3. 重大事件与大市值财报（≥3000 亿美元）", ""]
    if events.get("status") == "skipped":
        out += ["已跳过（--skip-events）。"]
        return
    macro = events.get("macro", {"recent": [], "upcoming": []})
    out += ["### 宏观事件", ""]
    rows = [
        [row["date"], row.get("time_et") or "-", zh(row.get("type", "")), row.get("name", ""), "已发生" if row["days_from_as_of"] <= 0 else f"{row['days_from_as_of']} 天后"]
        for row in macro["recent"] + macro["upcoming"]
    ]
    out += _table(["日期", "时间(ET)", "类型", "事件", "距今"], rows) if rows else ["近期无已登记的宏观事件（在 `events.macro` 维护）。"]

    out += ["", f"### 财报（{events['session_date']} 交易日及前一交易日盘后）", ""]
    if events["today"]:
        rows = [
            [
                row["ticker"],
                _num(row["market_cap_b"], 0, "B"),
                f"{row['date']} {zh(row['session'])}" + ("（预估）" if row["date_is_estimate"] else ""),
                "已发布，需核实实际值" if row["status"] == "reported-verify-actuals" else "待发布",
                f"{_num(row.get('eps_forward'))} / {_num(row.get('eps_ttm'))}",
                _num(row.get("implied_move_pct"), 2, "%"),
                row.get("theme") or "-",
                ", ".join(row.get("linked") or []) or "-",
            ]
            for row in events["today"]
        ]
        out += _table(["代码", "市值", "时间", "状态", "EPS 远期/TTM", "隐含波动", "影响主题", "联动标的"], rows)
        out += ["", "EPS 为 Yahoo 远期与 TTM 值，不是本季实际值。"]
    else:
        out += ["本时段无符合条件的大市值财报。"]
    out += ["", f"### 未来 {events.get('lookahead_days', 30)} 日大市值财报 / 投资者日", ""]
    if events["next_30d"]:
        rows = [[row["date"], row.get("ticker") or "-", zh(row.get("type", "")), zh(row.get("session") or None), _num(row.get("market_cap_b"), 0, "B")] for row in events["next_30d"]]
        out += _table(["日期", "代码", "类型", "时段", "市值"], rows)
    else:
        out += ["无。"]


def _render_sectors(result: dict[str, Any], out: list[str]) -> None:
    look = result.get("lookbacks", {})
    leader_days, sector_days = look.get("leader_return_days", 5), look.get("sector_return_days", 20)
    out += ["", f"## 4. 细分板块与强势股（{sector_days} 日排名）", ""]
    rows = []
    for sector in result["sectors"]:
        strong = [item for item in sector["members"] if item.get("strong")]
        names = "，".join(f"{item['ticker']} {_signed(item['ret_leader'])}（{item['days_above_sma5']}日>MA5）" for item in strong[:4]) or "无"
        rows.append(
            [str(sector["rank"]), sector["name"], _signed(sector["score_20d"]), zh(sector["score_basis"]), f"{sector['breadth_above_sma20']}% / {sector['breadth_above_sma50']}%", f"{len(strong)}/{len(sector['members'])}", names]
        )
    out += _table(["排名", "板块", f"{sector_days}日", "口径", "广度 >SMA20/50", "强势数", f"强势股（{leader_days}日涨幅）"], rows)
    out += ["", "强势股标准：连续站稳 5 日线（≥2 个交易日收盘在 SMA5 上方）、收盘 > SMA20、SMA20 > SMA50，且短期涨幅为正。"]


def _render_options(result: dict[str, Any], out: list[str]) -> None:
    options = result["options"]
    out += ["", "## 5. 分板块期权异动与 GEX 异动（近端 ≤30 DTE）", ""]
    if options.get("status") == "skipped":
        out += ["已跳过（--skip-options）。"]
        return
    tickers = options["tickers"]
    anomalies = options.get("anomalies", [])
    reset = options.get("chain_reset", [])
    if reset:
        out += [f"> **数据状态：{len(reset)}/{len(tickers)} 只标的的期权链 OI/报价/IV 已被 Yahoo 清零（非常规交易时段）。** 成交量仍为上一交易日数据，GEX、Delta 与第 6 节合约需在美股常规时段（ET 09:30–16:00）重跑。", ""]
    if anomalies:
        out += ["**异动标的：** " + "，".join(anomalies), ""]
    else:
        out += ["本次无成交量 ≥2 倍基线或 GEX 显著变化的标的。", ""]
    for group in options.get("by_sector", [{"sector": "全部", "rank": None, "tickers": list(tickers)}]):
        title = f"#{group['rank']} {group['sector']}" if group.get("rank") else group["sector"]
        out += [f"### {title}", ""]
        rows = []
        for symbol in sorted(group["tickers"], key=lambda ticker: -tickers[ticker]["volume"]["total"]):
            item = tickers[symbol]
            gex = item["gex"]
            rows.append(
                [
                    symbol,
                    _num(item["spot"]),
                    f"{item['volume']['total']:,}",
                    _num(item["volume"]["put_call_ratio"]),
                    _anomaly_flag(item["anomaly"]),
                    _num(gex.get("put_wall"), 1),
                    _num(gex.get("call_wall"), 1),
                    _num(gex.get("gamma_flip_strike"), 1),
                    zh(gex.get("regime")),
                    _gex_flag(item.get("gex_change", {"status": "unavailable", "flags": []})),
                ]
            )
        out += _table(["代码", "现价", "近端成交量", "P/C", "成交异动", "Put 墙", "Call 墙", "GEX 累计零点", "Gamma 状态", "GEX 变化"], rows) + [""]
    out += [
        f"无风险利率取 ^IRX：{options['risk_free_rate'] * 100:.2f}%。成交异动需至少 3 个历史快照，阈值为中位数 2 倍；"
        "GEX 变化对比上一快照：Gamma 翻转、墙位移动 ≥3% 现价或净 GEX 变化 ≥50%。"
    ]


def _render_playbooks(result: dict[str, Any], out: list[str]) -> None:
    leader_days = result.get("lookbacks", {}).get("leader_return_days", 5)
    out += ["", "## 6. 可能的期权策略（强势板块的强势股 + 异动股）", ""]
    if not result["playbooks"]:
        out += ["无符合条件的强势股，或期权扫描已跳过。"]
        return
    rows = []
    for book in result["playbooks"]:
        plan = book["stock_plan"]
        if plan["bias"] == "偏多":
            levels = f"买区 {_num(plan['entry_zone'][0])}–{_num(plan['entry_zone'][1])}，跌破 {_num(plan['invalidate_below'])} 失效，目标 {_num(plan['target'])}" if plan.get("entry_zone") else "-"
        elif plan["bias"] == "偏空":
            levels = f"站回 {_num(plan['reclaim_level'])} 再看，支撑 {_num(plan['support'])}"
        else:
            rng = plan.get("range") or [None, None]
            levels = f"区间 {_num(rng[0])}–{_num(rng[1])}，中轴 SMA50 {_num(plan.get('pivot'))}"
        sector = f"#{book['sector_rank']} {book['sector']}" if book.get("sector_rank") else book["sector"]
        rows.append([sector, book["ticker"], _num(book.get("spot")), _signed(book["ret_leader"]), TREND_LABEL.get(book["trend"], book["trend"]), plan["action"], levels])
    out += _table(["板块", "代码", "现价", f"{leader_days}日", "趋势", "正股动作", "关键价位"], rows)
    out += [""]
    rows = []
    for book in result["playbooks"]:
        for style, legs in (("激进", book["aggressive"]), ("保守", book["conservative"])):
            for leg in legs:
                detail = []
                if "breakeven" in leg:
                    detail.append(f"盈亏平衡 {_num(leg['breakeven'])}")
                if "max_loss_per_contract" in leg:
                    detail.append(f"最大亏损 ${_num(leg['max_loss_per_contract'], 0)}/张")
                if "annualized_yield_pct" in leg:
                    detail.append(f"年化 {_num(leg['annualized_yield_pct'], 1)}%，保证金 ${_num(leg['collateral_per_contract'], 0)}")
                if "roll_lines" in leg:
                    detail.append("滚动线 " + " / ".join(f"Δ{key[-4:]}@{_num(value)}" for key, value in leg["roll_lines"].items()))
                if "static_yield_pct" in leg:
                    detail.append(f"静态收益 {_num(leg['static_yield_pct'])}%")
                if leg.get("earnings_before_expiry"):
                    detail.append(f"**到期前财报 {leg['earnings_before_expiry']}**")
                if leg.get("liquidity") == "thin":
                    detail.append("流动性偏薄")
                rows.append([book["ticker"], style, ACTION_LABEL.get(leg["action"], leg["action"]), f"{leg['expiry']} ({leg['dte']}D)", _num(leg["strike"]), _num(leg["mid"]), _num(leg["delta"]), _num(leg["iv_pct"], 1, "%"), f"{leg['open_interest']:,}", "；".join(detail)])
        if not book["aggressive"] and not book["conservative"]:
            rows.append([book["ticker"], "-", "；".join(book["notes"]) or "无合约", "-", "-", "-", "-", "-", "-", "-"])
    out += _table(["代码", "风格", "策略", "到期", "行权价", "中间价", "Delta", "IV", "OI", "说明"], rows)
    notes = sorted({note for book in result["playbooks"] for note in book["notes"]})
    if notes:
        out += ["", "备注：" + " ".join(notes)]
    out += ["", "滚动线：现金担保卖 Put 的 Put Delta 达到 0.30/0.35/0.40/0.50 时对应的正股价格（同 IV 估算），可作为提前滚动或止损参考。"]


def _render_holdings(result: dict[str, Any], out: list[str]) -> None:
    from .options_agent import render_agent_markdown

    out += ["", "## 7. 持仓检查与建议", ""]
    check = result.get("holdings_check")
    if not check:
        out += ["未加载持仓档案（使用 `--profile` 指定，或 `--skip-holdings` 已跳过）。"]
        return
    out += [render_agent_markdown(check, embedded=True).strip()]


def render_markdown(result: dict[str, Any]) -> str:
    windows = [key for key in next(iter(result["indices"].values()), {}).get("sma", {})] or ["5", "10", "20", "30", "50", "100"]
    out = [f"# 美股指标扫描 {result['data_end']}", ""]
    _render_macro(result, out)
    _render_market(result, windows, out)
    _render_events(result, out)
    _render_sectors(result, out)
    _render_options(result, out)
    _render_playbooks(result, out)
    _render_holdings(result, out)
    if result["warnings"]:
        out += ["", "## 数据警告", ""] + [f"- {item}" for item in result["warnings"][:30]]
    out += ["", f"> {result['disclaimer']}", ""]
    return "\n".join(out)
