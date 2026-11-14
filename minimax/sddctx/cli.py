"""sddctx 命令行入口。

``sddctx`` 是上下文模块的正式调用入口，输入输出见 ``docs/RUNBOOK.md``。
"""

from __future__ import annotations

import argparse
import datetime as _dt
import json
import pathlib
import subprocess
import sys
from typing import Any

from . import __version__
from .assemble import build_bundle, bundle_summary
from .config import Settings, dump_json
from .ingest import ingest_all
from .models import TaskState
from .state import PowerContextStateStore
from .store import SeekdbStore


# ----------------------------------------------------------------------
# 辅助
# ----------------------------------------------------------------------
def _print_json(obj: Any) -> None:
    print(dump_json(obj))


def _write_evidence(settings: Settings, name: str, payload: Any) -> pathlib.Path:
    out_dir = settings.evidence_dir
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = _dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    path = out_dir / f"{stamp}-{name}.json"
    path.write_text(dump_json(payload), encoding="utf-8")
    return path


def _run_pytest(
    settings: Settings, paths: list[str], label: str
) -> dict[str, Any]:
    """运行 pytest，记录命令、退出码与输出。"""
    if not paths:
        return {"label": label, "command": None, "exit_code": None, "skipped": True}
    command = [sys.executable, "-m", "pytest", *paths, "-v", "--no-header", "-p", "no:cacheprovider"]
    started = _dt.datetime.now().isoformat(timespec="seconds")
    proc = subprocess.run(
        command,
        cwd=str(settings.project_root),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    finished = _dt.datetime.now().isoformat(timespec="seconds")
    result = {
        "label": label,
        "command": " ".join(command),
        "cwd": str(settings.project_root),
        "started_at": started,
        "finished_at": finished,
        "exit_code": proc.returncode,
        "stdout": proc.stdout[-20000:],
        "stderr": proc.stderr[-8000:],
    }
    _write_evidence(settings, f"pytest-{label}", result)
    return result


def _state_store(settings: Settings) -> PowerContextStateStore:
    return PowerContextStateStore(settings)


def _load_state(settings: Settings) -> TaskState | None:
    try:
        state, _ = _state_store(settings).load()
        return state
    except Exception:  # noqa: BLE001
        return None


# ----------------------------------------------------------------------
# 子命令
# ----------------------------------------------------------------------
def cmd_init(args: argparse.Namespace, settings: Settings) -> int:
    settings.ensure_dirs()
    with SeekdbStore(settings) as store:
        report = store.ensure_schema()
        info = store.server_info()
    _print_json({"ok": True, "settings": settings.describe(), "database": info, "schema": report})
    return 0


def cmd_ingest(args: argparse.Namespace, settings: Settings) -> int:
    settings.ensure_dirs()
    with SeekdbStore(settings) as store:
        store.ensure_schema()
        summary = ingest_all(store, settings)
        summary["database"] = store.server_info()
    path = _write_evidence(settings, "ingest", summary)
    summary["evidence_file"] = str(path)
    _print_json(summary)
    return 0


def cmd_query(args: argparse.Namespace, settings: Settings) -> int:
    with SeekdbStore(settings) as store:
        state = _load_state(settings) if args.with_state else None
        bundle = build_bundle(store, settings, args.task, state=state)
        summary = bundle_summary(bundle)
        summary["database"] = store.server_info()
    if args.json:
        _print_json(bundle.to_dict())
    elif args.summary:
        _print_json(summary)
    else:
        print(bundle.to_markdown())
    return 0


def cmd_state_save(args: argparse.Namespace, settings: Settings) -> int:
    state = TaskState(
        task_id=args.task_id,
        task_text=args.task_text or "",
        status=args.status,
        done_items=list(args.done or []),
        todo_items=list(args.todo or []),
        validation_results=list(args.result or []),
        notes=list(args.note or []),
    )
    store = _state_store(settings)
    receipt = store.save(state)
    _print_json({"ok": True, "receipt": receipt, "state": state.to_dict()})
    return 0


def cmd_state_show(args: argparse.Namespace, settings: Settings) -> int:
    store = _state_store(settings)
    state, history = store.load()
    payload = {
        "scope_id": store.scope_id(),
        "logical_scope": settings.powercontext_scope,
        "server": settings.powercontext_url,
        "current": state.to_dict() if state else None,
        "history_count": len(history),
        "history": [
            {
                "seq": item["payload"].get("seq"),
                "updated_at": item["payload"].get("updated_at"),
                "status": item["payload"].get("status"),
                "done": item["payload"].get("done_items"),
                "todo": item["payload"].get("todo_items"),
                "results": item["payload"].get("validation_results"),
                "artifact_id": item.get("artifact_id"),
                "revision": item.get("revision"),
            }
            for item in history
        ],
    }
    _print_json(payload)
    return 0


def cmd_state_search(args: argparse.Namespace, settings: Settings) -> int:
    store = _state_store(settings)
    hits = store.search(args.query)
    _print_json(
        {
            "scope_id": store.scope_id(),
            "query": args.query,
            "hit_count": len(hits),
            "hits": [
                {
                    "score": hit.get("score"),
                    "state": hit["payload"],
                }
                for hit in hits
            ],
        }
    )
    return 0


def cmd_test(args: argparse.Namespace, settings: Settings) -> int:
    paths = list(args.paths or [])
    if not paths:
        paths = [str(settings.tests_dir.relative_to(settings.project_root))]
    result = _run_pytest(settings, paths, args.label or "tests")
    print(result["stdout"])
    if result.get("stderr"):
        print(result["stderr"], file=sys.stderr)
    print(f"exit_code={result['exit_code']}")
    return int(result["exit_code"] or 0)


def cmd_db(args: argparse.Namespace, settings: Settings) -> int:
    with SeekdbStore(settings) as store:
        if args.sql:
            rows = store.query(args.sql)
            _print_json({"sql": args.sql, "rows": rows})
        elif args.stats:
            _print_json({"database": store.server_info(), "tables": store.stats()})
        else:
            _print_json({"database": store.server_info(), "tables": store.stats()})
    return 0


def cmd_doctor(args: argparse.Namespace, settings: Settings) -> int:
    report: dict[str, Any] = {"settings": settings.describe()}
    ok = True
    try:
        with SeekdbStore(settings) as store:
            report["database"] = store.server_info()
            try:
                report["tables"] = store.stats()
            except Exception as exc:  # noqa: BLE001
                # 还没执行过 `sddctx init` 时表不存在，这不算环境故障。
                report["tables"] = {"initialized": False, "error": str(exc)[:200]}
    except Exception as exc:  # noqa: BLE001
        report["database"] = {"error": str(exc)}
        ok = False
    try:
        state, _ = _state_store(settings).load()
        report["powercontext"] = {
            "reachable": True,
            "scope_id": settings.powercontext_scope,
            "has_state": state is not None,
        }
    except Exception as exc:  # noqa: BLE001
        report["powercontext"] = {"reachable": False, "error": str(exc)[:300]}
    from .minimax_bridge import resolve_credentials

    creds = resolve_credentials(settings)
    report["minimax"] = {
        "base_url": creds["base_url"],
        "model": creds["model"],
        "credential_source": creds["source"],
        "credential_present": bool(creds["api_key"]),
    }
    if not creds["api_key"]:
        ok = False
    report["ok"] = ok
    _print_json(report)
    return 0 if ok else 1


def cmd_agent(args: argparse.Namespace, settings: Settings) -> int:
    """完整闭环：组装上下文 -> 交给 MiniMax -> 落盘改动 -> 跑测试 -> 保存状态。"""
    from .minimax_bridge import (
        _SYSTEM_PROMPT,
        apply_model_output,
        build_user_prompt,
        call_minimax,
        extract_function_patches,
    )

    settings.ensure_dirs()
    task_id = args.task_id or ("task-" + _dt.datetime.now().strftime("%Y%m%d%H%M%S"))
    report: dict[str, Any] = {
        "task_id": task_id,
        "task": args.task,
        "started_at": _dt.datetime.now().isoformat(timespec="seconds"),
    }

    with SeekdbStore(settings) as store:
        state = _load_state(settings)
        bundle = build_bundle(store, settings, args.task, state=state)
        report["context_summary"] = bundle_summary(bundle)
        report["database"] = store.server_info()
        context_markdown = bundle.to_markdown()
    (settings.evidence_dir).mkdir(parents=True, exist_ok=True)
    context_path = settings.evidence_dir / "context-latest.md"
    context_path.write_text(context_markdown, encoding="utf-8")
    report["context_file"] = str(context_path)

    # 1) 先把「待办」写入 PowerContext，证明状态被持久化
    state_store = _state_store(settings)
    try:
        pending = TaskState(
            task_id=task_id,
            task_text=args.task,
            status="in_progress",
            done_items=["已导入规格/代码/测试到 seekdb", "已按任务组装上下文"],
            todo_items=["由 MiniMax 完成代码修改", "运行目标功能测试", "运行回归测试"],
            validation_results=[],
        )
        report["state_before"] = state_store.save(pending)
    except Exception as exc:  # noqa: BLE001
        report["state_before_error"] = str(exc)[:400]

    # 2) 交给 MiniMax
    text, evidence = call_minimax(
        settings, _SYSTEM_PROMPT, build_user_prompt(bundle), label=task_id
    )
    report["minimax"] = {
        "model": evidence["model"],
        "http_status": evidence["http_status"],
        "usage": evidence.get("usage"),
        "endpoint": evidence["endpoint"],
    }

    # 3) 落盘模型给出的补丁
    patches = extract_function_patches(text)
    report["model_patches"] = [
        {"file": p.file_path, "function": p.function_name} for p in patches
    ]
    if args.apply:
        mode, changes = apply_model_output(text, settings.project_root)
    else:
        mode, changes = "dry-run", []
    report["patch_mode"] = mode
    report["changes"] = changes

    # 4) 跑测试
    target_rel = str((settings.tests_dir / "test_coupon_threshold.py").relative_to(settings.project_root))
    regression_rel = str(
        (settings.tests_dir / "test_coupon_regression.py").relative_to(settings.project_root)
    )
    report["tests"] = {
        "target": _run_pytest(settings, [target_rel], "target"),
        "regression": _run_pytest(settings, [regression_rel], "regression"),
    }

    # 5) 回写状态
    target_ok = report["tests"]["target"].get("exit_code") == 0
    regression_ok = report["tests"]["regression"].get("exit_code") == 0
    done = ["已导入规格/代码/测试到 seekdb", "已按任务组装上下文"]
    if report["changes"]:
        changed_paths = ", ".join(change["path"] for change in report["changes"])
        done.append(f"MiniMax 已产出代码修改：{changed_paths}")
    results = [
        f"目标功能测试：{'通过' if target_ok else '失败'}（exit_code={report['tests']['target'].get('exit_code')}）",
        f"回归测试：{'通过' if regression_ok else '失败'}（exit_code={report['tests']['regression'].get('exit_code')}）",
    ]
    final_state = TaskState(
        task_id=task_id,
        task_text=args.task,
        status="done" if (target_ok and regression_ok) else "in_progress",
        done_items=done,
        todo_items=[] if (target_ok and regression_ok) else ["修复未通过的测试"],
        validation_results=results,
    )
    try:
        report["state_after"] = state_store.save(final_state)
    except Exception as exc:  # noqa: BLE001
        report["state_after_error"] = str(exc)[:400]

    report["finished_at"] = _dt.datetime.now().isoformat(timespec="seconds")
    report["success"] = bool(target_ok and regression_ok)
    path = _write_evidence(settings, f"agent-{task_id}", report)
    report["evidence_file"] = str(path)
    _print_json(report)
    return 0 if report["success"] else 1


def cmd_serve(args: argparse.Namespace, settings: Settings) -> int:
    from .server import serve

    serve(settings, host=args.host, port=args.port)
    return 0


# ----------------------------------------------------------------------
# 参数解析
# ----------------------------------------------------------------------
def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="sddctx",
        description="面向软件持续演进的 SDD 工程上下文模块",
    )
    parser.add_argument("--version", action="version", version=f"sddctx {__version__}")
    parser.add_argument("--project-root", default=None, help="工程根目录（默认自动探测）")
    sub = parser.add_subparsers(dest="command", required=True)

    p_init = sub.add_parser("init", help="建库建表并检查运行环境")
    p_init.set_defaults(func=cmd_init)

    p_ingest = sub.add_parser("ingest", help="导入规格、代码与测试到 seekdb／OceanBase")
    p_ingest.set_defaults(func=cmd_ingest)

    p_query = sub.add_parser("query", help="按任务检索并输出上下文")
    p_query.add_argument("task", help="任务描述")
    p_query.add_argument("--json", action="store_true", help="输出完整 JSON")
    p_query.add_argument("--summary", action="store_true", help="只输出摘要")
    p_query.add_argument("--with-state", action="store_true", help="附带 PowerContext 中的任务状态")
    p_query.set_defaults(func=cmd_query)

    p_state = sub.add_parser("state", help="任务状态（PowerContext）")
    state_sub = p_state.add_subparsers(dest="state_command", required=True)

    p_save = state_sub.add_parser("save", help="保存任务状态")
    p_save.add_argument("--task-id", required=True)
    p_save.add_argument("--task-text", default="")
    p_save.add_argument("--status", default="in_progress")
    p_save.add_argument("--done", action="append", default=[])
    p_save.add_argument("--todo", action="append", default=[])
    p_save.add_argument("--result", action="append", default=[])
    p_save.add_argument("--note", action="append", default=[])
    p_save.set_defaults(func=cmd_state_save)

    p_show = state_sub.add_parser("show", help="读取当前任务状态与历史")
    p_show.set_defaults(func=cmd_state_show)

    p_search = state_sub.add_parser("search", help="按查询语义召回历史任务状态")
    p_search.add_argument("query")
    p_search.set_defaults(func=cmd_state_search)

    p_test = sub.add_parser("test", help="运行测试并留存日志")
    p_test.add_argument("paths", nargs="*")
    p_test.add_argument("--label", default="tests")
    p_test.set_defaults(func=cmd_test)

    p_db = sub.add_parser("db", help="直接访问数据库（取证用）")
    p_db.add_argument("--sql", help="执行一条 SELECT 并打印结果")
    p_db.add_argument("--stats", action="store_true")
    p_db.set_defaults(func=cmd_db)

    p_doctor = sub.add_parser("doctor", help="检查数据库 / PowerContext / MiniMax 连通性")
    p_doctor.set_defaults(func=cmd_doctor)

    p_agent = sub.add_parser("agent", help="完整闭环：上下文 -> MiniMax -> 改动 -> 测试 -> 状态")
    p_agent.add_argument("task", help="任务描述")
    p_agent.add_argument("--task-id", default=None)
    p_agent.add_argument("--apply", action="store_true", help="把模型产出的文件写入磁盘")
    p_agent.set_defaults(func=cmd_agent)

    p_serve = sub.add_parser("serve", help="启动 HTTP API")
    p_serve.add_argument("--host", default="127.0.0.1")
    p_serve.add_argument("--port", type=int, default=8765)
    p_serve.set_defaults(func=cmd_serve)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    settings = Settings.load(args.project_root)
    return int(args.func(args, settings) or 0)


if __name__ == "__main__":
    raise SystemExit(main())
