"""MiniMax 桥接：把任务上下文交给使用 MiniMax 的开发助手完成代码修改。

接入方式：**HTTP API**，Anthropic Messages 协议（``POST {base_url}/messages``）。

凭据解析顺序：
1. ``SDDCTX_MINIMAX_API_KEY`` 环境变量；
2. 本机 MiniMax Code 的登录态（``~/.minimax/auth/**/auth.json`` 的 accessToken）；
3. 本机 MiniMax Code 的 ``config.yaml``（provider options）。

无论来源是哪一个，请求与响应都会被完整落盘到 ``evidence/minimax/``，
用于证明 MiniMax 真实参与了本次代码修改。
"""

from __future__ import annotations

import ast
import datetime as _dt
import difflib
import json
import os
import pathlib
import re
from typing import Any

import httpx

from .config import Settings
from .models import ContextBundle
from .patching import FunctionPatch, apply_function_patches

_SYSTEM_PROMPT = """你是资深 Python 工程师，正在一个规格驱动开发（SDD）的工程里执行一项局部修改。

规则：
1. 只依据下面给出的「SDD 任务上下文」工作。标注为「生效中」的规格条目是必须遵守的约束；
   标注为「已失效」的条目**不得**作为约束依据。
2. 不得改变「回归测试」所覆盖的既有行为；不得删除、跳过或弱化任何测试；
   不得修改测试的预期结果。
3. 只实现任务描述要求的行为，不要顺手重构、重排 import 或改写 docstring。

输出格式：只输出需要修改的**函数补丁**，不要输出解释性文字、Markdown 代码围栏或多余内容。
每个补丁块形如：

<<<PATCH file=demo_app/pricing.py function=apply_coupon>>>
（该函数的完整新实现，包含 def 行与函数体）
<<<END PATCH>>>

可以有多个 PATCH 块，每个针对一个函数；未出现在补丁里的函数保持原样。
如果任务不需要修改任何代码，输出一行：NO_CHANGE。
"""

_FILE_BLOCK = re.compile(
    r"<<<FILE\s+path=(?P<path>[^\r\n>]+)>>>\r?\n(?P<body>.*?)\r?\n?<<<END FILE>>>",
    re.DOTALL,
)

_PATCH_BLOCK = re.compile(
    r"<<<PATCH\s+file=(?P<path>[^\r\n>]+)\s+function=(?P<function>[^\r\n>]+)>>>\r?\n"
    r"(?P<body>.*?)\r?\n?<<<END PATCH>>>",
    re.DOTALL,
)


# ----------------------------------------------------------------------
# 凭据
# ----------------------------------------------------------------------
def _minimax_code_auth() -> str | None:
    """读取本机 MiniMax Code 的 access token。"""
    root = pathlib.Path(
        os.environ.get("MINIMAX_DATA_DIR") or (pathlib.Path.home() / ".minimax")
    )
    auth_root = root / "auth"
    if not auth_root.is_dir():
        return None
    for path in sorted(auth_root.rglob("auth.json")):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        for record in (data.get("records") or {}).values():
            token = record.get("accessToken")
            if token:
                return str(token)
    return None


def _minimax_code_config() -> tuple[str | None, str | None, str | None]:
    """从本机 MiniMax Code 的 config.yaml 读取 baseURL / apiKey / 默认模型。"""
    root = pathlib.Path(
        os.environ.get("MINIMAX_DATA_DIR") or (pathlib.Path.home() / ".minimax")
    )
    path = root / "config.yaml"
    if not path.is_file():
        return None, None, None
    try:
        import yaml

        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except Exception:  # noqa: BLE001
        return None, None, None
    provider = (data.get("provider") or {}).get("minimax") or {}
    options = provider.get("options") or {}
    base_url = options.get("baseURL")
    api_key = options.get("apiKey")
    if api_key and api_key.startswith("sk-xxx"):
        api_key = None
    default_model = data.get("defaultModel")
    model = default_model.split("/", 1)[1] if default_model and "/" in default_model else None
    return base_url, api_key, model


