"""Offline, SQL-backed descriptive analyses with explicit sampling validity."""
from __future__ import annotations

from collections import defaultdict
from contextlib import closing
from datetime import date
import heapq
from pathlib import Path
import sqlite3

from .data import DB_NAME, POLLUTANT_COLUMNS, load_dataset
from .standards import (annual_completeness, canonical_pollutant, get_standard,
                        monthly_completeness)

INTENTS = {"ranking", "exceedance", "trend", "seasonal", "anomaly"}


def _rounded(value, places=2):
    return round(value, places) if value is not None else None


def _names(value) -> list[str]:
    if not value:
        return []
    if isinstance(value, str):
        return [value]
    return [str(item) for item in value]


def _selection(plan: dict) -> tuple[str, list, dict]:
    year = plan.get("year")
    if year not in (None, ""):
        try:
            if isinstance(year, bool):
                raise ValueError
            year = int(year)
            if not 1 <= year <= 9999:
                raise ValueError
        except (TypeError, ValueError) as error:
            raise ValueError("year 必须是有效年份。") from error
    else:
        year = None
    months = plan.get("months") or []
    if isinstance(months, (str, int)):
        months = [months]
    try:
        if any(isinstance(month, bool) or int(month) != float(month) for month in months):
            raise ValueError
        months = sorted({int(month) for month in months})
        if any(month < 1 or month > 12 for month in months):
            raise ValueError
    except (TypeError, ValueError) as error:
        raise ValueError("months 只能包含1至12的月份。") from error
    group_by = plan.get("group_by") or "city"
    if group_by not in ("city", "province"):
        raise ValueError("group_by 只能是 city 或 province。")
    try:
        top_n = int(plan.get("top_n") or 5)
        if isinstance(plan.get("top_n"), bool) or top_n < 1:
            raise ValueError
    except (TypeError, ValueError) as error:
        raise ValueError("top_n 必须是正整数。") from error
    top_n = min(top_n, 100)
    cities, provinces = _names(plan.get("cities")), _names(plan.get("provinces"))
    clauses, parameters = [], []
    if year is not None:
        clauses.append("substr(date,1,4)=?")
        parameters.append(f"{year:04d}")
    if months:
        clauses.append("CAST(substr(date,6,2) AS INTEGER) IN (" + ",".join("?" for _ in months) + ")")
        parameters.extend(months)
    for column, names in (("city", cities), ("province", provinces)):
        if names:
            clauses.append(column + " IN (" + ",".join("?" for _ in names) + ")")
            parameters.extend(names)
    return " AND ".join(clauses) or "1=1", parameters, {
        "year": year, "months": months, "cities": cities, "provinces": provinces,
        "group_by": group_by, "top_n": top_n,
    }


def _table(columns: list[tuple[str, str]], rows: list[dict]) -> dict:
    return {"columns": [{"key": key, "label": label} for key, label in columns], "rows": rows}


def _chart(kind: str, title: str, unit: str, labels: list, series: list, standard=None) -> dict:
    chart = {"type": kind, "title": title, "unit": unit, "labels": labels, "series": series}
    if standard and standard.get("limit") is not None:
        chart.update({"threshold": standard["limit"], "threshold_label": standard["basis"] + "限值（历史口径）"})
    return chart


def _empty(pollutant: str, intent: str, meta: dict, context: dict, warnings: list) -> dict:
    basis = {"ranking": "annual", "trend": "monthly", "seasonal": "seasonal"}.get(intent, "daily")
    return {"title": f"{pollutant} 数据分析", "summary": "当前筛选条件下没有日记录。",
            "insight": "请根据数据覆盖范围调整年份、城市或省份。", "metrics": [],
            "table": _table([], []), "charts": [], "standard": get_standard(pollutant, basis),
            "warnings": warnings, "quality": dict(meta["quality"]), "context": context}


