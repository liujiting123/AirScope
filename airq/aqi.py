"""Derive daily AQI from valid daily concentrations under historical HJ 633-2012.

Official source (table 1; sections 4.2, 4.4, 5.1.2 and 6.2):
https://www.mee.gov.cn/ywgz/fgbz/bz/bzwb/jcffbz/201203/W020250407406362045613.pdf

The daily report has SIX pollutant species and SEVEN concentration indicators:
SO2, NO2, PM10, PM2.5 and CO daily means, plus O3 daily maximum 1-hour
and daily maximum 8-hour averages. Both ozone indicators normally contribute
to AQI. Only when O3_8h > 800 ug/m3 does table 1 note (3) replace the
8-hour subindex with the 1-hour subindex. The 1-hour value is required even
in the ordinary range; a six-indicator subset is not reported as complete.

Subindices use piecewise linear interpolation and ALL results are rounded
upwards to integers (6.2), not rounded to nearest. AQI is the maximum rounded
subindex. Primary pollutants are all tied maxima when AQI > 50 (4.4.2).

Callers must establish the hourly/daily statistical validity before calling:
this function cannot infer valid-hour or valid-window counts from a mean.
CO must be in mg/m3; all other concentrations must be in ug/m3. Input daily
means are used as supplied, without invented concentration-rounding rules.
Concentrations beyond the highest applicable table breakpoint are not
extrapolated or silently capped; they produce None with a reason.

The result is calculated from concentrations, not an upstream reviewed AQI
release. HJ 633-2012 is the historical standard for this 2024 analysis.
"""
from __future__ import annotations

from decimal import Decimal, InvalidOperation, ROUND_CEILING, localcontext

SOURCE_URL = "https://www.mee.gov.cn/ywgz/fgbz/bz/bzwb/jcffbz/201203/W020250407406362045613.pdf"
DERIVED_NOTE = "由有效日浓度按 HJ 633—2012 七项日报指标派生，非上游审核日AQI；用于2024历史数据分析。"
REQUIRED_POLLUTANTS = ("PM2.5", "PM10", "SO2", "NO2", "CO", "O3_8h")
IAQI_BREAKPOINTS = (0, 50, 100, 150, 200, 300, 400, 500)
CONCENTRATION_BREAKPOINTS = {
    "PM2.5": (0, 35, 75, 115, 150, 250, 350, 500),
    "PM10": (0, 50, 150, 250, 350, 420, 500, 600),
    "SO2": (0, 50, 150, 475, 800, 1600, 2100, 2620),
    "NO2": (0, 40, 80, 180, 280, 565, 750, 940),
    "CO": (0, 2, 4, 14, 24, 36, 48, 60),
    "O3_8h": (0, 100, 160, 215, 265, 800),
    "O3_1h": (0, 160, 200, 300, 400, 800, 1000, 1200),
}
QUALITY_BANDS = ((50, "优"), (100, "良"), (150, "轻度污染"),
                 (200, "中度污染"), (300, "重度污染"), (500, "严重污染"))


def _concentration(value) -> Decimal | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        number = Decimal(str(value).strip())
    except (InvalidOperation, TypeError, ValueError):
        return None
    return number if number.is_finite() and number >= 0 else None


def _subindex(concentration: Decimal, pollutant: str) -> int | None:
    bounds = CONCENTRATION_BREAKPOINTS[pollutant]
    if concentration == 0:
        return 0
    if concentration > bounds[-1]:
        return None
    for index in range(1, len(bounds)):
        low, high = Decimal(bounds[index - 1]), Decimal(bounds[index])
        if concentration <= high:
            # Explicit endpoints avoid an arithmetic artefact at an exact limit.
            if concentration == high:
                return IAQI_BREAKPOINTS[index]
            with localcontext() as context:
                context.prec = 50
                result = (Decimal(IAQI_BREAKPOINTS[index - 1])
                          + (concentration - low)
                          * (IAQI_BREAKPOINTS[index] - IAQI_BREAKPOINTS[index - 1])
                          / (high - low))
                return int(result.to_integral_value(rounding=ROUND_CEILING))
    return None


def _unavailable(reason: str) -> dict:
    return {"aqi": None, "quality": "", "primary_pollutants": [],
            "reason": reason, "note": DERIVED_NOTE}


def calculate_daily_aqi(values: dict, o3_hourly_max: float | None = None) -> dict:
    """Return derived AQI, Chinese quality, primary pollutants, reason and note.

    ``values`` requires PM2.5, PM10, SO2, NO2, CO and O3_8h. The separate
    ``o3_hourly_max`` is the same day's valid maximum 1-hour O3 concentration.
    Missing, nonnumeric, nonfinite or negative indicators produce no AQI.
    A valid AQI has an empty reason; unavailable quality is an empty string.
    Ozone is named ``O3`` among primary pollutants, regardless of which of its
    two concentration indicators determines its subindex.
    """
    if not isinstance(values, dict):
        return _unavailable("日浓度必须是包含六种污染物的字典。")
    concentrations = {name: _concentration(values.get(name)) for name in REQUIRED_POLLUTANTS}
    invalid = [name for name, concentration in concentrations.items() if concentration is None]
    if invalid:
        return _unavailable("日报指标缺失或无效：" + "、".join(invalid) + "；不以缺项浓度计算完整AQI。")
    hourly_ozone = _concentration(o3_hourly_max)
    if hourly_ozone is None:
        return _unavailable("缺少或无效的 O3 日最大1小时平均浓度；完整日报需要七项有效指标。")
    if hourly_ozone < concentrations["O3_8h"]:
        return _unavailable("O3 日最大1小时值小于日最大8小时值，指标口径或数值需复核。")
    concentrations["O3_1h"] = hourly_ozone
    subindices = {}
    for pollutant, concentration in concentrations.items():
        if pollutant == "O3_8h" and concentration > 800:
            # Table 1 note (3): no extrapolation of the 8-hour ozone column.
            continue
        index = _subindex(concentration, pollutant)
        if index is None:
            return _unavailable(f"{pollutant} 浓度超过 HJ 633—2012 表1最高断点；不外推或擅自封顶AQI。")
        subindices[pollutant] = index
    aqi = max(subindices.values())
    quality = next(label for maximum, label in QUALITY_BANDS if aqi <= maximum)
    primary = []
    if aqi > 50:
        for pollutant, index in subindices.items():
            name = "O3" if pollutant.startswith("O3_") else pollutant
            if index == aqi and name not in primary:
                primary.append(name)
    return {"aqi": aqi, "quality": quality, "primary_pollutants": primary,
            "reason": "", "note": DERIVED_NOTE}