def resolve_credentials(settings: Settings) -> dict[str, Any]:
    """解析 MiniMax 调用所需的 base url、api key 与模型名。"""
    config_base, config_key, config_model = _minimax_code_config()
    base_url = settings.minimax_base_url or config_base or ""
    api_key = settings.minimax_api_key or config_key or _minimax_code_auth()
    model = settings.minimax_model or config_model or "MiniMax-M3.1-Flash"
    return {
        "base_url": base_url,
        "api_key": api_key,
        "model": model,
        "source": (
            "env"
            if settings.minimax_api_key
            else ("minimax-code-config" if config_key else "minimax-code-auth")
        ),
    }


# ----------------------------------------------------------------------
# 调用
# ----------------------------------------------------------------------
def build_user_prompt(bundle: ContextBundle) -> str:
    """组装发给模型的用户提示。"""
    parts = [bundle.to_markdown()]
    parts.append("## 你的任务\n")
    parts.append(bundle.task)
    parts.append("")
    parts.append("请只输出需要修改的文件块（格式见系统提示）。")
    return "\n".join(parts)


def call_minimax(
    settings: Settings, system: str, user: str, *, label: str = "task"
) -> tuple[str, dict[str, Any]]:
    """调用 MiniMax Messages API，返回 ``(文本, 证据)``。

    稳健性策略：
    * 通过 ``thinking.budget_tokens`` 限制思维链长度——本任务是「按规格改几行代码」，
      思维链过长会吃光输出预算（实测 stop_reason=max_tokens 且没有任何补丁输出）；
    * 若模型因输出预算耗尽而截断（``stop_reason == "max_tokens"`` 且没有产出正文），
      自动翻倍 ``max_tokens`` 重试，最多 ``minimax_max_attempts`` 次。
    """
    creds = resolve_credentials(settings)
    if not creds["api_key"]:
        raise RuntimeError(
            "未找到 MiniMax 凭据：请设置环境变量 SDDCTX_MINIMAX_API_KEY，"
            "或在本机登录 MiniMax Code。"
        )
    url = creds["base_url"].rstrip("/") + "/messages"
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {creds['api_key']}",
        "x-api-key": creds["api_key"],
    }

    started = _dt.datetime.now().isoformat(timespec="seconds")
    attempts: list[dict[str, Any]] = []
    max_tokens = settings.minimax_max_tokens
    text = ""
    data: dict[str, Any] = {}
    last_status = 0
    last_response_text = ""

    for attempt in range(1, max(1, settings.minimax_max_attempts) + 1):
        body = {
            "model": creds["model"],
            "max_tokens": max_tokens,
            "system": system,
            "messages": [{"role": "user", "content": user}],
            "thinking": {
                "type": "enabled",
                "budget_tokens": settings.minimax_thinking_budget,
            },
        }
        with httpx.Client(timeout=settings.minimax_timeout) as client:
            response = client.post(url, headers=headers, json=body)
        last_status = response.status_code
        if response.status_code != 200:
            last_response_text = response.text[:4000]
            attempts.append(
                {
                    "attempt": attempt,
                    "max_tokens": max_tokens,
                    "http_status": response.status_code,
                    "error": response.text[:500],
                }
            )
            break

        data = response.json()
        text_parts: list[str] = []
        thinking_parts: list[str] = []
        for block in data.get("content", []):
            if block.get("type") == "text":
                text_parts.append(block.get("text", ""))
            elif block.get("type") == "thinking":
                thinking_parts.append(block.get("thinking", ""))
        text = "\n".join(text_parts)
        attempts.append(
            {
                "attempt": attempt,
                "max_tokens": max_tokens,
                "http_status": response.status_code,
                "stop_reason": data.get("stop_reason"),
                "usage": data.get("usage"),
                "produced_text": bool(text.strip()),
                "text_length": len(text),
            }
        )
        if text.strip():
            break
        if data.get("stop_reason") != "max_tokens":
            break
        max_tokens = min(max_tokens * 2, 65536)

    finished = _dt.datetime.now().isoformat(timespec="seconds")
    evidence: dict[str, Any] = {
        "label": label,
        "endpoint": url,
        "model": creds["model"],
        "credential_source": creds["source"],
        "request_started_at": started,
        "request_finished_at": finished,
        "system_prompt": system,
        "user_prompt": user,
        "http_status": last_status,
        "attempts": attempts,
    }
    if last_status != 200:
        evidence["response_text"] = last_response_text
        _write_evidence(settings, label, evidence)
        raise RuntimeError(f"MiniMax 调用失败 HTTP {last_status}: {last_response_text[:500]}")

    evidence["request_body"] = body
    evidence["response_body"] = data
    evidence["usage"] = data.get("usage")
    evidence["stop_reason"] = data.get("stop_reason")
    evidence["text"] = text
    evidence["thinking_excerpt"] = "\n".join(
        b.get("thinking", "")
        for b in data.get("content", [])
        if b.get("type") == "thinking"
    )[:2000]
    _write_evidence(settings, label, evidence)
    if not text.strip():
        raise RuntimeError(
            "MiniMax 未产出任何补丁文本（可能输出预算耗尽），详见 evidence/minimax/ 下的请求记录"
        )
    return text, evidence


