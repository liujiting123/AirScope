"""工具驱动的离线 ReAct 执行循环。

每一步基于前一步 observation 决定继续或停止。规划只生成约束，
真实读取、清洗、统计、校验、绘图由独立工具执行。日志是工具摘要，
不是大模型思维过程。所有输出均可由本地 CSV 重算。
"""
from __future__ import annotations
from datetime import datetime
import json
import math
from pathlib import Path
import time
import uuid

from .data import load_dataset
from .analysis import analyze
from .planner import PlanningError, plan_query
from .charts import export_result


def validate_result(result: dict) -> dict:
    """检查真实输出的结构、有限数值、分母关系及图表长度。"""
    checks = 0
    def walk(value):
        nonlocal checks
        if isinstance(value, float):
            if not math.isfinite(value):
                raise ValueError("结果校验发现非有限数值。")
            checks += 1
        elif isinstance(value, dict):
            for item in value.values():
                walk(item)
        elif isinstance(value, (list, tuple)):
            for item in value:
                walk(item)
    walk(result)
    table = result.get("table", {})
    keys = [c["key"] for c in table.get("columns", [])]
    if len(keys) != len(set(keys)):
        raise ValueError("结果表字段重复。")
    for row in table.get("rows", []):
        for key in ("exceedance_rate", "exceedance_pct", "rate", "rate_pct"):
            if isinstance(row.get(key), (int, float)) and not 0 <= row[key] <= 100:
                raise ValueError("超标比例超出 0–100%。")
        valid = next((row[k] for k in ("valid_days", "valid_city_days", "valid_count") if k in row), None)
        exceed = next((row[k] for k in ("exceeded_days", "exceed_days", "exceedance_days", "exceed_city_days") if k in row), None)
        if isinstance(valid, (int, float)) and isinstance(exceed, (int, float)) and not 0 <= exceed <= valid:
            raise ValueError("超标计数超过有效观测分母。")
        if isinstance(valid, (int, float)) and valid > 0 and isinstance(exceed, (int, float)) and isinstance(row.get("rate"), (int, float)):
            if abs(row["rate"] - 100 * exceed / valid) > .011:
                raise ValueError("超标比例与原始计数不一致。")
            checks += 1
    for chart in result.get("charts", []):
        for series in chart.get("series", []):
            if len(series.get("values", [])) != len(chart.get("labels", [])):
                raise ValueError("图表标签与数值长度不一致。")
            checks += 1
    return {"checks": checks, "rows": len(table.get("rows", [])), "passed": True}


class OfflineReActPolicy:
    """根据已得到的工具观察选择下一动作；不生成分析数值。"""
    STATISTICAL_TOOLS = {
        "ranking": "rank_cities", "exceedance": "count_exceedance",
        "trend": "monthly_trend", "seasonal": "compare_seasons", "anomaly": "detect_jumps",
    }

    @classmethod
    def next_action(cls, state):
        if "inventory" not in state:
            return "read_dataset", "inventory"
        if not state["inventory"]:
            raise PlanningError("data/raw 中没有 CSV 数据，请先准备真实公开数据。")
        if "dataset" not in state:
            return "clean_data", "dataset"
        if not state["dataset"].get("rows"):
            raise PlanningError("CSV 中未找到有效城市日数据，请检查字段与日期。")
        if "plan" not in state:
            return "plan_query", "plan"
        if "result" not in state:
            return cls.STATISTICAL_TOOLS[state["plan"]["intent"]], "result"
        no_events = state["plan"]["intent"] == "anomaly" and state["result"].get("quality", {}).get("selected_valid_city_days", 0) > 0
        if not state["result"].get("table", {}).get("rows") and not no_events:
            raise PlanningError("所选时间、地区和污染物没有有效观测，请更换筛选条件。")
        if "standard" not in state:
            return "judge_standard", "standard"
        if not state["standard"]:
            raise ValueError("聚合结果未提供国标判定口径。")
        if "validation" not in state:
            return "validate_result", "validation"
        return None


