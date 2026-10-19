"""Competition-specified historical GB 3095-2012 reference limits.

Completeness is checked against valid daily observations, independently for each
pollutant. No monthly or seasonal compliance limit is implied by these helpers.
"""
from __future__ import annotations

import calendar
import re
import unicodedata
from typing import Mapping

STANDARD_NAME = "GB 3095—2012 二级（赛事指定历史口径）"
STANDARD_URL = "https://www.mee.gov.cn/ywgz/fgbz/bz/bzwb/dqhjbh/dqhjzlbz/201203/W020250407403788086276.pdf"
STANDARD_NOTE = "比赛采用 GB 3095—2012 及2018修改单二级限值；2024历史数据按此口径分析。"
LIMITS = {
    "PM2.5": {"annual": 35, "daily": 75, "unit": "μg/m³"},
    "PM10": {"annual": 70, "daily": 150, "unit": "μg/m³"},
    "SO2": {"annual": 60, "daily": 150, "unit": "μg/m³"},
    "NO2": {"annual": 40, "daily": 80, "unit": "μg/m³"},
    "CO": {"annual": None, "daily": 4, "unit": "mg/m³"},
    "O3_8h": {"annual": None, "daily": 160, "unit": "μg/m³"},
    "AQI": {"annual": None, "daily": None, "unit": "指数"},
}


def canonical_pollutant(value: str | None) -> str:
    value = unicodedata.normalize("NFKC", value or "PM2.5")
    key = re.sub(r"[\s_.\-]", "", value).lower()
    names = {
        "pm25": "PM2.5", "细颗粒物": "PM2.5", "pm10": "PM10",
        "可吸入颗粒物": "PM10", "so2": "SO2", "二氧化硫": "SO2",
        "no2": "NO2", "二氧化氮": "NO2", "co": "CO", "一氧化碳": "CO",
        "o38h": "O3_8h", "臭氧8小时": "O3_8h", "臭氧8小时平均": "O3_8h",
        "日最大8小时臭氧": "O3_8h", "aqi": "AQI", "空气质量指数": "AQI",
    }
    if key in ("o3", "臭氧"):
        raise ValueError("普通 O3 未说明平均时长，不能作为 O3_8h；请明确使用日最大8小时平均臭氧。")
    if key not in names:
        raise ValueError(f"不支持的污染物：{value}。可用：{', '.join(LIMITS)}")
    return names[key]


def get_standard(pollutant: str, basis: str = "daily") -> dict:
    pollutant = canonical_pollutant(pollutant)
    entry = LIMITS[pollutant]
    labels = {"annual": "年平均", "daily": "日最大8小时平均" if pollutant == "O3_8h" else "24小时平均",
              "monthly": "月平均（描述统计）", "seasonal": "季平均（描述统计）"}
    limit = entry.get(basis) if basis in ("annual", "daily") else None
    note = STANDARD_NOTE
    if pollutant == "AQI":
        note += " AQI 是综合指数，本引擎不把它作为该标准的浓度限值进行达标判定。"
    elif basis == "annual" and limit is None:
        note += f" {pollutant} 无年平均浓度限值，样本均值仅供描述。"
    elif basis in ("monthly", "seasonal"):
        note += " 该统计周期无对应限值，不以月均或季均判定达标。"
    note += " 统计值严格大于对应周期限值时记为超标；缺失值不参与分母。"
    return {"name": STANDARD_NAME, "pollutant": pollutant, "limit": limit,
            "unit": entry["unit"], "basis": labels.get(basis, basis), "note": note, "url": STANDARD_URL}


def get_standards() -> list[dict]:
    return [{"pollutant": name, "annual_limit": values["annual"], "daily_limit": values["daily"],
             "unit": values["unit"], "daily_basis": "日最大8小时平均" if name == "O3_8h" else "24小时平均",
             "name": STANDARD_NAME, "note": STANDARD_NOTE, "url": STANDARD_URL}
            for name, values in LIMITS.items() if name != "AQI"]


def monthly_required_days(year: int, month: int) -> int:
    # Validate both year and month, including leap years, without assuming 30 days.
    calendar.monthrange(year, month)
    return 25 if month == 2 else 27


def monthly_completeness(year: int, month: int, valid_days: int) -> dict:
    required = monthly_required_days(year, month)
    return {"complete": valid_days >= required, "valid_days": valid_days,
            "required_days": required, "calendar_days": calendar.monthrange(year, month)[1]}


def annual_completeness(year: int, valid_by_month: Mapping[int, int], full_year: bool = True) -> dict:
    months = {month: monthly_completeness(year, month, int(valid_by_month.get(month, 0)))
              for month in range(1, 13)}
    total = sum(item["valid_days"] for item in months.values())
    incomplete = [month for month, item in months.items() if not item["complete"]]
    return {"complete": bool(full_year and total >= 324 and not incomplete),
            "full_year_selected": bool(full_year), "valid_days": total, "required_days": 324,
            "calendar_days": 366 if calendar.isleap(year) else 365,
            "incomplete_months": incomplete, "months": months}
