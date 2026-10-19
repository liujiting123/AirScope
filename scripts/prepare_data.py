#!/usr/bin/env python3
"""Download and aggregate Quotsoft's 2024 China-city air-quality archive.

Only Python's standard library is used. Completed daily source files are kept
in data/source-cache and reused on later runs. The output is a long-form CSV
with one row per source city and calendar date.
"""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import hashlib
import json
import math
import os
import sys
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from airq.aqi import calculate_daily_aqi

DATA_DIR = ROOT / "data"
CACHE_DIR = DATA_DIR / "source-cache"
DAILY_CACHE = CACHE_DIR / "daily"
RAW_DIR = DATA_DIR / "raw"
OUTPUT_CSV = RAW_DIR / "air_quality_2024.csv"
SOURCE_JSON = DATA_DIR / "source.json"
HASH_MANIFEST = CACHE_DIR / "SHA256SUMS.txt"
ADMIN_JSON = CACHE_DIR / "administrative-divisions-pcas-code.json"

SOURCE_PAGE = "https://quotsoft.net/air/"
SOURCE_TEMPLATE = "https://quotsoft.net/air/data/china_cities_{date}.csv"
UPSTREAM_URL = "https://air.cnemc.cn:18007/"
ADMIN_URL = (
    "https://raw.githubusercontent.com/modood/Administrative-divisions-of-China/"
    "master/dist/pcas-code.json"
)
USER_AGENT = "air-quality-coursework-data-preparer/1.0 (stdlib urllib)"
POLLUTANTS = ("PM2.5", "PM10", "SO2", "NO2", "CO")
SOURCE_FIELDS = (*POLLUTANTS, "O3", "AQI")
CITY_ALIASES = {
    "阿坝州": "阿坝藏族羌族自治州",
    "甘孜州": "甘孜藏族自治州",
    "凉山州": "凉山彝族自治州",
    "黔东南州": "黔东南苗族侗族自治州",
    "黔南州": "黔南布依族苗族自治州",
    "黔西南州": "黔西南布依族苗族自治州",
    "湘西州": "湘西土家族苗族自治州",
    "恩施州": "恩施土家族苗族自治州",
    "伊犁州": "伊犁哈萨克自治州",
    "巴州": "巴音郭楞蒙古自治州",
    "昌吉州": "昌吉回族自治州",
    "博州": "博尔塔拉蒙古自治州",
    "克州": "克孜勒苏柯尔克孜自治州",
    "和田": "和田地区",
    "阿克苏": "阿克苏地区",
    "吐鲁番": "吐鲁番市",
    "哈密": "哈密市",
    "铜仁地区": "铜仁市",
    "大兴安岭地区": "大兴安岭地区",
    "那曲地区": "那曲市",
    "海东地区": "海东市",
    "西双版纳州": "西双版纳傣族自治州",
    "甘南州": "甘南藏族自治州",
    "海西州": "海西蒙古族藏族自治州",
}
CITY_PROVINCE_OVERRIDES = {
    "山东省": ("寿光", "章丘", "即墨", "胶南", "胶州", "莱西", "平度", "蓬莱", "招远", "莱州", "荣成", "文登", "乳山"),
    "江苏省": ("吴江", "昆山", "常熟", "张家港", "太仓", "句容", "江阴", "宜兴", "金坛", "溧阳", "海门"),
    "浙江省": ("临安", "富阳", "义乌", "诸暨"),
    "辽宁省": ("瓦房店",),
    "云南省": ("楚雄州", "红河州", "文山州", "大理州", "德宏州", "怒江州", "迪庆州"),
    "新疆维吾尔自治区": ("巴音郭楞州", "伊犁哈萨克州", "石河子", "五家渠"),
    "甘肃省": ("兰州新区",),
    "江西省": ("赣江新区",),
    "河北省": ("雄安新区",),
    "陕西省": ("西咸新区",),
}


def date_range(start: dt.date, end_inclusive: dt.date):
    current = start
    while current <= end_inclusive:
        yield current
        current += dt.timedelta(days=1)


