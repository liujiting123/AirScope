"""Stream CSV shards into a bounded-memory, cached SQLite daily dataset."""
from __future__ import annotations

import codecs
from contextlib import closing
import csv
from datetime import date, datetime
import hashlib
import json
import math
from pathlib import Path
import re
import sqlite3
import unicodedata

from .standards import LIMITS

IMPORT_VERSION = "airq-daily-v3"
DB_NAME = "airquality.sqlite"
POLLUTANT_COLUMNS = {"PM2.5": "PM2_5", "PM10": "PM10", "SO2": "SO2", "NO2": "NO2",
                     "CO": "CO", "O3_8h": "O3_8h", "AQI": "AQI"}
CANONICAL_COLUMNS = ("date", "city", "province", *POLLUTANT_COLUMNS, "quality")
UNKNOWN_PROVINCE = "未知"


def _key(value: str) -> str:
    value = unicodedata.normalize("NFKC", str(value)).strip().lower()
    value = re.sub(r"[([][^)\]]*[)\]]", "", value)
    return re.sub(r"[\s_.\-/·]", "", value)


_ALIAS_GROUPS = {
    "date": ("date", "日期", "时间", "监测日期", "监测时间", "统计日期", "datetime", "day"),
    "city": ("city", "cityname", "city_name", "城市", "城市名称", "市", "地区", "城市名"),
    "province": ("province", "province_name", "provincename", "省份", "省", "省份名称", "省名", "省/直辖市"),
    "PM2.5": ("PM2.5", "PM25", "PM2_5", "细颗粒物", "PM2.5浓度", "PM2.5日均值", "PM2.5平均浓度"),
    "PM10": ("PM10", "可吸入颗粒物", "PM10浓度", "PM10日均值", "PM10平均浓度"),
    "SO2": ("SO2", "二氧化硫", "SO2浓度", "SO2日均值"),
    "NO2": ("NO2", "二氧化氮", "NO2浓度", "NO2日均值"),
    "CO": ("CO", "一氧化碳", "CO浓度", "CO日均值"),
    "O3_8h": ("O3_8h", "O3-8h", "O3 8小时", "O3_8h_24h", "O3_8h_max", "O3_8h最大值",
               "臭氧8小时", "臭氧8小时平均", "臭氧日最大8小时平均", "日最大8小时臭氧",
               "O3日最大8小时平均", "O3最大8小时平均", "日最大8小时平均臭氧", "臭氧8小时滑动平均"),
    "AQI": ("AQI", "空气质量指数", "AQI指数"),
    "quality": ("quality", "quality_level", "level", "空气质量", "空气质量等级", "质量等级", "质量状况", "质量"),
}
ALIASES = {_key(alias): canonical for canonical, aliases in _ALIAS_GROUPS.items() for alias in aliases}
MISSING = {"", "-", "--", "—", "–", "na", "n/a", "nan", "null", "none", "缺测", "无", "未监测", "暂无"}


def _sha_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _encoding(path: Path) -> str:
    # A streaming decode pass avoids a late UTF-8 error after partial ingestion.
    for encoding in ("utf-8-sig", "gb18030"):
        decoder = codecs.getincrementaldecoder(encoding)(errors="strict")
        try:
            with path.open("rb") as handle:
                for block in iter(lambda: handle.read(65536), b""):
                    decoder.decode(block)
                decoder.decode(b"", final=True)
            return encoding
        except UnicodeDecodeError:
            continue
    raise ValueError(f"CSV 编码无法识别（支持 UTF-8、GB18030）：{path.name}")


def _parse_date(value: str | None) -> str | None:
    value = unicodedata.normalize("NFKC", str(value or "")).strip()
    if not value:
        return None
    if re.fullmatch(r"\d{8}(?:\.0)?", value):
        try:
            return datetime.strptime(value[:8], "%Y%m%d").date().isoformat()
        except ValueError:
            return None
    match = re.match(r"^(\d{4})[-/年.](\d{1,2})[-/月.](\d{1,2})(?:日|(?:[ T].*)?)$", value)
    if match:
        try:
            return date(*(int(part) for part in match.groups())).isoformat()
        except ValueError:
            return None
    return None