def _monthly(connection, where: str, parameters: list, column: str, group: str) -> list[dict]:
    # Only aggregates, never raw daily rows, are materialized for charts.
    query = (f'SELECT {group} AS name, substr(date,1,7) AS month, AVG("{column}") AS mean, '
             f'COUNT("{column}") AS valid_days, COUNT(*) AS records, COUNT(DISTINCT city) AS cities '
             f'FROM daily WHERE {where} GROUP BY {group}, substr(date,1,7) ORDER BY month,name')
    return [dict(row) for row in connection.execute(query, parameters)]


def _monthly_line(monthly: list[dict], names: list[str], pollutant: str, unit: str) -> dict:
    labels = sorted({row["month"] for row in monthly})
    lookup = {(row["name"], row["month"]): row["mean"] for row in monthly}
    return _chart("line", f"{pollutant} 月均变化", unit, labels,
                  [{"name": name, "values": [_rounded(lookup.get((name, month))) for month in labels]} for name in names])


def _rank(connection, where, parameters, options, pollutant, column, result):
    group, year = options["group_by"], options["year"]
    rows = [dict(row) for row in connection.execute(
        f'SELECT {group} AS name, AVG("{column}") AS mean, COUNT("{column}") AS valid_days, '
        f'COUNT(*) AS records, COUNT(DISTINCT city) AS city_count FROM daily WHERE {where} '
        f'GROUP BY {group} ORDER BY mean IS NULL,mean DESC,name LIMIT ?', [*parameters, options["top_n"]])]
    monthly = _monthly(connection, where, parameters, column, group)
    counts = defaultdict(dict)
    for row in monthly:
        if year is not None:
            counts[row["name"]][int(row["month"][5:7])] = row["valid_days"]
    standard = get_standard(pollutant, "annual")
    full_year = not options["months"] or options["months"] == list(range(1, 13))
    official_count = 0
    for index, row in enumerate(rows, 1):
        raw_mean = row["mean"]
        completeness = annual_completeness(year, counts[row["name"]], full_year) if year is not None and group == "city" else None
        official = bool(completeness and completeness["complete"] and standard["limit"] is not None)
        row.update({"rank": index, group: row["name"], "mean": _rounded(raw_mean),
                    "annual_complete": bool(completeness and completeness["complete"]),
                    "assessment": "无有效数据" if raw_mean is None else "样本期均值参考",
                    "exceeds_annual": None, "completeness": completeness})
        if official:
            row["exceeds_annual"] = raw_mean > standard["limit"]
            row["assessment"] = "年均超标" if row["exceeds_annual"] else "年均达标"
            official_count += 1
        elif raw_mean is not None and standard["limit"] is None:
            row["assessment"] = "样本期均值参考（无年均限值）"
    period = f"{year}年" if year else "所选时期"
    noun = "城市" if group == "city" else "省份"
    result.update({"title": f"{period}{pollutant}{noun}均值排名", "standard": standard,
                   "table": _table([("rank", "排名"), (group, noun), ("mean", "均值"),
                                     ("valid_days", "有效日数" if group == "city" else "有效城市日"),
                                     ("assessment", "年均有效性与判断")], rows)})
    if not rows or rows[0]["mean"] is None:
        result["summary"] = f"所选日记录没有有效的 {pollutant} 浓度，无法计算均值排名。"
        result["insight"] = "缺测记录不参与均值，也不视为达标。"
        return
    first = rows[0]
    result["summary"] = f"{first['name']}的{pollutant}均值最高，为 {first['mean']} {standard['unit']}，基于 {first['valid_days']} 个有效城市日。"
    result["insight"] = f"展示 {len(rows)} 个{noun}；其中 {official_count} 个满足正式年均浓度判断条件。"
    if group == "province":
        result["insight"] = "省份均值按有效城市日加权，城市有效日数较多时权重较大；此汇总不作省份年均达标判定。"
        result["warnings"].append("省份为有效城市日汇总，不是逐城正式年均值的等权平均，也不是法定省级达标评价。")
    if official_count < sum(row["mean"] is not None for row in rows):
        result["warnings"].append("未满足单年全年、至少324个有效日及每月至少27日（2月至少25日）的记录，仅作样本期均值参考；CO与O3_8h无年均限值。")
    result["metrics"].append({"label": "可作正式年均判断的城市", "value": official_count, "unit": "个"})
    # The annual threshold applies only when every displayed city is evaluable.
    bar_standard = standard if official_count == len(rows) and group == "city" else None
    result["charts"] = [_chart("bar", f"{pollutant} 均值排名", standard["unit"], [row["name"] for row in rows],
                               [{"name": pollutant, "values": [row["mean"] for row in rows]}], bar_standard),
                         _monthly_line(monthly, [row["name"] for row in rows], pollutant, standard["unit"])]


