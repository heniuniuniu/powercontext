"""端到端验证驱动。

按题目要求依次执行并留证：
  1. 环境自检（数据库 / PowerContext / MiniMax）
  2. 初始化数据：建库建表 + 导入规格/代码/测试
  3. 按任务检索，输出上下文（规格 / 代码 / 测试 / 已失效条目）
  4. 从**空状态**保存任务记录
  5. **重启上下文模块**（新进程）后读取并复用该状态
  6. 把上下文交给使用 MiniMax 的开发助手完成代码修改
  7. 运行目标功能测试与回归测试
  8. 回写任务状态与验证结果

用法：
    python scripts/verify.py                # 完整流程
    python scripts/verify.py --skip-agent   # 只跑到第 5 步
"""

from __future__ import annotations

import argparse
import datetime as _dt
import json
import pathlib
import subprocess
import sys
import time

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from sddctx.assemble import build_bundle, bundle_summary  # noqa: E402
from sddctx.config import Settings, dump_json  # noqa: E402
from sddctx.models import TaskState  # noqa: E402
from sddctx.state import PowerContextStateStore  # noqa: E402
from sddctx.store import SeekdbStore  # noqa: E402

TASK = (
    "支持无门槛优惠券（coupon.threshold = 0）：无门槛券不做门槛判定即可使用；"
    "并且订单商品金额恰好等于券门槛时，折扣券也应当可以使用（门槛判定使用 >= 而不是 >）。"
    "请只修改 demo_app/pricing.py 中 apply_coupon 的门槛判定逻辑，"
    "不要改变满减券/折扣券的减免计算、上限封顶、运费隔离与金额精度等既有行为。"
)

TASK_ID = "task-coupon-threshold"


def _log(step: str, message: str) -> None:
    stamp = _dt.datetime.now().strftime("%H:%M:%S")
    print(f"[{stamp}] {step:<10} {message}", flush=True)