def _write_evidence(settings: Settings, label: str, evidence: dict[str, Any]) -> pathlib.Path:
    out_dir = settings.evidence_dir / "minimax"
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = _dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    path = out_dir / f"{stamp}-{label}.json"
    path.write_text(
        json.dumps(evidence, ensure_ascii=False, indent=2, default=str), encoding="utf-8"
    )
    return path


# ----------------------------------------------------------------------
# 输出解析与落盘
# ----------------------------------------------------------------------
def extract_file_blocks(text: str) -> dict[str, str]:
    """从模型输出中解析出 ``路径 -> 完整文件内容``（整文件模式，回退用）。"""
    blocks: dict[str, str] = {}
    for match in _FILE_BLOCK.finditer(text or ""):
        path = match.group("path").strip().replace("\\", "/")
        body = match.group("body")
        if body.endswith("\n"):
            body = body[:-1]
        blocks[path] = body
    return blocks


def extract_function_patches(text: str) -> list[FunctionPatch]:
    """从模型输出中解析出函数级补丁（首选模式）。"""
    patches: list[FunctionPatch] = []
    for match in _PATCH_BLOCK.finditer(text or ""):
        path = match.group("path").strip().replace("\\", "/")
        function = match.group("function").strip()
        body = match.group("body")
        if body.endswith("\n"):
            body = body[:-1]
        patches.append(
            FunctionPatch(file_path=path, function_name=function, new_source=body)
        )
    return patches


def apply_model_output(
    text: str, root: pathlib.Path
) -> tuple[str, list[dict[str, Any]]]:
    """把模型输出落盘。

    优先使用函数级补丁（只替换目标函数，文件其余部分逐字节不变）；
    若模型没有给出补丁但给出了整文件，则回退到整文件替换。

    Returns:
        ``(模式, 变更记录列表)``。
    """
    patches = extract_function_patches(text)
    if patches:
        return "function-splice", apply_function_patches(patches, root)
    blocks = extract_file_blocks(text)
    if blocks:
        return "whole-file", apply_file_blocks(blocks, root)
    return "none", []


def apply_file_blocks(blocks: dict[str, str], root: pathlib.Path) -> list[dict[str, Any]]:
    """把模型给出的文件内容写入磁盘，返回逐文件的变更记录。"""
    changes: list[dict[str, Any]] = []
    for rel_path, content in blocks.items():
        target = (root / rel_path).resolve()
        try:
            target.relative_to(root.resolve())
        except ValueError:
            raise RuntimeError(f"模型试图写入工程根目录之外的路径：{rel_path}") from None
        before = target.read_text(encoding="utf-8") if target.exists() else ""

        if not content.endswith("\n"):
            content += "\n"

        if rel_path.endswith(".py"):
            try:
                ast.parse(content)
            except SyntaxError as exc:
                raise RuntimeError(
                    f"模型产出的 {rel_path} 语法不合法，已拒绝写入：{exc}"
                ) from exc

        diff = "".join(
            difflib.unified_diff(
                before.splitlines(keepends=True),
                content.splitlines(keepends=True),
                fromfile=f"a/{rel_path}",
                tofile=f"b/{rel_path}",
                n=3,
            )
        )
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
        changes.append(
            {
                "path": rel_path,
                "existed": bool(before),
                "changed": before != content,
                "before_bytes": len(before.encode("utf-8")),
                "after_bytes": len(content.encode("utf-8")),
                "added_lines": sum(
                    1 for line in diff.splitlines() if line.startswith("+") and not line.startswith("+++")
                ),
                "removed_lines": sum(
                    1 for line in diff.splitlines() if line.startswith("-") and not line.startswith("---")
                ),
                "diff": diff,
            }
        )
    return changes