def _exceedance(connection, where, parameters, options, pollutant, column, result):
    standard = get_standard(pollutant, "daily")
    result["title"] = f"{pollutant} 城市日超标率"
    result["standard"] = standard
    if standard["limit"] is None:
        result["summary"] = f"{pollutant} 没有本赛事所采用浓度标准下的日限值，不能计算该口径超标率。"
        result["insight"] = "可改用有明确日浓度限值的污染物，或查看该指标趋势。"
        result["warnings"].append(standard["note"])
        return
    group = options["group_by"]
    rows = [dict(row) for row in connection.execute(
        f'SELECT {group} AS name,COUNT("{column}") AS valid_days,COUNT(*) AS records, '
        f'SUM(CASE WHEN "{column}">? THEN 1 ELSE 0 END) AS exceeded_days, '
        f'COUNT(DISTINCT CASE WHEN "{column}">? THEN date END) AS any_city_exceeded_dates, '
        f'COUNT(DISTINCT CASE WHEN "{column}" IS NOT NULL THEN date END) AS valid_dates '
        f'FROM daily WHERE {where} GROUP BY {group}', [standard["limit"], standard["limit"], *parameters])]
    for row in rows:
        row.update({group: row["name"], "rate": _rounded(100 * row["exceeded_days"] / row["valid_days"]) if row["valid_days"] else None,
                    "missing_days": row["records"] - row["valid_days"],
                    "assessment": "无有效数据" if not row["valid_days"] else "按有效城市日计算"})
    rows.sort(key=lambda row: (row["rate"] is None, -(row["rate"] or 0), row["name"]))
    rows = rows[:options["top_n"]]
    all_valid, all_exceeded = connection.execute(
        f'SELECT COUNT("{column}"),SUM(CASE WHEN "{column}">? THEN 1 ELSE 0 END) FROM daily WHERE {where}',
        [standard["limit"], *parameters]).fetchone()
    rate = _rounded(100 * (all_exceeded or 0) / all_valid) if all_valid else None
    noun = "城市" if group == "city" else "省份"
    columns = [(group, noun), ("valid_days", "有效城市日"), ("exceeded_days", "超标城市日"), ("rate", "城市日超标率（%）"), ("missing_days", "缺测城市日")]
    if group == "province":
        columns.append(("any_city_exceeded_dates", "至少1城超标的不同日期数"))
        result["warnings"].append("省份超标率分母为有效城市日；至少1城超标的不同日期数为另一指标，不能与城市日分母混用。")
    result["table"] = _table(columns, rows)
    if not all_valid:
        result["summary"] = f"所选日记录没有有效的 {pollutant} 浓度，超标率无法计算。"
        result["insight"] = "缺失值不计入超标率分母，也不被记为达标。"
        return
    result["summary"] = f"所选范围共 {all_valid} 个有效城市日，其中 {all_exceeded or 0} 个严格超过 {standard['limit']} {standard['unit']}，超标率为 {rate}%。"
    result["insight"] = "等于限值计为未超标；无有效值的城市日不进入分母。"
    result["metrics"].extend([{"label": "超标城市日", "value": all_exceeded or 0, "unit": "条"},
                               {"label": "城市日超标率", "value": rate, "unit": "%"}])
    result["charts"] = [_chart("bar", f"{pollutant} 城市日超标率", "%", [row["name"] for row in rows],
                               [{"name": "超标率", "values": [row["rate"] for row in rows]}]),
                         _chart("donut", "有效城市日构成", "条", ["超标", "未超标"],
                                [{"name": "城市日", "values": [all_exceeded or 0, all_valid - (all_exceeded or 0)]}])]


