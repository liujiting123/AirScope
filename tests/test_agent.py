"""临时夹具只验证工具链/追问/导出，不属于演示数据。"""
import csv
import json
from pathlib import Path
import tempfile
import unittest

from airq.agent import AirQualityAgent, validate_result


class AgentIntegrationTests(unittest.TestCase):
    def test_rank_followup_exports_and_trace(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root/"data"/"raw").mkdir(parents=True)
            with (root/"data"/"raw"/"fixture.csv").open("w", encoding="utf-8", newline="") as f:
                writer = csv.writer(f)
                writer.writerow(["date", "city", "province", "PM2.5", "PM10"])
                for i in range(6):
                    for day in range(1, 6):
                        writer.writerow([f"2024-01-{day:02}", f"测试城市{i}", "测试省", 20+i*10, 100+i*20])
            agent = AirQualityAgent(root)
            first = agent.run("2024年PM2.5年均最高5个城市", "test")
            self.assertEqual(len(first["table"]["rows"]), 5)
            self.assertEqual(len(first["context"]["cities"]), 5)
            second = agent.run("上一轮的城市PM10超标情况", "test")
            self.assertEqual(second["plan"]["cities"], first["context"]["cities"])
            self.assertEqual(len(second["table"]["rows"]), 5)
            for key, path in second["downloads"].items():
                self.assertTrue((root/path.lstrip("/")).is_file(), key)
            persisted = json.loads((root/second["downloads"]["json"].lstrip("/")).read_text())
            self.assertEqual(persisted["trace"][-1]["tool"], "visualize")
            self.assertEqual(persisted["context"], second["context"])
            self.assertEqual(len(agent.history()), 2)
            quiet = agent.run("测试城市0的PM2.5异常跳变", "test")
            self.assertTrue(quiet["ok"])
            self.assertEqual(quiet["table"]["rows"], [])

    def test_result_validator_checks_actual_arithmetic(self):
        result = {"table": {"columns": [], "rows": [{"valid_days": 10, "exceeded_days": 3, "rate": 30}]}, "charts": []}
        self.assertTrue(validate_result(result)["passed"])
        result["table"]["rows"][0]["rate"] = 40
        with self.assertRaises(ValueError):
            validate_result(result)


if __name__ == "__main__":
    unittest.main()