def _number(value: str | None, quality: dict, pollutant: str) -> float | None:
    text = unicodedata.normalize("NFKC", str(value or "")).strip()
    if text.lower() in MISSING:
        quality["missing_numeric_cells"] += 1
        quality["missing_by_pollutant"][pollutant] += 1
        return None
    if re.fullmatch(r"[+\-]?\d{1,3}(?:,\d{3})+(?:\.\d+)?", text):
        text = text.replace(",", "")
    try:
        number = float(text)
        if not math.isfinite(number):
            raise ValueError
    except ValueError:
        quality["invalid_numeric_cells"] += 1
        quality["missing_by_pollutant"][pollutant] += 1
        return None
    if number < 0:
        quality["negative_numeric_cells"] += 1
        quality["missing_by_pollutant"][pollutant] += 1
        return None
    return number


def _new_quality() -> dict:
    return {"input_rows": 0, "accepted_rows": 0, "duplicate_rows": 0,
            "invalid_date_rows": 0, "missing_city_rows": 0, "missing_numeric_cells": 0,
            "invalid_numeric_cells": 0, "negative_numeric_cells": 0,
            "missing_by_pollutant": {name: 0 for name in POLLUTANT_COLUMNS},
            "ordinary_o3_columns_ignored": 0, "rows_without_pollutants": 0,
            "unknown_province_rows": 0, "files_skipped": 0,
            "cleaning_policy": "缺失、非数和负值记为空；污染峰值保留；城市+日期唯一，重复行优先保留首个非空值。"}


