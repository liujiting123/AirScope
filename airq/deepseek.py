"""DeepSeek Chat Completions 客户端，仅使用标准库。

配置来自项目 .env.local 或环境变量。密钥只用于服务器端 Authorization，
日志、模型消息、浏览器与导出结果均不包含密钥。
接口依据 https://api-docs.deepseek.com/api/create-chat-completion/
"""
from __future__ import annotations
import json
import os
from pathlib import Path
import socket
import time
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import Request, urlopen


class DeepSeekError(RuntimeError):
    """可直接向界面显示的脱敏接口错误。"""


def load_config(root: Path) -> dict[str, str]:
    values = {}
    path = Path(root) / ".env.local"
    if path.is_file():
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            if key.strip().startswith("DEEPSEEK_"):
                values[key.strip()] = value.strip().strip("\"'")
    for key in ("DEEPSEEK_API_KEY", "DEEPSEEK_BASE_URL", "DEEPSEEK_MODEL"):
        if os.environ.get(key):
            values[key] = os.environ[key]
    return values


class DeepSeekClient:
    def __init__(self, root: Path):
        config = load_config(root)
        self._api_key = config.get("DEEPSEEK_API_KEY", "").strip()
        self.base_url = config.get("DEEPSEEK_BASE_URL", "https://api.deepseek.com").rstrip("/")
        self.model = config.get("DEEPSEEK_MODEL", "deepseek-flash")
        if urlparse(self.base_url).scheme != "https":
            raise DeepSeekError("DeepSeek 接口地址需使用 HTTPS。")

    @property
    def configured(self):
        return bool(self._api_key)

    def _request(self, endpoint: str, body=None):
        if not self.configured:
            raise DeepSeekError("请在项目 .env.local 中配置 DEEPSEEK_API_KEY 后启动模型分析。")
        encoded = None if body is None else json.dumps(body, ensure_ascii=False, allow_nan=False).encode("utf-8")
        headers = {"Authorization": "Bearer " + self._api_key,
                   "Content-Type": "application/json", "Accept": "application/json"}
        for attempt in range(2):
            try:
                request = Request(self.base_url + endpoint, data=encoded, headers=headers)
                with urlopen(request, timeout=90) as response:
                    return json.load(response)
            except HTTPError as error:
                code = error.code
                if code in (429, 502, 503) and attempt == 0:
                    error.close()
                    time.sleep(1)
                    continue
                error.close()
                explanations = {400: "请求参数不被接口接受，请检查模型和工具定义", 401: "密钥未通过认证", 402: "账户余额不足", 403: "当前密钥没有访问权限", 404: "接口或模型不存在", 422: "工具参数格式不正确", 429: "接口请求繁忙，请稍后重试", 500: "模型服务暂时异常", 502: "模型网关暂时异常", 503: "模型服务暂时不可用"}
                raise DeepSeekError(f"DeepSeek HTTP {code}：{explanations.get(code, '接口调用未成功')}。") from None
            except (TimeoutError, socket.timeout):
                raise DeepSeekError("DeepSeek 请求超过90秒，请稍后重试。") from None
            except URLError:
                raise DeepSeekError("无法连接 DeepSeek，请检查本机网络。") from None
            except (ValueError, UnicodeDecodeError):
                raise DeepSeekError("DeepSeek 返回了无法解析的响应。") from None

    def list_models(self):
        """用于主动检查当前密钥可用模型，不在网页初始化时自动调用。"""
        return self._request("/models")

    def complete(self, messages, tools=None, tool_choice="auto"):
        payload = {"model": self.model, "messages": messages,
                   "thinking": {"type": "disabled"}, "stream": False,
                   "max_tokens": 2400, "temperature": 0.2}
        if tools:
            payload.update({"tools": tools, "tool_choice": tool_choice})
        result = self._request("/chat/completions", payload)
        if not result.get("choices") or not isinstance(result["choices"][0].get("message"), dict):
            raise DeepSeekError("DeepSeek 响应未包含有效消息。")
        return result