def _run_cli(args: list[str], timeout: int = 1800) -> subprocess.CompletedProcess[str]:
    """在**新进程**中运行 sddctx CLI（用于证明模块重启后仍能读取状态）。"""
    cmd = [sys.executable, "-m", "sddctx", *args]
    _log("subprocess", " ".join(cmd))
    return subprocess.run(
        cmd,
        cwd=str(ROOT),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--skip-agent", action="store_true", help="不调用 MiniMax")
    parser.add_argument("--task", default=TASK)
    parser.add_argument("--task-id", default=TASK_ID)
    args = parser.parse_args()

    settings = Settings.load(ROOT)
    settings.ensure_dirs()
    report: dict[str, Any] = {
        "started_at": _dt.datetime.now().isoformat(timespec="seconds"),
        "project_root": str(ROOT),
        "task": args.task,
        "task_id": args.task_id,
        "steps": [],
    }

    def step(name: str, ok: bool, **payload: Any) -> None:
        report["steps"].append({"step": name, "ok": ok, **payload})
        _log("STEP", f"{name} -> {'ok' if ok else 'FAILED'}")
        print(dump_json(payload), flush=True)

    # ---- 1. 环境自检 --------------------------------------------------
    doctor = _run_cli(["doctor"])
    doctor_payload = json.loads(doctor.stdout) if doctor.returncode == 0 else {"stdout": doctor.stdout}
    step("doctor", doctor.returncode == 0, result=doctor_payload)

    # ---- 2. 初始化数据 ----------------------------------------------
    init = _run_cli(["init"])
    ingest = _run_cli(["ingest"])
    ingest_payload = json.loads(ingest.stdout) if ingest.returncode == 0 else {"stdout": ingest.stdout}
    step(
        "init+ingest",
        init.returncode == 0 and ingest.returncode == 0,
        counts=ingest_payload.get("counts"),
        table_stats=ingest_payload.get("table_stats"),
        database=ingest_payload.get("database"),
    )

    doctor_after = _run_cli(["doctor"])
    doctor_after_payload = (
        json.loads(doctor_after.stdout) if doctor_after.returncode == 0 else {"stdout": doctor_after.stdout}
    )
    step(
        "doctor-after-init",
        doctor_after.returncode == 0,
        result=doctor_after_payload,
    )

    # ---- 3. 检索并输出上下文 -----------------------------------------
    with SeekdbStore(settings) as store:
        bundle = build_bundle(store, settings, args.task)
        summary = bundle_summary(bundle)
        db_info = store.server_info()
    context_path = settings.evidence_dir / "context-latest.md"
    context_path.write_text(bundle.to_markdown(), encoding="utf-8")
    step(
        "context",
        bool(summary["spec_ids"]),
        summary=summary,
        context_file=str(context_path),
        database=db_info,
    )

    # ---- 4. 从空状态保存任务记录 -------------------------------------
    pc = PowerContextStateStore(settings)
    before = pc.load()[0]
    pending = TaskState(
        task_id=args.task_id,
        task_text=args.task,
        status="in_progress",
        done_items=["已导入规格/代码/测试到 seekdb", "已按任务组装上下文"],
        todo_items=["由 MiniMax 完成代码修改", "运行目标功能测试", "运行回归测试"],
        validation_results=[],
    )
    receipt = pc.save(pending)
    step(
        "state-save",
        True,
        scope_id=receipt["scope_id"],
        seq=receipt["seq"],
        previous_state_existed=before is not None,
        state=pending.to_dict(),
    )

    # ---- 5. 重启上下文模块后读取并复用 --------------------------------
    restarted = _run_cli(["state", "show"])
    restart_payload = json.loads(restarted.stdout) if restarted.returncode == 0 else {}
    current = restart_payload.get("current") or {}
    reused = current.get("task_id") == args.task_id
    step(
        "state-restart-read",
        reused,
        note="在全新进程（python -m sddctx state show）中读取",
        exit_code=restarted.returncode,
        current=current,
        history_count=restart_payload.get("history_count"),
    )

    with SeekdbStore(settings) as store:
        bundle_with_state = build_bundle(store, settings, args.task, state=store_state(settings))
    state_in_context = bundle_with_state.state is not None
    step(
        "state-reused-in-context",
        state_in_context,
        note="恢复出的状态被并入新生成的上下文",
        has_state_section=state_in_context,
        markdown_contains_state="任务进展" in bundle_with_state.to_markdown(),
    )

    if args.skip_agent:
        report["finished_at"] = _dt.datetime.now().isoformat(timespec="seconds")
        report["success"] = all(s["ok"] for s in report["steps"])
        _write_report(settings, report)
        return 0 if report["success"] else 1

    # ---- 6. 交给 MiniMax 完成代码修改 --------------------------------
    agent = _run_cli(["agent", "--apply", "--task-id", args.task_id, args.task])
    agent_payload = json.loads(agent.stdout) if agent.returncode == 0 else {"stdout": agent.stdout[-4000:]}
    step(
        "minimax-agent",
        bool(agent_payload.get("success")),
        minimax=agent_payload.get("minimax"),
        changes=agent_payload.get("changes"),
        context_summary=agent_payload.get("context_summary"),
        evidence_file=agent_payload.get("evidence_file"),
        exit_code=agent.returncode,
    )

    # ---- 7. 目标功能测试与回归测试 -----------------------------------
    target = _run_cli(["test", "demo_app/tests/test_coupon_threshold.py", "--label", "after-target"])
    regression = _run_cli(
        ["test", "demo_app/tests/test_coupon_regression.py", "--label", "after-regression"]
    )
    step(
        "tests",
        target.returncode == 0 and regression.returncode == 0,
        target_exit_code=target.returncode,
        regression_exit_code=regression.returncode,
        target_tail=target.stdout[-1500:],
        regression_tail=regression.stdout[-1500:],
    )

    # ---- 8. 回写状态 --------------------------------------------------
    final = TaskState(
        task_id=args.task_id,
        task_text=args.task,
        status="done" if target.returncode == 0 and regression.returncode == 0 else "in_progress",
        done_items=[
            "已导入规格/代码/测试到 seekdb",
            "已按任务组装上下文",
            "已保存任务状态并在新进程中恢复",
            "MiniMax 已完成代码修改",
        ],
        todo_items=[] if target.returncode == 0 else ["修复未通过的目标功能测试"],
        validation_results=[
            f"目标功能测试：{'通过' if target.returncode == 0 else '失败'}（exit_code={target.returncode}）",
            f"回归测试：{'通过' if regression.returncode == 0 else '失败'}（exit_code={regression.returncode}）",
        ],
    )
    final_receipt = pc.save(final)
    step("state-final", True, receipt=final_receipt, state=final.to_dict())

    report["finished_at"] = _dt.datetime.now().isoformat(timespec="seconds")
    report["success"] = all(s["ok"] for s in report["steps"])
    _write_report(settings, report)
    _log("DONE", f"success={report['success']}")
    return 0 if report["success"] else 1


def store_state(settings: Settings) -> TaskState | None:
    state, _ = PowerContextStateStore(settings).load()
    return state


def _write_report(settings: Settings, report: dict[str, Any]) -> None:
    settings.evidence_dir.mkdir(parents=True, exist_ok=True)
    stamp = _dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    path = settings.evidence_dir / f"verify-{stamp}.json"
    path.write_text(dump_json(report), encoding="utf-8")
    _log("report", str(path))
    summary_path = settings.evidence_dir / "verify-latest.json"
    summary_path.write_text(dump_json(report), encoding="utf-8")


if __name__ == "__main__":
    raise SystemExit(main())
