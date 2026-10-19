"""需求书问法、指代消解和不支持约束的定向测试。"""
import unittest
from airq.planner import plan_query, PlanningError

DATA = {"years": [2024], "cities": ["北京", "上海", "广州", "深圳", "石家庄"],
        "provinces": ["北京市", "上海市", "广东省", "河北省"]}


class PlannerTests(unittest.TestCase):
    def test_official_examples(self):
        result = plan_query("找出2024年PM2.5年均浓度最高的5个城市，画出月度变化曲线", DATA)
        self.assertEqual((result["intent"], result["year"], result["pollutant"], result["top_n"]), ("ranking", 2024, "PM2.5", 5))
        result = plan_query("筛选PM2.5超标记录，统计各省份超标天数占比", DATA)
        self.assertEqual((result["intent"], result["group_by"], result["top_n"]), ("exceedance", "province", 100))
        result = plan_query("统计北京市夏季臭氧O₃超标总天数", DATA)
        self.assertEqual((result["intent"], result["cities"], result["months"], result["pollutant"]), ("exceedance", ["北京"], [6, 7, 8], "O3_8h"))

    def test_followup(self):
        result = plan_query("再看这几个城市的PM10超标情况", DATA, {"cities": ["北京", "上海"], "year": 2024, "pollutant": "PM2.5"})
        self.assertEqual(result["cities"], ["北京", "上海"])
        self.assertEqual(result["pollutant"], "PM10")
        self.assertEqual(result["intent"], "exceedance")

    def test_missing_or_unsupported_scope(self):
        for query in ["2023年PM2.5年均最高5城", "再看这些城市", "2024年6月1日北京PM2.5趋势", "预测明天空气质量", "长沙市PM2.5趋势", "你好"]:
            with self.subTest(query=query), self.assertRaises(PlanningError):
                plan_query(query, DATA)

    def test_months_and_single_pollutant(self):
        result = plan_query("北京3月至5月PM2.5月度变化", DATA)
        self.assertEqual(result["months"], [3, 4, 5])
        with self.assertRaises(PlanningError):
            plan_query("对比PM2.5和PM10", DATA)

    def test_pollutant_digits_are_not_month_filters(self):
        for pollutant in ("PM2.5", "PM10", "O3"):
            self.assertEqual(plan_query(f"北京2024年{pollutant}月均变化", DATA)["months"], [])
        result = plan_query("对比北京2024年1月至6月PM2.5月度变化", DATA)
        self.assertEqual((result["intent"], result["months"]), ("trend", [1, 2, 3, 4, 5, 6]))

    def test_province_does_not_select_same_named_city(self):
        data = {"years": [2024], "cities": ["吉林", "长春"], "provinces": ["吉林省"]}
        result = plan_query("吉林省各城市PM2.5超标天数", data)
        self.assertEqual(result["cities"], [])
        self.assertEqual(result["provinces"], ["吉林省"])


if __name__ == "__main__":
    unittest.main()
