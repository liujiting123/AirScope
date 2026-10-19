#!/usr/bin/env python3
"""清穹 AirScope 本地服务：python3 app.py --open 或 --query '问题'。"""
from __future__ import annotations
import argparse
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import re
import threading
from urllib.parse import parse_qs, urlparse, unquote
import webbrowser

from airq.agent import AirQualityAgent
from airq.llm_agent import DeepSeekAirQualityAgent, AgentExecutionError
from airq.deepseek import DeepSeekError
from airq.standards import get_standards, STANDARD_NOTE

ROOT = Path(__file__).resolve().parent


class Handler(SimpleHTTPRequestHandler):
    server_version = "AirScope/2.0"

    def __init__(self, *args, agent, **kwargs):
        self.agent = agent
        super().__init__(*args, directory=str(ROOT/"web"), **kwargs)

    def send_json(self, data, status=200):
        raw = json.dumps(data, ensure_ascii=False, allow_nan=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(raw)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(raw)

    def do_GET(self):
        parsed = urlparse(self.path)
        route = parsed.path
        if route == "/api/status":
            return self.send_json(self.agent.status())
        if route == "/api/dataset":
            return self.send_json(self.agent.dataset)
        if route == "/api/standards":
            return self.send_json({"standards": get_standards(), "note": STANDARD_NOTE})
        if route == "/api/history":
            return self.send_json({"runs": self.agent.history()})
        if route == "/api/result":
            args = parse_qs(parsed.query)
            runid = args.get("id", [""])[0]
            if not re.fullmatch(r"[\w-]{1,80}", runid):
                return self.send_json({"ok": False, "error": "分析记录不存在。"}, 404)
            result_path = ROOT/"outputs"/runid/"result.json"
            if not result_path.is_file():
                return self.send_json({"ok": False, "error": "分析记录不存在。"}, 404)
            result = json.loads(result_path.read_text(encoding="utf-8"))
            session_id = args.get("session_id", [""])[0]
            if session_id:
                if hasattr(self.agent, "restore_session"):
                    self.agent.restore_session(session_id[:100], result)
                else:
                    self.agent.sessions[session_id[:100]] = result.get("context", {})
            return self.send_json(result)
        if route.startswith("/outputs/"):
            target = (ROOT/unquote(route.lstrip("/"))).resolve()
            if not target.is_relative_to((ROOT/"outputs").resolve()) or not target.is_file():
                return self.send_error(404)
            raw = target.read_bytes()
            mime = {".html":"text/html; charset=utf-8", ".json":"application/json; charset=utf-8", ".csv":"text/csv; charset=utf-8", ".svg":"image/svg+xml"}.get(target.suffix)
            if not mime:
                return self.send_error(404)
            self.send_response(200)
            self.send_header("Content-Type", mime)
            self.send_header("Content-Length", str(len(raw)))
            if target.suffix in (".csv", ".json"):
                self.send_header("Content-Disposition", f'attachment; filename="{target.name}"')
            self.end_headers()
            self.wfile.write(raw)
            return
        if route == "/favicon.ico":
            self.send_response(204)
            self.end_headers()
            return
        if route.startswith("/api/"):
            return self.send_json({"ok": False, "error": "接口不存在。"}, 404)
        return super().do_GET()

    def do_POST(self):
        route = urlparse(self.path).path
        if route not in ("/api/analyze", "/api/analyze/stream"):
            return self.send_json({"ok": False, "error": "接口不存在。"}, 404)
        streaming = False
        def emit(event):
            self.wfile.write((json.dumps(event, ensure_ascii=False, allow_nan=False)+"\n").encode("utf-8"))
            self.wfile.flush()
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if not 0 < length <= 16384:
                raise ValueError("请求为空或内容过长。")
            body = json.loads(self.rfile.read(length))
            if not isinstance(body, dict):
                raise ValueError("请求应为JSON对象。")
            query = body.get("query", "")
            session_id = body.get("session_id", "browser")
            if not isinstance(query, str) or not isinstance(session_id, str):
                raise ValueError("query 和 session_id 应为文本。")
            if not query.strip() or len(query) > 2000:
                raise ValueError("请输入1–2000字的分析问题。")
            if route.endswith("/stream"):
                self.send_response(200)
                self.send_header("Content-Type", "application/x-ndjson; charset=utf-8")
                self.send_header("Cache-Control", "no-store")
                self.send_header("Connection", "close")
                self.end_headers()
                self.close_connection = True
                streaming = True
                emit({"type": "stage", "message": "正在准备分析会话…"})
            with self.server.analysis_lock:
                if isinstance(self.agent, DeepSeekAirQualityAgent):
                    result = self.agent.run(query, session_id[:100], on_event=emit if streaming else None)
                else:
                    result = self.agent.run(query, session_id[:100])
            if streaming:
                emit({"type": "result", "result": result})
                return
            return self.send_json(result)
        except (BrokenPipeError, ConnectionResetError):
            return
        except (ValueError, TypeError, KeyError, DeepSeekError, AgentExecutionError) as error:
            if streaming:
                emit({"type": "error", "error": str(error)})
                return
            return self.send_json({"ok": False, "error": str(error)}, 400)
        except Exception as error:
            print(f"分析失败：{type(error).__name__}: {error}", flush=True)
            if streaming:
                emit({"type": "error", "error": "本次分析未完成，请查看启动终端中的错误详情。"})
                return
            return self.send_json({"ok": False, "error": "本次分析未完成，请查看启动终端中的错误详情。"}, 500)


def main():
    parser = argparse.ArgumentParser(description="清穹 AirScope · DeepSeek 空气质量分析 Agent")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--open", action="store_true", help="启动后打开浏览器")
    parser.add_argument("--query", help="命令行分析并保存报告")
    parser.add_argument("--offline", action="store_true", help="明确切换到无模型的规则备用模式")
    args = parser.parse_args()
    agent = AirQualityAgent(ROOT) if args.offline else DeepSeekAirQualityAgent(ROOT)
    if args.query:
        result = agent.run(args.query)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return
    handler = partial(Handler, agent=agent)
    try:
        server = ThreadingHTTPServer(("127.0.0.1", args.port), handler)
    except OSError as error:
        raise SystemExit(f"端口 {args.port} 不可用：{error}。请使用 --port 8766。")
    server.analysis_lock = threading.Lock()
    url = f"http://127.0.0.1:{args.port}"
    mode = "离线规则备用模式" if args.offline else f"DeepSeek 模型工具调用 · {agent.status().get('model','')}"
    print(f"\n清穹 AirScope 已启动：{url}\n模式：{mode}\n本地数据：{agent.dataset.get('rows',0):,} 个城市日\n按 Ctrl+C 停止。\n", flush=True)
    if args.open:
        webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n已停止服务。")
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