def daily_path(day: dt.date) -> Path:
    return DAILY_CACHE / f"china_cities_{day:%Y%m%d}.csv"


def canonical_type(value: str) -> str:
    value = value.strip().replace("₂", "2").replace("₃", "3")
    aliases = {
        "PM2.5": "PM2.5",
        "PM2.5_1h": "PM2.5",
        "PM10": "PM10",
        "SO2": "SO2",
        "NO2": "NO2",
        "CO": "CO",
        "O3": "O3",
        "AQI": "AQI",
    }
    return aliases.get(value, value)


def parse_number(value: Any) -> float | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text or text.lower() in {"na", "nan", "null", "none", "-", "--", "---"}:
        return None
    try:
        number = float(text)
    except ValueError:
        return None
    if not math.isfinite(number) or number < 0:
        return None
    return number


def validate_source_file(path: Path, expected_date: dt.date | None = None) -> bool:
    try:
        if path.stat().st_size < 32:
            return False
        with path.open("r", encoding="utf-8-sig", newline="") as stream:
            reader = csv.reader(stream)
            header = next(reader, None)
            if not header or [item.strip().lower() for item in header[:3]] != ["date", "hour", "type"]:
                return False
            first_row = next(reader, None)
            if not first_row or len(first_row) < 4:
                return False
            if expected_date and first_row[0].strip().replace("-", "") != f"{expected_date:%Y%m%d}":
                return False
        return True
    except (OSError, UnicodeDecodeError, csv.Error, StopIteration):
        return False


def download_daily(day: dt.date, attempts: int = 5) -> tuple[str, int, str]:
    destination = daily_path(day)
    if validate_source_file(destination, day):
        return "cached", destination.stat().st_size, day.isoformat()

    destination.parent.mkdir(parents=True, exist_ok=True)
    partial = destination.with_suffix(destination.suffix + ".part")
    url = SOURCE_TEMPLATE.format(date=f"{day:%Y%m%d}")
    last_error = "unknown error"

    for attempt in range(1, attempts + 1):
        try:
            request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
            with urllib.request.urlopen(request, timeout=45) as response:
                with partial.open("wb") as output:
                    while True:
                        chunk = response.read(1024 * 1024)
                        if not chunk:
                            break
                        output.write(chunk)
            if not validate_source_file(partial, day):
                raise ValueError("response is not a valid Quotsoft city CSV")
            os.replace(partial, destination)
            return "downloaded", destination.stat().st_size, day.isoformat()
        except urllib.error.HTTPError as error:
            last_error = f"HTTP {error.code}"
            if error.code == 404:
                partial.unlink(missing_ok=True)
                return "missing", 0, f"{day.isoformat()}: {last_error}"
        except (urllib.error.URLError, TimeoutError, OSError, ValueError) as error:
            last_error = f"{type(error).__name__}: {error}"
        partial.unlink(missing_ok=True)
        if attempt < attempts:
            time.sleep(min(2 ** (attempt - 1), 15))

    return "failed", 0, f"{day.isoformat()}: {last_error}"


def download_admin_json() -> None:
    if ADMIN_JSON.exists() and ADMIN_JSON.stat().st_size > 1000:
        try:
            json.loads(ADMIN_JSON.read_text(encoding="utf-8"))
            return
        except (OSError, json.JSONDecodeError):
            pass

    partial = ADMIN_JSON.with_suffix(ADMIN_JSON.suffix + ".part")
    last_error: Exception | None = None
    for attempt in range(1, 6):
        try:
            request = urllib.request.Request(ADMIN_URL, headers={"User-Agent": USER_AGENT})
            with urllib.request.urlopen(request, timeout=45) as response, partial.open("wb") as output:
                while True:
                    chunk = response.read(1024 * 1024)
                    if not chunk:
                        break
                    output.write(chunk)
            json.loads(partial.read_text(encoding="utf-8"))
            os.replace(partial, ADMIN_JSON)
            return
        except (urllib.error.URLError, TimeoutError, OSError, json.JSONDecodeError) as error:
            last_error = error
            partial.unlink(missing_ok=True)
            if attempt < 5:
                time.sleep(min(2 ** (attempt - 1), 15))
    raise RuntimeError(f"could not retrieve province/city mapping JSON: {last_error}")


