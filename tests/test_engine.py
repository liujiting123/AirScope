"""Small synthetic fixtures exercise failure risks; not demonstration data."""
import calendar
import csv
from contextlib import closing
from datetime import date, timedelta
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest

from airq.analysis import analyze
from airq.data import load_dataset
from airq.standards import annual_completeness, monthly_completeness


class EngineTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        (self.root / "data" / "raw").mkdir(parents=True)

    def write(self, name, rows, fields=None, encoding="utf-8"):
        if fields is None:
            fields = list(dict.fromkeys(key for row in rows for key in row))
        with (self.root / "data" / "raw" / name).open("w", encoding=encoding, newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=fields)
            writer.writeheader()
            writer.writerows(rows)

    def query(self, intent="ranking", **kwargs):
        return analyze(self.root, {"intent": intent, "pollutant": "PM2.5", "year": 2024, "top_n": 10, **kwargs})

    def test_strict_threshold_missing_and_peaks(self):
        self.write("daily.csv", [{"date": f"2024-01-{i:02}", "city": "甲", "PM2.5": value}
                                 for i, value in enumerate([75, 75.01, "", "bad", -1, 10000], 1)])
        result = self.query("exceedance")
        row = result["table"]["rows"][0]
        self.assertEqual((row["valid_days"], row["exceeded_days"], row["missing_days"]), (3, 2, 3))
        self.assertEqual(row["rate"], 66.67)
        ranking = self.query()["table"]["rows"][0]
        self.assertEqual(ranking["mean"], round((75 + 75.01 + 10000) / 3, 2))
        self.assertEqual(ranking["assessment"], "样本期均值参考")
        self.assertIsNone(ranking["exceeds_annual"])
        self.assertEqual(result["quality"]["negative_numeric_cells"], 1)
        self.assertEqual(result["quality"]["invalid_numeric_cells"], 1)

    def test_all_missing_never_compliant(self):
        self.write("empty-values.csv", [{"date": "2024-01-01", "city": "甲", "PM2.5": ""}])
        ranking = self.query()["table"]["rows"][0]
        self.assertEqual(ranking["assessment"], "无有效数据")
        self.assertIsNone(ranking["mean"])
        self.assertIsNone(ranking["exceeds_annual"])
        daily = self.query("exceedance")["table"]["rows"][0]
        self.assertIsNone(daily["rate"])
        self.assertEqual(daily["valid_days"], 0)

    def test_chunked_dedup_alias_encoding_and_cache(self):
        fields = ["日期", "城市名称", "省份", "PM2.5(μg/m³)", "PM10"]
        self.write("01.csv", [{"日期": "2024年1月1日", "城市名称": "甲", "PM2.5(μg/m³)": "10"},
                              {"日期": "20240102", "城市名称": "甲", "PM2.5(μg/m³)": "20"}], fields, "gb18030")
        self.write("02.csv", [{"date": "2024-01-01", "city": "甲", "province": "乙省", "PM2.5": "99", "PM10": "30"},
                              {"date": "2024/1/2", "city": "甲", "PM10": "40"},
                              {"date": "2024-02-30", "city": "甲", "PM2.5": "50"}])
        source = {"name": "单测来源", "source_url": "https://example.test/data", "rows": 9999}
        (self.root / "data" / "source.json").write_text(json.dumps(source), encoding="utf-8")
        meta = load_dataset(self.root, chunk_size=1)
        self.assertEqual((meta["rows"], meta["quality"]["duplicate_rows"], meta["quality"]["invalid_date_rows"]), (2, 2, 1))
        self.assertEqual(meta["name"], "单测来源")
        self.assertEqual(meta["provinces"], ["乙省", "未知"])
        with closing(sqlite3.connect(self.root / "data" / "airquality.sqlite")) as conn:
            self.assertEqual(conn.execute("SELECT PM2_5,PM10,province FROM daily ORDER BY date").fetchall(), [(10.0, 30.0, "乙省"), (20.0, 40.0, "未知")])
        cached = load_dataset(self.root)
        self.assertTrue(cached["cache_reused"])
        self.assertEqual(cached["sha256"], meta["sha256"])
        self.write("03.csv", [{"date": "2024-01-03", "city": "甲", "PM2.5": 5}])
        changed = load_dataset(self.root)
        self.assertFalse(changed["cache_reused"])
        self.assertEqual(changed["rows"], 3)
        self.assertNotEqual(changed["sha256"], meta["sha256"])

    def test_annual_validity_requires_year_total_and_every_month(self):
        rows = []
        for month in range(1, 13):
            count = 29 if month == 1 else 25 if month == 2 else 27
            for day in range(1, count + 1):
                rows.append({"date": f"2024-{month:02}-{day:02}", "city": "完整", "PM2.5": 35})
        # High annual count alone cannot repair a deficient month.
        day = date(2024, 1, 1)
        while day.year == 2024:
            if not (day.month == 3 and day.day > 26):
                rows.append({"date": day.isoformat(), "city": "缺月", "PM2.5": 35.00001})
            day += timedelta(days=1)
        self.write("year.csv", rows)
        result = {row["city"]: row for row in self.query()["table"]["rows"]}
        self.assertEqual(result["完整"]["valid_days"], 324)
        self.assertEqual(result["完整"]["assessment"], "年均达标")
        self.assertFalse(result["完整"]["exceeds_annual"])
        self.assertEqual(result["缺月"]["completeness"]["incomplete_months"], [3])
        self.assertEqual(result["缺月"]["assessment"], "样本期均值参考")
        partial = self.query(months=[1, 2])["table"]["rows"]
        self.assertTrue(all(row["assessment"] == "样本期均值参考" for row in partial))
        counts = {month: 25 if month == 2 else 27 for month in range(1, 13)}
        self.assertEqual(sum(counts.values()), 322)
        self.assertFalse(annual_completeness(2024, counts)["complete"])

    def test_annual_boundary_uses_unrounded_mean(self):
        rows = []
        day = date(2023, 1, 1)
        while day.year == 2023:
            rows.append({"date": day.isoformat(), "city": "甲", "PM2.5": 35.00001})
            day += timedelta(days=1)
        self.write("year.csv", rows)
        row = self.query(year=2023)["table"]["rows"][0]
        self.assertEqual(row["mean"], 35.0)
        self.assertTrue(row["exceeds_annual"])
        self.assertEqual(row["assessment"], "年均超标")

    def test_leap_month_and_daily_weighted_means(self):
        self.assertEqual(monthly_completeness(2024, 2, 25)["calendar_days"], 29)
        self.assertEqual(monthly_completeness(2023, 2, 25)["calendar_days"], 28)
        self.assertFalse(monthly_completeness(2024, 2, 24)["complete"])
        self.assertFalse(monthly_completeness(2024, 4, 26)["complete"])
        rows = [{"date": "2024-01-01", "city": "甲", "PM2.5": 0}]
        rows += [{"date": f"2024-02-{day:02}", "city": "甲", "PM2.5": 100} for day in (1, 2, 29)]
        self.write("weighted.csv", rows)
        self.assertEqual(self.query()["table"]["rows"][0]["mean"], 75)
        months = self.query("trend")["table"]["rows"]
        self.assertEqual([row["mean"] for row in months], [0, 100])
        self.assertFalse(any(row["complete"] for row in months))
        self.assertIsNone(self.query("trend")["standard"]["limit"])

    def test_province_exceedance_denominator_is_city_days(self):
        self.write("province.csv", [
            {"date": "2024-01-01", "city": "甲", "province": "省", "PM2.5": 100},
            {"date": "2024-01-02", "city": "甲", "province": "省", "PM2.5": 75},
            {"date": "2024-01-01", "city": "乙", "province": "省", "PM2.5": 100},
            {"date": "2024-01-02", "city": "乙", "province": "省", "PM2.5": ""},
        ])
        row = self.query("exceedance", group_by="province")["table"]["rows"][0]
        self.assertEqual((row["valid_days"], row["exceeded_days"], row["rate"]), (3, 2, 66.67))
        self.assertEqual(row["any_city_exceeded_dates"], 1)
        self.assertEqual(row["valid_dates"], 2)
        self.assertIsNone(self.query(group_by="province")["table"]["rows"][0]["exceeds_annual"])

    def test_o3_semantics_and_threshold(self):
        self.write("o3.csv", [{"date": "2024-01-01", "city": "普通", "O3": 999},
                              {"date": "2024-01-02", "city": "普通", "O3": 999}])
        result = self.query("exceedance", pollutant="O3_8h")
        self.assertEqual(result["quality"]["selected_valid_city_days"], 0)
        self.assertIsNone(result["table"]["rows"][0]["rate"])
        self.assertTrue(any("普通 O3" in warning for warning in result["warnings"]))
        with self.assertRaisesRegex(ValueError, "不能作为"):
            self.query(pollutant="O3")
        self.write("o3-explicit.csv", [{"日期": "2024-01-01", "城市": "八小时", "臭氧日最大8小时平均": 160},
                                       {"日期": "2024-01-02", "城市": "八小时", "臭氧日最大8小时平均": 161}])
        daily = self.query("exceedance", pollutant="O3_8h", cities=["八小时"])["table"]["rows"][0]
        self.assertEqual((daily["valid_days"], daily["exceeded_days"], daily["rate"]), (2, 1, 50))
        annual = self.query(pollutant="O3_8h", cities=["八小时"])
        self.assertIsNone(annual["standard"]["limit"])
        self.assertIsNone(annual["table"]["rows"][0]["exceeds_annual"])

    def test_seasonal_uses_daily_weights_and_calendar_winter(self):
        self.write("seasons.csv", [
            {"date": "2024-01-01", "city": "甲", "PM2.5": 10},
            {"date": "2024-02-01", "city": "甲", "PM2.5": 30},
            {"date": "2024-12-01", "city": "甲", "PM2.5": 50},
            {"date": "2024-12-02", "city": "甲", "PM2.5": 70},
            {"date": "2024-03-01", "city": "甲", "PM2.5": 100},
        ])
        result = self.query("seasonal")
        rows = {row["season"]: row for row in result["table"]["rows"]}
        self.assertEqual(rows["冬季"]["mean"], 40)
        self.assertEqual(rows["冬季"]["valid_days"], 4)
        self.assertFalse(rows["冬季"]["complete"])
        self.assertEqual(rows["春季"]["mean"], 100)
        self.assertIsNone(result["standard"]["limit"])
        self.assertTrue(any("同一日历年" in warning for warning in result["warnings"]))

    def test_anomaly_adjacent_calendar_days_and_no_deletion(self):
        self.write("jumps.csv", [{"date": "2024-01-01", "city": "甲", "PM2.5": 10},
                                 {"date": "2024-01-03", "city": "甲", "PM2.5": 1000},
                                 {"date": "2024-01-04", "city": "甲", "PM2.5": 10},
                                 {"date": "2024-01-05", "city": "乙", "PM2.5": 2000}])
        result = self.query("anomaly")
        self.assertEqual(len(result["table"]["rows"]), 1)
        self.assertEqual(result["table"]["rows"][0]["date"], "2024-01-04")
        self.assertEqual(result["table"]["rows"][0]["delta"], -990)
        self.assertEqual(load_dataset(self.root)["rows"], 4)
        self.assertEqual(self.query(cities=["甲"])["table"]["rows"][0]["mean"], 340)


if __name__ == "__main__":
    unittest.main()