def _read_source(data_dir: Path) -> dict:
    source = data_dir / "source.json"
    if not source.exists():
        return {}
    try:
        result = json.loads(source.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise ValueError(f"source.json 无法读取：{error}") from error
    if not isinstance(result, dict):
        raise ValueError("source.json 必须是 JSON 对象。")
    return result


def load_dataset(root: Path, chunk_size: int = 5000) -> dict:
    """Import CSVs in ``data/raw`` and return source plus verified dataset metadata.

    Memory holds at most ``chunk_size`` raw records. A duplicate city/date fills
    only missing cells; conflicting nonmissing observations retain the first
    value in sorted file order. A changed input rebuilds in a single transaction.
    """
    if isinstance(chunk_size, bool) or not isinstance(chunk_size, int) or chunk_size <= 0:
        raise ValueError("chunk_size 必须是正整数。")
    root = Path(root)
    data_dir = root / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    raw_dir = data_dir / "raw"
    paths = sorted(raw_dir.glob("*.csv"), key=lambda path: path.name) if raw_dir.exists() else []
    source = _read_source(data_dir)
    files = [{"name": path.name, "sha256": _sha_file(path), "bytes": path.stat().st_size} for path in paths]
    signature = hashlib.sha256(json.dumps({"version": IMPORT_VERSION, "files": files, "source": source},
                                         ensure_ascii=False, sort_keys=True).encode()).hexdigest()
    database = data_dir / DB_NAME
    with closing(sqlite3.connect(database)) as connection, connection:
        connection.execute("CREATE TABLE IF NOT EXISTS dataset_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
        cached = connection.execute("SELECT value FROM dataset_meta WHERE key='dataset'").fetchone()
        if cached:
            try:
                meta = json.loads(cached[0])
                exists = connection.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='daily'").fetchone()
                if meta.get("sha256") == signature and exists:
                    meta["cache_reused"] = True
                    return meta
            except (TypeError, ValueError):
                pass
        quality = _new_quality()
        if source.get("quality") is not None:
            quality["source_quality"] = source["quality"]
        warnings = list(source.get("warnings", []))
        # DDL and data replacement are transactional, preserving the old cache on failure.
        connection.execute("BEGIN IMMEDIATE")
        connection.execute("DROP TABLE IF EXISTS daily")
        connection.execute("CREATE TABLE daily (date TEXT NOT NULL, city TEXT NOT NULL, province TEXT NOT NULL, "
                           "PM2_5 REAL, PM10 REAL, SO2 REAL, NO2 REAL, CO REAL, O3_8h REAL, AQI REAL, quality TEXT, "
                           "PRIMARY KEY (city, date)) WITHOUT ROWID")
        columns = ["date", "city", "province", *POLLUTANT_COLUMNS.values(), "quality"]
        placeholders = ",".join("?" for _ in columns)
        update = [f'"{column}"=COALESCE(daily."{column}",excluded."{column}")'
                  for column in [*POLLUTANT_COLUMNS.values(), "quality"]]
        update.append("province=CASE WHEN daily.province='未知' THEN excluded.province ELSE daily.province END")
        insert = f"INSERT INTO daily ({','.join(columns)}) VALUES ({placeholders}) ON CONFLICT(city,date) DO UPDATE SET {','.join(update)}"
        batch = []
        for path, file_meta in zip(paths, files):
            file_meta["encoding"] = _encoding(path)
            file_meta["rows"] = 0
            with path.open(encoding=file_meta["encoding"], newline="") as handle:
                reader = csv.DictReader(handle)
                fields = reader.fieldnames or []
                mapping = {}
                for field in fields:
                    canonical = ALIASES.get(_key(field))
                    if canonical and canonical not in mapping:
                        mapping[canonical] = field
                ignored_o3 = any(_key(field) in ("o3", "臭氧", "o3浓度", "臭氧浓度") for field in fields)
                if ignored_o3:
                    quality["ordinary_o3_columns_ignored"] += 1
                    warnings.append(f"{path.name} 的普通 O3 列未说明8小时口径，未导入 O3_8h。")
                if "date" not in mapping or "city" not in mapping:
                    quality["files_skipped"] += 1
                    warnings.append(f"{path.name} 缺少可识别的日期/城市列，已跳过。")
                    continue
                for raw in reader:
                    quality["input_rows"] += 1
                    file_meta["rows"] += 1
                    parsed_date = _parse_date(raw.get(mapping["date"]))
                    if parsed_date is None:
                        quality["invalid_date_rows"] += 1
                        continue
                    city = str(raw.get(mapping["city"]) or "").strip()
                    if city.lower() in MISSING:
                        quality["missing_city_rows"] += 1
                        continue
                    province = str(raw.get(mapping.get("province", "")) or "").strip()
                    if province.lower() in MISSING:
                        province = UNKNOWN_PROVINCE
                    values = [_number(raw.get(mapping.get(name, "")), quality, name) for name in POLLUTANT_COLUMNS]
                    quality_value = str(raw.get(mapping.get("quality", "")) or "").strip() or None
                    batch.append((parsed_date, city, province, *values, quality_value))
                    quality["accepted_rows"] += 1
                    if len(batch) >= chunk_size:
                        connection.executemany(insert, batch)
                        batch.clear()
        if batch:
            connection.executemany(insert, batch)
        connection.execute("CREATE INDEX daily_date_idx ON daily(date)")
        connection.execute("CREATE INDEX daily_province_idx ON daily(province,date)")
        rows, date_min, date_max = connection.execute("SELECT COUNT(*),MIN(date),MAX(date) FROM daily").fetchone()
        quality["duplicate_rows"] = quality["accepted_rows"] - rows
        quality["unknown_province_rows"] = connection.execute("SELECT COUNT(*) FROM daily WHERE province=?", (UNKNOWN_PROVINCE,)).fetchone()[0]
        quality["rows_without_pollutants"] = connection.execute("SELECT COUNT(*) FROM daily WHERE " + " AND ".join(f'"{column}" IS NULL' for name, column in POLLUTANT_COLUMNS.items() if name != "AQI")).fetchone()[0]
        quality["valid_by_pollutant"] = {name: connection.execute(f'SELECT COUNT("{column}") FROM daily').fetchone()[0]
                                         for name, column in POLLUTANT_COLUMNS.items()}
        meta = dict(source)
        meta.update({"name": source.get("name") or source.get("dataset_name") or "本地空气质量 CSV 数据",
                     "rows": rows, "cities": [row[0] for row in connection.execute("SELECT DISTINCT city FROM daily ORDER BY city")],
                     "provinces": [row[0] for row in connection.execute("SELECT DISTINCT province FROM daily ORDER BY province")],
                     "years": [int(row[0]) for row in connection.execute("SELECT DISTINCT substr(date,1,4) FROM daily ORDER BY 1")],
                     "date_min": date_min or "", "date_max": date_max or "", "quality": quality, "files": files,
                     "sha256": signature, "warnings": warnings, "cache_reused": False})
        connection.execute("INSERT OR REPLACE INTO dataset_meta(key,value) VALUES('dataset',?)", (json.dumps(meta, ensure_ascii=False),))
        return meta
