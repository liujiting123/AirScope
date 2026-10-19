"""Offline model doubles verify real tool execution, repair, context and failure."""
from copy import deepcopy
import csv
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from airq.analysis import analyze
from airq.llm_agent import AgentExecutionError, DeepSeekAirQualityAgent


def completion(content=None, calls=None):
    message = {"role": "assistant", "content": content, "reasoning_content": "PRIVATE_REASONING_MUST_NOT_PERSIST"}
    if calls:
        message["tool_calls"] = [
            {"id": f"call-{index}-{name}", "type": "function",
             "function": {"name": name, "arguments": json.dumps(arguments, ensure_ascii=False)}}
            for index, (name, arguments) in enumerate(calls)]
    return {"choices": [{"message": message}], "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15}}


class MockClient:
    model = "test-deepseek"

    def __init__(self, responses):
        self.responses = iter(responses)
        self.requests = []

    def complete(self, messages, tools=None, tool_choice="auto"):
        self.requests.append(deepcopy(messages))
        response = next(self.responses)
        if isinstance(response, Exception):
            raise response
        return response(messages) if callable(response) else deepcopy(response)


class LlmAgentTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        raw = self.root / "data" / "raw"
        raw.mkdir(parents=True)
        with (raw / "fixture.csv").open("w", encoding="utf-8", newline="") as handle:
            writer = csv.writer(handle)
            writer.writerow(["date", "city", "province", "PM2.5", "PM10"])
            writer.writerows([
                ["2024-01-01", "甲市", "甲省", 20, 100],
                ["2024-01-02", "甲市", "甲省", 40, 200],
                ["2024-01-01", "乙市", "乙省", 80, 300],
                ["2024-01-02", "乙市", "乙省", 100, 400],
            ])

    def plan(self, **kwargs):
        return {"intent": "ranking", "pollutant": "PM2.5", "year": 2024, "top_n": 2, **kwargs}

    def test_tool_observations_reach_model_and_multiple_real_results_export(self):
        def final(messages):
            observations = [json.loads(message["content"]) for message in messages if message["role"] == "tool"]
            self.assertEqual(observations[0]["cities"], ["乙市", "甲市"])
            self.assertEqual(observations[1]["table"]["rows"][0]["mean"], 90)
            self.assertEqual(observations[2]["table"]["rows"][0]["mean"], 350)
            self.assertTrue(observations[2]["validation"]["passed"])
            return completion("乙市PM2.5样本均值90 μg/m³；PM10月均350 μg/m³。样本不满足年均完整性要求。")

        client = MockClient([
            completion(calls=[("get_dataset_info", {})]),
            completion(calls=[("analyze_air_quality", {"plan": self.plan()}),
                              ("analyze_air_quality", {"plan": self.plan(intent="trend", pollutant="PM10", cities=["乙市"])})]),
            completion(calls=[("validate_result", {"analysis_id": "analysis-1"}),
                              ("render_visualization", {"analysis_id": "analysis-1"})]),
            final,
        ])
        events = []
        agent = DeepSeekAirQualityAgent(self.root, client)
        result = agent.run("比较两种污染物", on_event=events.append)
        self.assertEqual(result["provider"], "deepseek")
        self.assertEqual(result["usage"]["total_tokens"], 60)
        self.assertEqual(len(result["analyses"]), 2)
        self.assertEqual(result["plan"]["pollutant"], "PM10")
        self.assertEqual(result["context"]["cities"], ["乙市"])
        for artifact in [result, *result["analyses"]]:
            for path in artifact["downloads"].values():
                self.assertTrue((self.root / path.lstrip("/")).is_file(), path)
        saved = json.loads((self.root / result["downloads"]["json"].lstrip("/")).read_text())
        self.assertEqual(saved["trace"][-1]["tool"], "export_result")
        self.assertNotIn("PRIVATE_REASONING", json.dumps(saved, ensure_ascii=False))
        self.assertEqual([event["type"] for event in events].count("answer"), 1)
        starts = [event["call_id"] for event in events if event["type"] == "tool_start"]
        ends = [event["call_id"] for event in events if event["type"] == "tool_end"]
        self.assertEqual(starts, ends)
        self.assertEqual([entry["tool"] for entry in result["trace"][:3]],
                         ["get_dataset_info", "analyze_air_quality", "analyze_air_quality"])

    def test_invalid_constraints_are_tool_errors_and_model_can_repair(self):
        invalid = [self.plan(year=2025), self.plan(cities=["不存在市"]), self.plan(top_n=1000),
                   self.plan(top_n=2.5), self.plan(start_date="2024-01-02"), self.plan(months=[2]),
                   self.plan(cities=["甲市"], provinces=["乙省"])]
        bad_calls = completion(calls=[("analyze_air_quality", {"plan": plan}) for plan in invalid] +
                               [("analyze_air_quality", {}), ("analyze_air_quality", {})])
        bad_calls["choices"][0]["message"]["tool_calls"][-2]["function"]["arguments"] = '{"plan":{"top_n":1e999}}'
        bad_calls["choices"][0]["message"]["tool_calls"][-1]["function"]["arguments"] = '{"plan":{},"plan":{}}'

        def repair(messages):
            errors = [json.loads(message["content"]) for message in messages if message["role"] == "tool"]
            self.assertEqual(len(errors), len(invalid) + 2)
            self.assertTrue(all(not error["ok"] for error in errors))
            return completion(calls=[("analyze_air_quality", {"plan": self.plan(cities=["甲市"])})])

        client = MockClient([
            bad_calls,
            repair, completion("甲市样本均值30 μg/m³，不作完整年均判定。"),
        ])
        with patch("airq.llm_agent.analyze", wraps=analyze) as engine:
            result = DeepSeekAirQualityAgent(self.root, client).run("查询甲市")
        self.assertEqual(engine.call_count, 1)
        self.assertEqual(len(result["analyses"]), 1)
        self.assertEqual(result["table"]["rows"][0]["mean"], 30)
        self.assertEqual(sum(item["status"] == "error" for item in result["trace"]), len(invalid) + 2)

    def test_followup_context_ordinary_answer_and_saved_result_restoration(self):
        def followup(messages):
            self.assertTrue(any(message["role"] == "user" and message["content"] == "找出最高城市" for message in messages))
            prior = [json.loads(message["content"]) for message in messages if message["role"] == "tool"]
            self.assertEqual(prior[0]["selected_cities"], ["乙市"])
            return completion(calls=[("analyze_air_quality", {"plan": self.plan(intent="exceedance", pollutant="PM10", cities=["乙市"])})])

        client = MockClient([
            completion(calls=[("analyze_air_quality", {"plan": self.plan(top_n=1)})]),
            completion("乙市最高。"), followup, completion("乙市PM10两天均超标。"),
            completion("PM2.5是空气中粒径不超过2.5微米的颗粒物。"),
        ])
        agent = DeepSeekAirQualityAgent(self.root, client)
        first = agent.run("找出最高城市", "s")
        second = agent.run("这些城市PM10呢", "s")
        self.assertEqual(second["context"]["cities"], first["context"]["cities"])
        ordinary = agent.run("什么是PM2.5", "s")
        self.assertFalse(ordinary["analysis_completed"])
        self.assertEqual(ordinary["charts"], [])
        self.assertEqual(ordinary["table"]["rows"], [])
        self.assertTrue(ordinary["answer"])
        self.assertEqual(len(agent.history()), 3)
        restored_client = MockClient([completion("已记住乙市与PM10的历史结果。")])
        restored = DeepSeekAirQualityAgent(self.root, restored_client)
        restored.restore_session("s", second)
        restored.run("上一轮是什么", "s")
        self.assertIn("乙市", json.dumps(restored_client.requests[0], ensure_ascii=False))
        self.assertIn("PM10", json.dumps(restored_client.requests[0], ensure_ascii=False))

    def test_api_failure_and_round_limit_never_create_success_or_history(self):
        agent = DeepSeekAirQualityAgent(self.root, MockClient([RuntimeError("provider failure")]))
        with self.assertRaisesRegex(AgentExecutionError, "API调用失败"):
            agent.run("请分析数据")
        self.assertEqual(agent.history(), [])
        self.assertEqual(agent.sessions, {})
        partially_done = MockClient([
            completion(calls=[("analyze_air_quality", {"plan": self.plan()})]),
            RuntimeError("provider failed after tool execution"),
        ])
        with self.assertRaisesRegex(AgentExecutionError, "API调用失败") as failed:
            DeepSeekAirQualityAgent(self.root, partially_done).run("先算后失败")
        self.assertEqual(failed.exception.trace[0]["tool"], "analyze_air_quality")
        self.assertEqual(agent.history(), [])
        never_finishes = MockClient([completion(calls=[("get_dataset_info", {})]) for _ in range(10)])
        agent = DeepSeekAirQualityAgent(self.root, never_finishes)
        with self.assertRaisesRegex(AgentExecutionError, "10轮") as caught:
            agent.run("持续调用")
        self.assertEqual(len(never_finishes.requests), 10)
        self.assertEqual(len(caught.exception.trace), 10)
        self.assertEqual(agent.history(), [])


if __name__ == "__main__":
    unittest.main()