def _trend(connection, where, parameters, options, pollutant, column, result):
    group = options["group_by"]
    monthly = _monthly(connection, where, parameters, column, group)
    totals = defaultdict(lambda: [0.0, 0])
    for row in monthly:
        if row["mean"] is not None:
            totals[row["name"]][0] += row["mean"] * row["valid_days"]
            totals[row["name"]][1] += row["valid_days"]
    names = sorted(totals, key=lambda name: (-totals[name][0] / totals[name][1], name))[:options["top_n"]]
    rows = []
    for row in monthly:
        if row["name"] not in names:
            continue
        year, month = map(int, row["month"].split("-"))
        coverage = monthly_completeness(year, month, row["valid_days"]) if group == "city" else None
        rows.append({group: row["name"], "month": row["month"], "mean": _rounded(row["mean"]),
                     "valid_days": row["valid_days"], "complete": coverage["complete"] if coverage else None,
                     "required_days": coverage["required_days"] if coverage else None,
                     "calendar_days": coverage["calendar_days"] if coverage else None,
                     "coverage": "完整月" if coverage and coverage["complete"] else "不完整月（参考）" if coverage else "省级城市日汇总"})
    standard = get_standard(pollutant, "monthly")
    result.update({"title": f"{pollutant} 月度变化", "standard": standard,
                   "summary": f"按日记录计算月均值，展示 {len(names)} 个{'城市' if group == 'city' else '省份'}的 {len({row['month'] for row in rows})} 个月份。",
                   "insight": "月均值按有效日加权计算；每月有效日不少于27日、2月不少于25日才标记为完整月。月均值不作年均或日均达标判断。",
                   "table": _table([(group, "城市" if group == "city" else "省份"), ("month", "月份"),
                                     ("mean", "月均值"), ("valid_days", "有效日数" if group == "city" else "有效城市日"),
                                     ("coverage", "完整性")], rows),
                   "charts": [_monthly_line(monthly, names, pollutant, standard["unit"])] if names else []})
    if not names:
        result["summary"] = f"所选日记录没有有效的 {pollutant} 浓度，无法计算月度均值。"
    if group == "province":
        result["warnings"].append("省份月均按有效城市日加权；有效城市日总数不能代表每个城市的月完整性。")
    elif any(not row["complete"] for row in rows):
        result["warnings"].append("包含不完整月份，其均值仅作样本期参考。")