def read_admin_tree(value: Any):
    if isinstance(value, list):
        return value
    if isinstance(value, dict):
        for key in ("provinces", "data", "result"):
            if isinstance(value.get(key), list):
                return value[key]
        return [value]
    return []


def name_variants(name: str) -> set[str]:
    name = name.strip()
    if not name:
        return set()
    variants = {name}
    for suffix in ("特别行政区", "维吾尔自治区", "壮族自治区", "回族自治区", "自治区", "自治州", "地区", "市", "盟"):
        if name.endswith(suffix) and len(name) > len(suffix):
            variants.add(name[: -len(suffix)])
    return variants


def build_province_map(admin_data: Any) -> dict[str, str]:
    province_map: dict[str, str] = {}
    provinces = read_admin_tree(admin_data)
    for province in provinces:
        if not isinstance(province, dict):
            continue
        province_name = str(province.get("name", "")).strip()
        if not province_name:
            continue
        for variant in name_variants(province_name):
            province_map.setdefault(variant, province_name)
        children = province.get("children") or province.get("cities") or []
        if not isinstance(children, list):
            continue
        for city in children:
            if not isinstance(city, dict):
                continue
            city_name = str(city.get("name", "")).strip()
            if city_name:
                for variant in name_variants(city_name):
                    province_map.setdefault(variant, province_name)
    for short_name, official_name in CITY_ALIASES.items():
        province = province_map.get(official_name)
        if province:
            province_map[short_name] = province
    for province_name, city_labels in CITY_PROVINCE_OVERRIDES.items():
        for city_label in city_labels:
            province_map[city_label] = province_name
    return province_map


def lookup_province(city: str, province_map: dict[str, str]) -> str:
    city = city.strip()
    if city in province_map:
        return province_map[city]
    for variant in name_variants(city):
        if variant in province_map:
            return province_map[variant]
    official_name = CITY_ALIASES.get(city)
    if official_name:
        for variant in name_variants(official_name):
            if variant in province_map:
                return province_map[variant]
    return ""


def read_day_values(path: Path | None) -> tuple[list[str], dict[str, dict[str, dict[int, float | None]]]]:
    if path is None or not path.exists() or not validate_source_file(path):
        return [], {}

    with path.open("r", encoding="utf-8-sig", newline="") as stream:
        reader = csv.DictReader(stream)
        headers = reader.fieldnames or []
        city_columns = headers[3:]
        values: dict[str, dict[str, dict[int, float | None]]] = {
            city: {} for city in city_columns if city.strip()
        }
        for row in reader:
            kind = canonical_type(row.get("type", ""))
            if kind not in SOURCE_FIELDS:
                continue
            try:
                hour = int(str(row.get("hour", "")).strip())
            except ValueError:
                continue
            if hour < 0 or hour > 23:
                continue
            for city in city_columns:
                if not city.strip():
                    continue
                values[city].setdefault(kind, {})[hour] = parse_number(row.get(city))
    return [city for city in city_columns if city.strip()], values


def read_city_headers(path: Path | None) -> list[str]:
    """Read only the wide source header when discovering the output city set."""
    if path is None or not path.exists() or not validate_source_file(path):
        return []
    try:
        with path.open("r", encoding="utf-8-sig", newline="") as stream:
            header = next(csv.reader(stream), None)
        if not header or len(header) < 4:
            return []
        return [city.strip() for city in header[3:] if city.strip()]
    except (OSError, UnicodeDecodeError, csv.Error, StopIteration):
        return []


def valid_mean(series: list[float | None]) -> tuple[float | None, int]:
    valid = [value for value in series if value is not None]
    if len(valid) < 20:
        return None, len(valid)
    return sum(valid) / len(valid), len(valid)


def source_row_value(day_values: dict[str, dict[str, dict[int, float | None]]], city: str, kind: str, hour: int) -> float | None:
    return day_values.get(city, {}).get(kind, {}).get(hour)


