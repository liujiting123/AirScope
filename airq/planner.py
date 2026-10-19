"""可审计的离线规划器：识别分析意图和约束，不生成统计数值。

支持需求书三类问题、趋势/季节对比/跳变检查及显式多轮指代。
此模块是受限领域规则规划器，不是大语言模型；超出语法范围时明确报错。
"""
from __future__ import annotations
import re


class PlanningError(ValueError):
    pass


POLLUTANTS = {
    "PM2.5": r"PM\s*2[.．]?5|细颗粒物",
    "PM10": r"PM\s*10|可吸入颗粒物",
    "SO2": r"SO2|二氧化硫",
    "NO2": r"NO2|二氧化氮",
    "CO": r"(?<![A-Z])CO(?![A-Z])|一氧化碳",
    "O3_8h": r"O3|臭氧",
    "AQI": r"AQI|空气质量指数",
}
NUMBER = {"一": 1, "二": 2, "三": 3, "四": 4, "五": 5, "六": 6,
          "七": 7, "八": 8, "九": 9, "十": 10}


def plan_query(query: str, dataset: dict, previous: dict | None = None) -> dict:
    q = query.strip().translate(str.maketrans("₂₃₁₀０１２３４５６７８９", "23100123456789"))
    if not q or len(q) > 1000:
        raise PlanningError("请输入 1–1000 字的空气质量分析问题。")
    if re.search(r"预测|明天|未来|因果|溯源|排放源", q):
        raise PlanningError("当前工具只分析已观测历史数据，不进行未来预测或污染因果归因。请查询年均排名、超标占比、月度趋势或异常跳变。")
    previous = previous or {}
    followup = bool(re.search(r"这几|这些|上述|刚才|上一轮|再看|换成|换为|同样", q))
    if followup and not previous:
        raise PlanningError("还没有可追问的上一轮结果。请先运行一次城市排名或城市分析。")
    pollutants = [p for p, pattern in POLLUTANTS.items() if re.search(pattern, q, re.I)]
    if len(pollutants) > 1:
        raise PlanningError("每次请分析一种污染物，便于保持单位和国标口径一致；下一轮可用“再看这些城市的 PM10”。")
    pollutant = pollutants[0] if pollutants else (previous.get("pollutant", "PM2.5") if followup else "PM2.5")
    years = sorted(set(int(x) for x in re.findall(r"(?<!\d)(20\d{2})(?:\s*年)?", q)))
    if len(years) > 1:
        raise PlanningError("当前一次分析一个自然年。跨年冬季请分别查询两个年份，避免混淆统计分母。")
    available_years = [int(y) for y in dataset.get("years", [])]
    if not available_years:
        raise PlanningError("尚未加载有效数据，请将真实日数据 CSV 放入 data/raw 后重启。")
    year = years[0] if years else (previous.get("year") if followup else None) or dataset.get("analysis_year") or max(available_years)
    if year not in available_years:
        raise PlanningError(f"本地数据不包含 {year} 年。可用年份：{'、'.join(map(str, available_years))}；不会用其他年份冒充。")
    def province_alias(name):
        return re.sub(r"(?:维吾尔自治区|壮族自治区|回族自治区|自治区|特别行政区|省)$", "", name)
    provinces = [p for p in dataset.get("provinces", []) if p != "未知" and (p in q or province_alias(p) in q)]
    city_text = q
    for province in provinces:
        if province.endswith("省") or province.endswith("自治区"):
            city_text = city_text.replace(province, " ")
    cities = [c for c in dataset.get("cities", []) if c in city_text or (len(c) > 2 and c.removesuffix("市") in city_text)]
    if followup and not cities and re.search(r"这几|这些|上述|刚才|上一轮|再看|换成|换为|同样", q):
        cities = list(previous.get("cities", []))
    # 明确城市名却不在数据中时，不悄悄扩大为全国。
    if not cities:
        place_q = re.sub(r"统计|分析|比较|对比|请问|请|查看|看看|筛选|找出", " ", q)
        named = re.findall(r"([\u4e00-\u9fff]{2,7}市)(?!场)", place_q)
        named = [x for x in named if x not in ("个城市", "这些城市", "几个城市", "所有城市") and not x.endswith("城市")]
        if named:
            raise PlanningError(f"本地数据未找到 {'、'.join(named)}。可用城市：{'、'.join(dataset.get('cities', [])[:18])}。")
    months = []
    time_text = q
    for pattern in POLLUTANTS.values():
        time_text = re.sub(pattern, " ", time_text, flags=re.I)
    seasonal = {"春": [3, 4, 5], "夏": [6, 7, 8], "秋": [9, 10, 11], "冬": [1, 2, 12]}
    for season, selected in seasonal.items():
        if re.search(season + r"(?:季|天)", q):
            months.extend(selected)
    span = re.search(r"(?<!\d)(1[0-2]|[1-9])\s*月?\s*(?:至|到|[-~—])\s*(1[0-2]|[1-9])\s*月", time_text)
    if span:
        start, end = map(int, span.groups())
        if start > end:
            raise PlanningError("月份范围跨年，请分两个自然年查询。")
        months = list(range(start, end + 1))
    elif not months:
        months = [int(x) for x in re.findall(r"(?<!\d)(1[0-2]|[1-9])\s*月(?!度)", time_text)]
    if re.search(r"\d{1,2}\s*[日号]|20\d\d[-/]\d", q):
        raise PlanningError("当前支持按年、月份范围和季节筛选；暂不支持精确到日的自然语言区间。")
    if re.search(r"异常|突变|跳变|突发", q):
        intent = "anomaly"
    elif re.search(r"季节|四季|各季|春夏秋冬", q):
        intent = "seasonal"
    elif "年均" in q or (not re.search(r"超标|达标|占比", q) and re.search(r"最高|最低|排名|排行|TOP|前\s*[一二三四五六七八九十\d]+", q, re.I)):
        intent = "ranking"
    elif re.search(r"超标|达标|占比", q):
        intent = "exceedance"
    elif re.search(r"趋势|变化|曲线|月度|月均|对比|比较|分析|统计", q):
        intent = "trend"
    else:
        raise PlanningError("暂未识别分析意图。试试“PM2.5 年均最高的 5 个城市”“各省 PM2.5 超标占比”或“北京市夏季臭氧超标天数”。")
    top = re.search(r"(?:TOP\s*|前\s*|最高的?\s*|最低的?\s*)(\d{1,3}|[一二三四五六七八九十])", q, re.I)
    top_n = int(top.group(1)) if top and top.group(1).isdigit() else NUMBER.get(top.group(1), 5) if top else 5
    if not top and intent in ("exceedance", "seasonal"):
        top_n = 100
    top_n = min(max(top_n, 1), 100)
    if "最低" in q:
        raise PlanningError("当前排名工具支持浓度从高到低排序。请改问“最高的城市”，或查看各城市月度趋势。")
    return {"intent": intent, "pollutant": pollutant, "year": year,
            "months": sorted(set(months)), "cities": cities, "provinces": provinces,
            "group_by": "province" if re.search(r"各省|省份|分省|按省", q) else "city",
            "top_n": top_n, "followup": followup}