def _seasonal(connection, where, parameters, options, pollutant, column, result):
    group = options["group_by"]
    monthly = _monthly(connection, where, parameters, column, group)
    seasons = {3: "春季", 4: "春季", 5: "春季", 6: "夏季", 7: "夏季", 8: "夏季",
               9: "秋季", 10: "秋季", 11: "秋季", 12: "冬季", 1: "冬季", 2: "冬季"}
    order = {"春季": 0, "夏季": 1, "秋季": 2, "冬季": 3}
    buckets = {}
    totals = defaultdict(lambda: [0.0, 0])
    for row in monthly:
        year, month = map(int, row["month"].split("-"))
        season = seasons[month]
        key = (row["name"], year, season)
        bucket = buckets.setdefault(key, {"sum": 0.0, "valid_days": 0, "complete_months": 0, "observed_months": 0})
        bucket["observed_months"] += 1
        if group == "city" and monthly_completeness(year, month, row["valid_days"])["complete"]:
            bucket["complete_months"] += 1
        if row["mean"] is not None:
            bucket["sum"] += row["mean"] * row["valid_days"]
            bucket["valid_days"] += row["valid_days"]
            totals[row["name"]][0] += row["mean"] * row["valid_days"]
            totals[row["name"]][1] += row["valid_days"]
    names = sorted(totals, key=lambda name: (-totals[name][0] / totals[name][1], name))[:options["top_n"]]
    rows = []
    for (name, year, season), bucket in sorted(buckets.items(), key=lambda item: (item[0][1], order[item[0][2]], item[0][0])):
        if name in names:
            rows.append({group: name, "year": year, "season": season, "period": f"{year} {season}",
                         "mean": _rounded(bucket["sum"] / bucket["valid_days"]) if bucket["valid_days"] else None,
                         "valid_days": bucket["valid_days"], "complete": bucket["complete_months"] == 3 if group == "city" else None,
                         "coverage": ("3个月均完整" if bucket["complete_months"] == 3 else "季内月份不完整（参考）") if group == "city" else "省级城市日汇总"})
    standard = get_standard(pollutant, "seasonal")
    labels = list(dict.fromkeys(row["period"] for row in rows))
    lookup = {(row[group], row["period"]): row["mean"] for row in rows}
    result.update({"title": f"{pollutant} 季节对比", "standard": standard,
                   "summary": f"比较 {len(names)} 个{'城市' if group == 'city' else '省份'}的季节均值，均值按有效城市日加权。",
                   "insight": "春季3—5月、夏季6—8月、秋季9—11月；冬季使用所选日历年份的1、2、12月。季均值无对应达标限值。",
                   "table": _table([(group, "城市" if group == "city" else "省份"), ("year", "年份"), ("season", "季节"),
                                     ("mean", "季均值"), ("valid_days", "有效日数" if group == "city" else "有效城市日"), ("coverage", "完整性")], rows),
                   "charts": [_chart("bar", f"{pollutant} 季节均值", standard["unit"], labels,
                                      [{"name": name, "values": [lookup.get((name, label)) for label in labels]} for name in names])] if names else []})
    result["warnings"].append("冬季按同一日历年1、2、12月组合，不表示连续跨年的气象冬季。")
    if options["months"]:
        result["warnings"].append("仅使用筛选中的月份，季节均值可能不覆盖完整季节。")
    if not names:
        result["summary"] = f"所选日记录没有有效的 {pollutant} 浓度，无法计算季节均值。"


def _anomaly(connection, where, parameters, options, pollutant, column, result):
    # Calendar-adjacent observations only: a missing date never becomes a one-day jump.
    floor = 1.0 if pollutant == "CO" else 50.0 if pollutant == "AQI" else 20.0
    previous = None
    heap, count, pairs, serial = [], 0, 0, 0
    for row in connection.execute(f'SELECT date,city,province,"{column}" AS value FROM daily WHERE {where} AND "{column}" IS NOT NULL ORDER BY city,date', parameters):
        day = date.fromisoformat(row["date"])
        if previous and previous["city"] == row["city"] and (day - previous["day"]).days == 1:
            pairs += 1
            delta = row["value"] - previous["value"]
            if abs(delta) >= max(floor, abs(previous["value"]) * 0.5):
                count += 1
                serial += 1
                event = {"city": row["city"], "province": row["province"], "date": row["date"],
                         "previous_date": previous["date"], "previous_value": _rounded(previous["value"]),
                         "value": _rounded(row["value"]), "delta": _rounded(delta),
                         "percent_change": _rounded(100 * delta / previous["value"]) if previous["value"] else None,
                         "marker": "相邻日跳变（待复核）"}
                item = (abs(delta), serial, event)
                if len(heap) < options["top_n"]:
                    heapq.heappush(heap, item)
                elif item[:2] > heap[0][:2]:
                    heapq.heapreplace(heap, item)
        previous = {"city": row["city"], "day": day, "date": row["date"], "value": row["value"]}
    events = [item[2] for item in sorted(heap, reverse=True)]
    standard = get_standard(pollutant, "daily")
    result.update({"title": f"{pollutant} 相邻日跳变复核", "standard": standard,
                   "summary": f"检查 {pairs} 对同城相邻有效日，标记 {count} 次跳变，展示幅度最大的 {len(events)} 次。",
                   "insight": f"标记规则：绝对变化量至少为 max({floor:g} {standard['unit']}, 前一日浓度的50%)；只作复核线索，浓度峰值仍保留。",
                   "table": _table([("city", "城市"), ("date", "日期"), ("previous_value", "前一日"),
                                     ("value", "当日"), ("delta", "变化量"), ("percent_change", "变化率（%）"), ("marker", "标记")], events),
                   "charts": [_chart("bar", f"{pollutant} 相邻日变化量", standard["unit"],
                                      [event["city"] + " " + event["date"] for event in events],
                                      [{"name": "当日减前一日", "values": [event["delta"] for event in events]}])] if events else []})
    result["metrics"].extend([{"label": "相邻有效日对", "value": pairs, "unit": "对"},
                               {"label": "跳变标记", "value": count, "unit": "次"}])
    result["table"]["empty_message"] = "未检测到符合当前规则的相邻日跳变。"
    result["warnings"].append("跳变不是数据错误的判定，不自动剔除高污染峰值；缺失日期不构成相邻日。")
    if options["group_by"] == "province":
        result["warnings"].append("跳变在省份筛选内逐城检查，避免将不同城市串接为相邻日。")


