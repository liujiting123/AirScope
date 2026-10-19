"""Synthetic unit inputs only; no AQI fixtures are used as demonstration data."""
import math
import unittest

from airq.aqi import calculate_daily_aqi


class DailyAQITests(unittest.TestCase):
    def values(self, **overrides):
        values = {"PM2.5": 0, "PM10": 0, "SO2": 0, "NO2": 0, "CO": 0, "O3_8h": 0}
        values.update(overrides)
        return values

    def test_official_50_and_100_concentration_limits(self):
        limits = {"PM2.5": (35, 75), "PM10": (50, 150), "SO2": (50, 150),
                  "NO2": (40, 80), "CO": (2, 4), "O3_8h": (100, 160)}
        for pollutant, pair in limits.items():
            for expected, concentration in zip((50, 100), pair):
                with self.subTest(pollutant=pollutant, expected=expected):
                    hourly = concentration if pollutant == "O3_8h" else 0
                    result = calculate_daily_aqi(self.values(**{pollutant: concentration}), hourly)
                    self.assertEqual(result["aqi"], expected)
                    self.assertEqual(result["quality"], "优" if expected == 50 else "良")
                    primary = ["O3" if pollutant == "O3_8h" else pollutant] if expected > 50 else []
                    self.assertEqual(result["primary_pollutants"], primary)

    def test_co_uses_milligrams_without_implicit_conversion(self):
        self.assertEqual(calculate_daily_aqi(self.values(CO=4), 0)["aqi"], 100)
        self.assertEqual(calculate_daily_aqi(self.values(CO=2.01), 0)["aqi"], 51)
        too_large = calculate_daily_aqi(self.values(CO=4000), 0)
        self.assertIsNone(too_large["aqi"])
        self.assertIn("最高断点", too_large["reason"])

    def test_round_up_and_primary_ties_after_rounding(self):
        result = calculate_daily_aqi(self.values(**{"PM2.5": 35.01, "PM10": 50.02}), 0)
        self.assertEqual(result["aqi"], 51)
        self.assertEqual(result["quality"], "良")
        self.assertEqual(result["primary_pollutants"], ["PM2.5", "PM10"])
        boundary = calculate_daily_aqi(self.values(NO2=80.0001), 0)
        self.assertEqual(boundary["aqi"], 101)
        self.assertEqual(boundary["quality"], "轻度污染")
        self.assertIn("非上游审核", result["note"])

    def test_zero_and_six_quality_classes(self):
        zero = calculate_daily_aqi(self.values(), 0)
        self.assertEqual((zero["aqi"], zero["quality"], zero["primary_pollutants"]), (0, "优", []))
        for concentration, index, quality in [(35, 50, "优"), (75, 100, "良"),
                                               (115, 150, "轻度污染"), (150, 200, "中度污染"),
                                               (250, 300, "重度污染"), (500, 500, "严重污染")]:
            with self.subTest(index=index):
                result = calculate_daily_aqi(self.values(**{"PM2.5": concentration}), 0)
                self.assertEqual((result["aqi"], result["quality"]), (index, quality))
        self.assertEqual(calculate_daily_aqi(self.values(**{"PM2.5": 250.01}), 0)["quality"], "严重污染")

    def test_missing_or_invalid_any_daily_indicator(self):
        for pollutant in self.values():
            for bad in (None, "", "bad", math.nan, math.inf, -1, True):
                with self.subTest(pollutant=pollutant, bad=bad):
                    result = calculate_daily_aqi(self.values(**{pollutant: bad}), 0)
                    self.assertIsNone(result["aqi"])
                    self.assertEqual(result["quality"], "")
                    self.assertEqual(result["primary_pollutants"], [])
                    self.assertIn(pollutant, result["reason"])
        missing = self.values()
        del missing["CO"]
        self.assertIsNone(calculate_daily_aqi(missing, 0)["aqi"])
        self.assertIsNone(calculate_daily_aqi(None, 0)["aqi"])

    def test_ozone_hourly_indicator_required_even_in_ordinary_range(self):
        values = self.values(O3_8h=100)
        for bad in (None, "bad", math.nan, math.inf, -1, False):
            with self.subTest(bad=bad):
                result = calculate_daily_aqi(values, bad)
                self.assertIsNone(result["aqi"])
                self.assertIn("1小时", result["reason"])
        # The hourly ozone subindex may dominate even when the 8-hour value <= 800.
        result = calculate_daily_aqi(values, 200)
        self.assertEqual(result["aqi"], 100)
        self.assertEqual(result["primary_pollutants"], ["O3"])

    def test_extreme_ozone_switch_is_strictly_above_800(self):
        exact = calculate_daily_aqi(self.values(O3_8h=800), 800)
        self.assertEqual((exact["aqi"], exact["quality"]), (300, "重度污染"))
        switched = calculate_daily_aqi(self.values(O3_8h=801), 900)
        self.assertEqual((switched["aqi"], switched["quality"]), (350, "严重污染"))
        self.assertEqual(switched["primary_pollutants"], ["O3"])
        self.assertIsNone(calculate_daily_aqi(self.values(O3_8h=801))["aqi"])
        self.assertEqual(calculate_daily_aqi(self.values(O3_8h=900), 1200)["aqi"], 500)
        out_of_table = calculate_daily_aqi(self.values(O3_8h=900), 1200.01)
        self.assertIsNone(out_of_table["aqi"])
        self.assertIn("最高断点", out_of_table["reason"])
        inconsistent = calculate_daily_aqi(self.values(O3_8h=801), 800)
        self.assertIsNone(inconsistent["aqi"])
        self.assertIn("需复核", inconsistent["reason"])

    def test_all_daily_table_endpoints_and_no_extrapolation(self):
        maxima = {"PM2.5": 500, "PM10": 600, "SO2": 2620, "NO2": 940, "CO": 60}
        for pollutant, maximum in maxima.items():
            with self.subTest(pollutant=pollutant):
                self.assertEqual(calculate_daily_aqi(self.values(**{pollutant: maximum}), 0)["aqi"], 500)
                self.assertIsNone(calculate_daily_aqi(self.values(**{pollutant: maximum + .01}), 0)["aqi"])


if __name__ == "__main__":
    unittest.main()