def aggregate(output_path: Path, source_status: dict[str, str], source_missing: list[str]) -> dict[str, Any]:
    days = list(date_range(dt.date(2024, 1, 1), dt.date(2024, 12, 31)))
    cities: set[str] = set()
    for day in date_range(dt.date(2024, 1, 1), dt.date(2025, 1, 1)):
        path = daily_path(day)
        cities.update(read_city_headers(path))

    province_map = build_province_map(json.loads(ADMIN_JSON.read_text(encoding="utf-8")))
    cities_sorted = sorted(cities)
    if not cities_sorted:
        raise RuntimeError("no city headers found in downloaded source files")

    stats: dict[str, Any] = {
        "rows": 0,
        "cities": len(cities_sorted),
        "dates": len(days),
        "city_dates": 0,
        "candidate_city_dates": len(cities_sorted) * len(days),
        "missing_daily_rows": 0,
        "missing_daily_dates": [],
        "unmapped_cities": [],
        "excluded_all_year_empty_cities": [],
        "filtered_empty_city_days": 0,
        "aqi_derived_rows": 0,
        "aqi_unavailable_reasons": {},
        "valid_days_by_pollutant": {kind: 0 for kind in POLLUTANTS + ("O3_8h", "O3_1h")},
        "invalid_hours_by_pollutant": {kind: 0 for kind in POLLUTANTS + ("O3_1h",)},
        "invalid_o3_days": 0,
        "o3_over_limit_exception_days": 0,
    }
    output_path.parent.mkdir(parents=True, exist_ok=True)
    temp_output = output_path.with_suffix(output_path.suffix + ".part")
    columns = [
        "date", "city", "province", "PM2.5", "PM10", "SO2", "NO2", "CO",
        "O3_8h", "hour_max_O3", "AQI", "quality", "primary_pollutants",
        "aqi_reason", "aqi_note", "data_quality",
    ]
    active_cities: set[str] = set()
    candidate_unmapped: set[str] = set()

    with temp_output.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=columns)
        writer.writeheader()
        today_headers, today = read_day_values(daily_path(days[0]))
        next_headers, following = read_day_values(daily_path(days[0] + dt.timedelta(days=1)))
        for index, day in enumerate(days, start=1):
            if not today_headers:
                stats["missing_daily_dates"].append(day.isoformat())
            if not today_headers or not next_headers:
                stats["missing_daily_rows"] += len(cities_sorted)

            for city in cities_sorted:
                quality: list[str] = []
                if not today_headers:
                    quality.append("source_day_missing")
                if not next_headers:
                    quality.append("next_day_missing")

                measures: dict[str, float | None] = {}
                for pollutant in POLLUTANTS:
                    hourly = [
                        source_row_value(today, city, pollutant, hour)
                        for hour in range(1, 24)
                    ]
                    hourly.append(source_row_value(following, city, pollutant, 0))
                    mean, count = valid_mean(hourly)
                    measures[pollutant] = mean
                    if mean is None:
                        quality.append(f"{pollutant}_invalid_hours_{count}_of_24")
                        stats["invalid_hours_by_pollutant"][pollutant] += 1
                    else:
                        stats["valid_days_by_pollutant"][pollutant] += 1

                o3_hourly = [
                    source_row_value(today, city, "O3", hour)
                    for hour in range(1, 24)
                ]
                o3_hourly.append(source_row_value(following, city, "O3", 0))
                valid_hourly_o3 = [value for value in o3_hourly if value is not None]
                if len(valid_hourly_o3) >= 20:
                    measures["hour_max_O3"] = max(valid_hourly_o3)
                    stats["valid_days_by_pollutant"]["O3_1h"] += 1
                else:
                    measures["hour_max_O3"] = None
                    quality.append(f"O3_1h_invalid_hours_{len(valid_hourly_o3)}_of_24")
                    stats["invalid_hours_by_pollutant"]["O3_1h"] += 1
                valid_windows: list[float] = []
                for end_hour in range(8, 25):
                    window = o3_hourly[end_hour - 8 : end_hour]
                    valid_values = [value for value in window if value is not None]
                    if len(valid_values) >= 6:
                        valid_windows.append(sum(valid_values) / len(valid_values))
                max_o3_8h = max(valid_windows) if valid_windows else None
                if max_o3_8h is not None and (len(valid_windows) >= 14 or max_o3_8h > 160):
                    measures["O3_8h"] = max_o3_8h
                    stats["valid_days_by_pollutant"]["O3_8h"] += 1
                    if len(valid_windows) < 14:
                        quality.append(f"O3_8h_over_160_exception_{len(valid_windows)}_of_17_windows")
                        stats["o3_over_limit_exception_days"] += 1
                else:
                    measures["O3_8h"] = None
                    quality.append(f"O3_8h_invalid_windows_{len(valid_windows)}_of_17")
                    stats["invalid_o3_days"] += 1

                province = lookup_province(city, province_map)
                if not province:
                    quality.append("province_unmapped")
                    candidate_unmapped.add(city)
                if not any(flag.endswith("missing") for flag in quality):
                    if len(quality) == 0:
                        quality.append("valid")
                aqi_result = calculate_daily_aqi(
                    {name: measures[name] for name in POLLUTANTS + ("O3_8h",)},
                    o3_hourly_max=measures["hour_max_O3"],
                )
                if aqi_result["aqi"] is None:
                    reason = aqi_result["reason"]
                    stats["aqi_unavailable_reasons"][reason] = stats["aqi_unavailable_reasons"].get(reason, 0) + 1
                else:
                    stats["aqi_derived_rows"] += 1
                rendered: dict[str, Any] = {
                    "date": day.isoformat(),
                    "city": city,
                    "province": province,
                    "AQI": "" if aqi_result["aqi"] is None else aqi_result["aqi"],
                    "quality": aqi_result["quality"],
                    "primary_pollutants": ";".join(aqi_result["primary_pollutants"]),
                    "aqi_reason": aqi_result["reason"],
                    "aqi_note": aqi_result["note"],
                    "data_quality": ";".join(quality),
                }
                for pollutant in POLLUTANTS:
                    value = measures[pollutant]
                    rendered[pollutant] = "" if value is None else f"{value:.4f}".rstrip("0").rstrip(".")
                value = measures["O3_8h"]
                rendered["O3_8h"] = "" if value is None else f"{value:.4f}".rstrip("0").rstrip(".")
                value = measures["hour_max_O3"]
                rendered["hour_max_O3"] = "" if value is None else f"{value:.4f}".rstrip("0").rstrip(".")
                writer.writerow(rendered)
                if any(measures[name] is not None for name in POLLUTANTS + ("O3_8h",)):
                    active_cities.add(city)
            if index % 30 == 0 or index == len(days):
                print(f"aggregate progress: {index}/{len(days)} dates, active cities {len(active_cities)}", flush=True)
            if index < len(days):
                today_headers, today = next_headers, following
                next_headers, following = read_day_values(daily_path(day + dt.timedelta(days=2)))

    stats["excluded_all_year_empty_cities"] = sorted(cities - active_cities)
    stats["filtered_empty_city_days"] = len(stats["excluded_all_year_empty_cities"]) * len(days)
    stats["cities"] = len(active_cities)
    stats["rows"] = 0
    stats["city_dates"] = 0
    stats["unmapped_cities"] = sorted(candidate_unmapped & active_cities)
    stats["aqi_derived_rows"] = 0
    stats["aqi_unavailable_reasons"] = {}
    stats["valid_days_by_pollutant"] = {kind: 0 for kind in POLLUTANTS + ("O3_8h", "O3_1h")}
    stats["invalid_hours_by_pollutant"] = {kind: 0 for kind in POLLUTANTS + ("O3_1h",)}
    stats["invalid_o3_days"] = 0
    stats["o3_over_limit_exception_days"] = 0
    filtered_output = output_path.with_suffix(output_path.suffix + ".filtered.part")
    with temp_output.open("r", encoding="utf-8", newline="") as source, filtered_output.open(
        "w", encoding="utf-8", newline=""
    ) as target:
        reader = csv.DictReader(source)
        writer = csv.DictWriter(target, fieldnames=columns)
        writer.writeheader()
        for row in reader:
            if row["city"] in active_cities:
                writer.writerow(row)
                stats["rows"] += 1
                stats["city_dates"] += 1
                for pollutant in POLLUTANTS:
                    if row[pollutant]:
                        stats["valid_days_by_pollutant"][pollutant] += 1
                    else:
                        stats["invalid_hours_by_pollutant"][pollutant] += 1
                if row["hour_max_O3"]:
                    stats["valid_days_by_pollutant"]["O3_1h"] += 1
                else:
                    stats["invalid_hours_by_pollutant"]["O3_1h"] += 1
                if row["O3_8h"]:
                    stats["valid_days_by_pollutant"]["O3_8h"] += 1
                else:
                    stats["invalid_o3_days"] += 1
                if "O3_8h_over_160_exception_" in row["data_quality"]:
                    stats["o3_over_limit_exception_days"] += 1
                if row["AQI"]:
                    stats["aqi_derived_rows"] += 1
                else:
                    reason = row["aqi_reason"] or "AQI未生成且未提供原因"
                    stats["aqi_unavailable_reasons"][reason] = stats["aqi_unavailable_reasons"].get(reason, 0) + 1
    temp_output.unlink(missing_ok=True)
    os.replace(filtered_output, output_path)
    return stats


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def write_source_metadata(
    results: dict[str, str],
    source_missing: list[str],
    stats: dict[str, Any],
) -> None:
    files = sorted(path for path in CACHE_DIR.rglob("*") if path.is_file() and path != HASH_MANIFEST)
    with HASH_MANIFEST.open("w", encoding="utf-8", newline="\n") as stream:
        for path in files:
            stream.write(f"{sha256_file(path)}  {path.relative_to(CACHE_DIR).as_posix()}\n")

    warnings: list[str] = []
    if source_missing:
        warnings.append(f"有{len(source_missing)}个源日期文件缺失或获取失败，详见source_missing_dates。")
    if stats.get("unmapped_cities"):
        warnings.append(f"有{len(stats['unmapped_cities'])}个活跃城市未映射省份，省份字段留空，详见statistics.unmapped_cities。")
    if stats.get("excluded_all_year_empty_cities"):
        warnings.append(
            f"排除{len(stats['excluded_all_year_empty_cities'])}个全年六项污染物均无有效日报值的源地区列，详见statistics.excluded_all_year_empty_cities。"
        )
    unavailable_aqi = stats.get("rows", 0) - stats.get("aqi_derived_rows", 0)
    if unavailable_aqi > 0:
        warnings.append(f"有{unavailable_aqi}行因日报指标缺失或超出AQI断点未派生AQI，具体原因见aqi_reason和统计项。")

    metadata = {
        "analysis_year": 2024,
        "name": "2024年中国城市空气质量监测数据",
        "source_url": SOURCE_PAGE,
        "file_url_template": SOURCE_TEMPLATE,
        "upstream_url": UPSTREAM_URL,
        "source_period": {"start": "2024-01-01", "end": "2024-12-31"},
        "supplemental_boundary_file": "2025-01-01 hourly city CSV, used for 2024-12-31 hour 00",
        "source_files_available": len(results),
        "source_file_status_counts": {
            status: sum(1 for value in results.values() if value == status)
            for status in ("downloaded", "cached", "missing", "failed")
        },
        "source_missing_dates": source_missing,
        "sha256_manifest": "source-cache/SHA256SUMS.txt",
        "cached_source_directory": "source-cache/daily/",
        "province_mapping": {
            "name": "中国行政区划省市县代码公开JSON",
            "url": ADMIN_URL,
            "cached_file": "source-cache/administrative-divisions-pcas-code.json",
            "sha256_in_manifest": True,
            "supplemental_source_label_groups": CITY_PROVINCE_OVERRIDES,
        },
        "processing": {
            "language": "仅Python标准库",
            "script": "scripts/prepare_data.py",
            "source_layout": "宽表；每行包含日期、小时（0—23）和污染物类型，各城市为列",
            "daily_window": "日期D取源文件D日小时1—23及D+1日小时00，共24个本地小时值。",
            "daily_means": "PM2.5、PM10、SO2、NO2、CO分别对有效小时值求算术平均，每日需至少20个有效小时值。",
            "o3_daily_max_8h": "按HJ 663—2013计算结束时标08—24的17个连续8小时均值，每窗至少6个有效小时；至少14个有效窗时有效。未达14窗但最大8小时值超过GB 3095—2012二级限值160 μg/m³时，按该标准仍视为有效。",
            "o3_hourly_max": "同一24小时自然日中，至少20个有效小时值时计算日最大1小时O3，供HJ 633—2012日报AQI使用；不足20小时留空。",
            "aqi": "使用HJ 633—2012日均浓度、O3日最大1小时值和日最大8小时值派生，保留AQI、中文等级、首要污染物、原因和口径说明。",
            "quality": "HJ 633—2012派生的中文AQI等级。",
            "data_quality": "分号分隔的污染物有效性、源文件缺失和省份映射标记。",
            "missing_values": "空值、非数值、负值和非有限值视为缺失；不插值。",
            "city_filter": "仅排除全年六项日报污染物均无有效值的源地区列；其他地区保留全年366个日期行。",
            "province_unknown_policy": "无法映射的源地区名称保持不变，省份留空；历史县级市、自治州和新区按明确静态省份分组补充映射，不更改源名称。",
            "standards": "按比赛指定历史口径GB 3095—2012、HJ 633—2012和HJ 663—2013处理2024数据。",
        },
        "output": "raw/air_quality_2024.csv",
        "statistics": stats,
        "warnings": warnings,
    }
    SOURCE_JSON.parent.mkdir(parents=True, exist_ok=True)
    SOURCE_JSON.write_text(json.dumps(metadata, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workers", type=int, default=4, help="parallel downloads (default: 4)")
    parser.add_argument("--attempts", type=int, default=5, help="retry attempts per daily file")
    parser.add_argument("--download-only", action="store_true", help="download and hash sources without aggregating")
    args = parser.parse_args()
    if args.workers < 1 or args.workers > 4:
        parser.error("--workers must be between 1 and 4")

    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    DAILY_CACHE.mkdir(parents=True, exist_ok=True)
    RAW_DIR.mkdir(parents=True, exist_ok=True)
    download_admin_json()

    required_dates = list(date_range(dt.date(2024, 1, 1), dt.date(2025, 1, 1)))
    results: dict[str, str] = {}
    missing: list[str] = []
    completed = 0
    downloaded_bytes = 0
    print(f"source download starting: {len(required_dates)} daily CSVs, {args.workers} workers", flush=True)
    with ThreadPoolExecutor(max_workers=args.workers) as executor:
        futures = {executor.submit(download_daily, day, args.attempts): day for day in required_dates}
        for future in as_completed(futures):
            status, size, detail = future.result()
            completed += 1
            day = futures[future]
            results[day.isoformat()] = status
            downloaded_bytes += size
            if status in ("missing", "failed"):
                missing.append(detail)
                print(f"source {status}: {detail}", flush=True)
            if completed % 10 == 0 or completed == len(required_dates):
                print(
                    f"download progress: {completed}/{len(required_dates)} files; "
                    f"cached/downloaded {downloaded_bytes / (1024 * 1024):.1f} MiB; "
                    f"missing/failed {len(missing)}",
                    flush=True,
                )

    if args.download_only:
        empty_stats: dict[str, Any] = {"rows": 0, "cities": 0, "dates": 366, "unmapped_cities": []}
        write_source_metadata(results, missing, empty_stats)
        return 0

    stats = aggregate(OUTPUT_CSV, results, missing)
    write_source_metadata(results, missing, stats)
    print(json.dumps({"output": str(OUTPUT_CSV), "source_json": str(SOURCE_JSON), "statistics": stats, "missing_source_dates": missing}, ensure_ascii=False, indent=2), flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