class AirQualityAgent:
    def __init__(self, root: Path):
        self.root = Path(root)
        self.sessions: dict[str, dict] = {}
        self.dataset = load_dataset(self.root)

    def status(self):
        return {"ready": bool(self.dataset.get("rows")), "dataset": self.dataset,
                "planner": "offline-react", "history_count": len(self.history())}

    def history(self):
        history_path = self.root / "outputs" / "history.jsonl"
        if not history_path.exists():
            return []
        rows = []
        with history_path.open(encoding="utf-8") as f:
            for line in f:
                try:
                    rows.append(json.loads(line))
                except ValueError:
                    continue
        return list(reversed(rows[-100:]))

    def run(self, query: str, session_id: str = "cli") -> dict:
        started = time.perf_counter()
        trace = []
        def call(tool, label, function, observation):
            t0 = time.perf_counter()
            value = function()
            trace.append({"tool": tool, "label": label, "status": "done",
                          "duration_ms": round((time.perf_counter()-t0)*1000, 2),
                          "observation": observation(value)})
            return value
        state = {}
        registry = {
            "read_dataset": ("读取本地数据", lambda: sorted((self.root/"data"/"raw").glob("*.csv")),
                             lambda files: f"发现 {len(files)} 份本地 CSV 数据分片。"),
            "clean_data": ("分片清洗与索引", lambda: load_dataset(self.root),
                           lambda d: f"{'复用已验证索引' if d.get('cache_reused') else '分片清洗完成'}；{d.get('rows',0):,} 个城市日；{d.get('quality',{}).get('duplicate_rows',0):,} 条重复记录已处理。"),
            "plan_query": ("解析任务与上下文", lambda: plan_query(query, state["dataset"], self.sessions.get(session_id)),
                           lambda p: f"{p['year']} 年 · {p['pollutant']} · {p['intent']}；{'继承上一轮城市' if p['followup'] else '新分析任务'}。"),
            "judge_standard": ("国标口径与有效性研判", lambda: state["result"].get("standard", {}),
                               lambda s: f"{s.get('name','GB 3095—2012')} · {s.get('basis','对应时间尺度')}；{s.get('note','')}"),
            "validate_result": ("交叉校验统计结果", lambda: validate_result(state["result"]),
                                lambda v: f"{v['checks']} 项数值及图表结构检查通过；统计表 {v['rows']} 行。"),
        }
        labels = {"ranking": "城市排名与月度聚合", "exceedance": "超标计数与地域聚合",
                  "trend": "月度趋势聚合", "seasonal": "季节对比聚合", "anomaly": "相邻日跳变识别"}
        for intent, tool in OfflineReActPolicy.STATISTICAL_TOOLS.items():
            registry[tool] = (labels[intent], lambda: analyze(self.root, state["plan"]),
                              lambda r: f"计算得到 {len(r.get('table',{}).get('rows',[]))} 行统计结果和 {len(r.get('charts',[]))} 组图表数据。")
        while True:
            action = OfflineReActPolicy.next_action(state)
            if action is None:
                break
            tool, observation_key = action
            state[observation_key] = call(tool, *registry[tool])
        self.dataset = state["dataset"]
        plan, result, validation = state["plan"], state["result"], state["validation"]
        now = datetime.now().astimezone()
        runid = now.strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:8]
        result.update({"ok": True, "id": runid, "query": query, "plan": plan,
                       "created_at": now.isoformat(timespec="seconds"), "trace": trace,
                       "validation": validation,
                       "provenance": {k: self.dataset.get(k) for k in ("name", "source_url", "sha256", "date_min", "date_max", "rows")}})
        result["quality"] = {**self.dataset.get("quality", {}), **result.get("quality", {})}
        # 导出工具完成后再更新保存记录，确保 JSON 和独立报告包含完整轨迹。
        call("visualize", "生成图表与可下载报告", lambda: export_result(self.root, result),
             lambda d: f"已保存 {len(result.get('charts',[]))} 张 SVG，以及 CSV、JSON 和独立 HTML 报告。")
        result["elapsed_ms"] = round((time.perf_counter()-started)*1000, 2)
        export_result(self.root, result)
        context = result.get("context", {})
        ranked_cities = [r.get("city") for r in result["table"]["rows"] if r.get("city")]
        selected = ranked_cities if plan["intent"] == "ranking" and plan["group_by"] == "city" else context.get("cities") or plan["cities"]
        if not selected:
            selected = [r.get("city") for r in result["table"]["rows"] if r.get("city")]
        result["context"] = {"cities": selected, "year": plan["year"], "pollutant": plan["pollutant"]}
        self.sessions[session_id] = result["context"]
        if len(self.sessions) > 200:
            del self.sessions[next(iter(self.sessions))]
        # 上下文同样随报告持久保存，历史重载可以恢复追问。
        (self.root/"outputs"/runid/"result.json").write_text(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")
        with (self.root/"outputs"/"history.jsonl").open("a", encoding="utf-8") as f:
            f.write(json.dumps({k: result[k] for k in ("id", "query", "title", "created_at", "elapsed_ms")}, ensure_ascii=False) + "\n")
        return result
