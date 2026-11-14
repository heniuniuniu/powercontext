"""模块配置。

所有配置项都可以用环境变量覆盖，默认值保证在干净环境中开箱即用。
"""

from __future__ import annotations

import json
import os
import pathlib
from dataclasses import dataclass, field
from typing import Any

# 默认的 MiniMax Code LLM 代理端点（Anthropic Messages 协议）。
DEFAULT_MINIMAX_BASE_URL = "https://agent.minimax.cn/mavis/api/v1/llm/v1"
DEFAULT_MINIMAX_MODEL = "MiniMax-M3.1-Flash"


def _env(name: str, default: str | None = None) -> str | None:
    value = os.environ.get(name)
    if value is None or value == "":
        return default
    return value


def _env_bool(name: str, default: bool = False) -> bool:
    raw = os.environ.get(name)
    if raw is None or raw == "":
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


@dataclass
class Settings:
    """sddctx 的运行配置。"""

    # ---- 工程路径 ----
    project_root: pathlib.Path
    specs_dir: pathlib.Path
    code_dir: pathlib.Path
    tests_dir: pathlib.Path
    evidence_dir: pathlib.Path
    state_dir: pathlib.Path

    # ---- 数据库 ----
    db_mode: str = "embedded"  # embedded | server
    db_dir: pathlib.Path = pathlib.Path(".sddctx/seekdb")
    db_host: str = "127.0.0.1"
    db_port: int = 2881
    db_user: str = "root"
    db_password: str = ""
    db_name: str = "sddctx"

    # ---- PowerContext ----
    powercontext_url: str = "http://127.0.0.1:8000"
    powercontext_scope: str = "project:sddctx-demo"
    powercontext_enabled: bool = True

    # ---- MiniMax ----
    minimax_base_url: str = DEFAULT_MINIMAX_BASE_URL
    minimax_api_key: str | None = None
    minimax_model: str = DEFAULT_MINIMAX_MODEL
    minimax_max_tokens: int = 8192
    minimax_thinking_budget: int = 0
    minimax_timeout: float = 300.0
    minimax_max_attempts: int = 3

    # ---- 检索 ----
    top_k_specs: int = 12
    top_k_code: int = 10
    top_k_tests: int = 16
    min_score: float = 0.05

    raw: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def load(cls, project_root: str | pathlib.Path | None = None) -> "Settings":
        """从环境变量与默认值构造配置。"""
        root = pathlib.Path(project_root or _env("SDDCTX_PROJECT_ROOT") or _detect_root())
        root = root.resolve()

        specs_dir = pathlib.Path(_env("SDDCTX_SPECS_DIR", str(root / "specs")))
        code_dir = pathlib.Path(_env("SDDCTX_CODE_DIR", str(root / "demo_app")))
        tests_dir = pathlib.Path(_env("SDDCTX_TESTS_DIR", str(root / "demo_app" / "tests")))
        evidence_dir = pathlib.Path(_env("SDDCTX_EVIDENCE_DIR", str(root / "evidence")))
        state_dir = pathlib.Path(_env("SDDCTX_STATE_DIR", str(root / ".sddctx")))

        db_dir = pathlib.Path(_env("SDDCTX_DB_DIR", str(state_dir / "seekdb")))
        db_mode = (_env("SDDCTX_DB_MODE", "embedded") or "embedded").lower()

        return cls(
            project_root=root,
            specs_dir=specs_dir,
            code_dir=code_dir,
            tests_dir=tests_dir,
            evidence_dir=evidence_dir,
            state_dir=state_dir,
            db_mode=db_mode,
            db_dir=db_dir,
            db_host=_env("SDDCTX_DB_HOST", "127.0.0.1") or "127.0.0.1",
            db_port=int(_env("SDDCTX_DB_PORT", "2881") or 2881),
            db_user=_env("SDDCTX_DB_USER", "root") or "root",
            db_password=_env("SDDCTX_DB_PASSWORD", "") or "",
            db_name=_env("SDDCTX_DB_NAME", "sddctx") or "sddctx",
            powercontext_url=(_env("SDDCTX_PC_URL", "http://127.0.0.1:8000") or "").rstrip("/"),
            powercontext_scope=_env("SDDCTX_PC_SCOPE", "project:sddctx-demo") or "",
            powercontext_enabled=_env_bool("SDDCTX_PC_ENABLED", True),
            minimax_base_url=_env("SDDCTX_MINIMAX_BASE_URL", DEFAULT_MINIMAX_BASE_URL)
            or DEFAULT_MINIMAX_BASE_URL,
            minimax_api_key=_env("SDDCTX_MINIMAX_API_KEY"),
            minimax_model=_env("SDDCTX_MINIMAX_MODEL", DEFAULT_MINIMAX_MODEL)
            or DEFAULT_MINIMAX_MODEL,
            minimax_max_tokens=int(_env("SDDCTX_MINIMAX_MAX_TOKENS", "8192") or 8192),
            minimax_thinking_budget=int(_env("SDDCTX_MINIMAX_THINKING_BUDGET", "0") or 0),
            minimax_timeout=float(_env("SDDCTX_MINIMAX_TIMEOUT", "300") or 300),
            minimax_max_attempts=int(_env("SDDCTX_MINIMAX_MAX_ATTEMPTS", "3") or 3),
        )

    # ------------------------------------------------------------------
    def ensure_dirs(self) -> None:
        """创建运行期需要的目录。"""
        for path in (self.evidence_dir, self.state_dir, self.db_dir if self.db_mode == "embedded" else self.state_dir):
            path.mkdir(parents=True, exist_ok=True)

    def describe(self) -> dict[str, Any]:
        """返回可安全展示的配置摘要（不含凭据）。"""
        return {
            "project_root": str(self.project_root),
            "specs_dir": str(self.specs_dir),
            "code_dir": str(self.code_dir),
            "tests_dir": str(self.tests_dir),
            "db_mode": self.db_mode,
            "db_dir": str(self.db_dir),
            "db_host": self.db_host,
            "db_port": self.db_port,
            "db_name": self.db_name,
            "powercontext_url": self.powercontext_url,
            "powercontext_scope": self.powercontext_scope,
            "powercontext_enabled": self.powercontext_enabled,
            "minimax_base_url": self.minimax_base_url,
            "minimax_model": self.minimax_model,
            "minimax_api_key_present": bool(self.minimax_api_key),
        }


def _detect_root() -> pathlib.Path:
    """向上查找包含 ``specs/`` 与 ``demo_app/`` 的工程根目录。"""
    here = pathlib.Path(__file__).resolve()
    for parent in here.parents:
        if (parent / "specs").is_dir() and (parent / "demo_app").is_dir():
            return parent
    return here.parents[1]


def dump_json(obj: Any) -> str:
    """统一的 JSON 序列化（保留中文）。"""
    return json.dumps(obj, ensure_ascii=False, indent=2, default=str)
