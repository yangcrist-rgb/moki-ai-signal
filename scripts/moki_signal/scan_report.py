from __future__ import annotations

from typing import Any


TREND_LABEL = {"bullish": "多头", "bearish": "空头", "range": "震荡", "insufficient-data": "数据不足"}
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


def render_markdown(result: dict[str, Any]) -> str:
    windows = ["5", "10", "20", "30", "50", "100"]
    regime = result["regime"]
    out = [
        f"# 美股指标扫描 {result['data_end']}",
        "",
        f"市场状态：**{regime['label']}**（四大指数多头 {regime['bullish_indices']} / 空头 {regime['bearish_indices']}）"
        + (f"；逆风：{'，'.join(regime['headwinds'])}" if regime["headwinds"] else ""),
        "",
        "## 1. 大盘与七巨头",
        "",
    ]
    out += _table(["代码", "名称", "收盘", "1日", "5日", "20日", "均线位置", "趋势"], _trend_rows(result["indices"], windows, True))
    out += [""]
    out += _table(["代码", "收盘", "1日", "5日", "20日", "均线位置", "趋势"], _trend_rows(result["magnificent_seven"], windows, False))

    out += ["", "## 2. 宏观", ""]
    rows = []
    for symbol, item in result["macro"].items():
        unit = "bp" if item["change_unit"] == "bp" else "%"
        last = f"{item['last']:.3f}%" if unit == "bp" else _num(item["last"])
        rows.append([item["name"], symbol, last, _signed(item["chg_1d"], unit), _signed(item["chg_5d"], unit), _signed(item["chg_20d"], unit)])
    out += _table(["指标", "代码", "最新", "1日", "5日", "20日"], rows)

    events = result["events"]
    if events.get("status") != "skipped":
        out += ["", f"## 3. 大市值财报（≥3000 亿美元，{events['session_date']} 交易日及前一交易日盘后）", ""]
        if events["today"]:
            rows = [
                [
                    row["ticker"],
                    _num(row["market_cap_b"], 0, "B"),
                    f"{row['date']} {row['session']}" + ("（预估）" if row["date_is_estimate"] else ""),
                    row["status"],
                    f"{_num(row.get('eps_forward'))} / {_num(row.get('eps_ttm'))}",
                    _num(row.get("implied_move_pct"), 2, "%"),
                    row.get("theme") or "-",
                    ", ".join(row.get("linked") or []) or "-",
                ]
                for row in events["today"]
            ]
            out += _table(["代码", "市值", "时间", "状态", "EPS 远期/TTM", "隐含波动", "影响主题", "联动标的"], rows)
            out += ["", "EPS 为 Yahoo 远期与 TTM 值，不是本季实际值；`reported-verify-actuals` 表示时间已过，需核实实际 EPS。"]
        else:
            out += ["本时段无符合条件的大市值财报。"]
        out += ["", "## 4. 未来 30 日事件", ""]
        if events["next_30d"]:
            rows = [[row["date"], row.get("ticker") or "-", row.get("type", ""), row.get("session") or "-", _num(row.get("market_cap_b"), 0, "B")] for row in events["next_30d"]]
            out += _table(["日期", "代码", "类型", "时段", "市值"], rows)
        else:
            out += ["无。"]
        out += ["", "投资者日需在 `events.manual` 中手动维护。"]

    out += ["", "## 5. 板块强弱（20 日排名）", ""]
    rows = []
    for sector in result["sectors"]:
        top = sector["members"][:2]
        leaders = "，".join(f"{item['ticker']} {_signed(item['ret_5d'])}" for item in top)
        rows.append([str(sector["rank"]), sector["name"], _signed(sector["score_20d"]), sector["score_basis"], f"{sector['breadth_above_sma20']}% / {sector['breadth_above_sma50']}%", leaders])
    out += _table(["排名", "板块", "20日", "口径", "广度 >SMA20/50", "5日领先"], rows)

    options = result["options"]
    if options.get("status") != "skipped":
        out += ["", "## 6. 期权异动与 GEX（近端 ≤30 DTE）", ""]
        rows = []
        for symbol, item in sorted(options["tickers"].items(), key=lambda kv: -kv[1]["volume"]["total"]):
            gex = item["gex"]
            anomaly = item["anomaly"]
            flag = anomaly["status"] if anomaly["status"] != "building-baseline" else f"building-baseline({anomaly['baseline_days']}/{anomaly['required_days']})"
            if anomaly.get("ratio") is not None:
                flag += f" x{anomaly['ratio']}"
            rows.append(
                [
                    symbol,
                    _num(item["spot"]),
                    f"{item['volume']['total']:,}",
                    _num(item["volume"]["put_call_ratio"]),
                    flag,
                    _num(gex.get("put_wall"), 1),
                    _num(gex.get("call_wall"), 1),
                    _num(gex.get("gamma_flip_strike"), 1),
                    gex.get("regime", "-"),
                ]
            )
        out += _table(["代码", "现价", "近端成交量", "P/C", "异动", "Put 墙", "Call 墙", "GEX 累计零点", "Gamma 状态"], rows)
        out += ["", f"无风险利率取 ^IRX：{options['risk_free_rate'] * 100:.2f}%。异动需至少 3 个历史快照、阈值为中位数 2 倍。"]

    if result["playbooks"]:
        out += ["", "## 7. 强势板块领先股：正股与期权策略", ""]
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
            rows.append([f"#{book['sector_rank']} {book['sector']}" if book.get("sector_rank") else book["sector"], book["ticker"], _num(book.get("spot")), _signed(book["ret_5d"]), TREND_LABEL.get(book["trend"], book["trend"]), plan["action"], levels])
        out += _table(["板块", "代码", "现价", "5日", "趋势", "正股动作", "关键价位"], rows)
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

    if result["warnings"]:
        out += ["", "## 数据警告", ""] + [f"- {item}" for item in result["warnings"][:30]]
    out += ["", f"> {result['disclaimer']}", ""]
    return "\n".join(out)