def analyze(root: Path, plan: dict) -> dict:
    """Execute a validated local analysis plan; no network, plotting or LLM calls."""
    if not isinstance(plan, dict):
        raise ValueError("plan 必须是对象。")
    intent = plan.get("intent") or "ranking"
    if intent not in INTENTS:
        raise ValueError(f"不支持的分析类型：{intent}")
    pollutant = canonical_pollutant(plan.get("pollutant"))
    column = POLLUTANT_COLUMNS[pollutant]
    where, parameters, options = _selection(plan)
    meta = load_dataset(Path(root))
    context = {**options, "pollutant": pollutant, "available_years": meta["years"],
               "available_cities": meta["cities"], "available_provinces": meta["provinces"],
               "date_min": meta["date_min"], "date_max": meta["date_max"], "dataset_sha256": meta["sha256"]}
    warnings = list(meta.get("warnings", []))
    result = _empty(pollutant, intent, meta, context, warnings)
    with closing(sqlite3.connect(Path(root) / "data" / DB_NAME)) as connection:
        connection.row_factory = sqlite3.Row
        selected = connection.execute(f'SELECT COUNT(*) AS records,COUNT("{column}") AS valid_days,COUNT(DISTINCT city) AS cities FROM daily WHERE {where}', parameters).fetchone()
        result["quality"].update({"selected_records": selected["records"], "selected_valid_city_days": selected["valid_days"],
                                   "selected_missing_city_days": selected["records"] - selected["valid_days"]})
        result["metrics"] = [{"label": "有效城市日", "value": selected["valid_days"], "unit": "条"},
                               {"label": "覆盖城市", "value": selected["cities"], "unit": "个"},
                               {"label": "缺测城市日", "value": selected["records"] - selected["valid_days"], "unit": "条"}]
        result["context"]["cities"] = [row[0] for row in connection.execute(f'SELECT DISTINCT city FROM daily WHERE {where} ORDER BY city', parameters)]
        if not selected["records"]:
            if not meta["rows"]:
                result["summary"] = "尚未导入有效日记录，请在 data/raw 放入真实来源 CSV 文件。"
            return result
        if selected["records"] != selected["valid_days"]:
            warnings.append(f"所选 {pollutant} 有 {selected['records'] - selected['valid_days']} 个缺测城市日，未计为达标。")
        if options["year"] is None and intent == "ranking":
            warnings.append("未限定单一年份，排名为所选时期日记录均值，不能作正式年均达标判定。")
        handlers = {"ranking": _rank, "exceedance": _exceedance, "trend": _trend,
                    "seasonal": _seasonal, "anomaly": _anomaly}
        handlers[intent](connection, where, parameters, options, pollutant, column, result)
        if selected["valid_days"]:
            result["insight"] += " 用连续监测识别重点区域与时段，为精准治污与美丽中国建设提供数据支撑。"
    return result
