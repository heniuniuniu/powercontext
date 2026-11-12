"""CLI 调用入口：init / import / context / save / verify / reset。"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path

from . import assemble, state, store

ROOT = Path(__file__).resolve().parents[1]


def run_tests(tag: str) -> tuple[int, str]:
    out = subprocess.run(
        [sys.executable, "-m", "unittest", "discover", "-s", str(ROOT / "tests"), "-v"],
        cwd=str(ROOT), capture_output=True, text=True,
    )
    text = (out.stdout + out.stderr).strip()
    log = ROOT / "logs" / f"test-{tag}.log"
    log.parent.mkdir(parents=True, exist_ok=True)
    log.write_text(
        f"# command: python -m unittest discover -s tests -v\n"
        f"# code_version: {'before' if tag == 'before' else 'after'}\n"
        f"# timestamp: {time.strftime('%Y-%m-%dT%H:%M:%S')}\n"
        f"# exit_code: {out.returncode}\n\n{text}\n",
        encoding="utf-8",
    )
    return out.returncode, text


def cmd_verify(args):
    before_rc, _ = run_tests("before")
    print(f"[verify] 修改前测试退出码 = {before_rc}（目标用例应失败）")

    patch = ROOT / "diff" / "order-filter.patch"
    target = ROOT / "demo_app" / "filter.py"
    after_src = (ROOT / "demo_app" / "filter_after.py").read_text(encoding="utf-8")
    target.write_text(after_src, encoding="utf-8")
    print(f"[verify] 已应用 {patch.relative_to(ROOT)} 到 {target.relative_to(ROOT)}")

    after_rc, _ = run_tests("after")
    print(f"[verify] 修改后测试退出码 = {after_rc}（应为 0）")

    state.save(args.task, done=["应用补丁并跑测试"],
               todo=[],
               verification={"before_exit": before_rc, "after_exit": after_rc,
                             "passed": before_rc != 0 and after_rc == 0},
               extra={"patch": str(patch.relative_to(ROOT))})
    return 0 if after_rc == 0 else 1


def main(argv=None):
    p = argparse.ArgumentParser(prog="sdd_context")
    sub = p.add_subparsers(dest="cmd", required=True)

    a = sub.add_parser("init", help="建表与全文索引")
    a.add_argument("--reset", action="store_true")
    a = sub.add_parser("import", help="导入 corpus 资料")
    a.add_argument("--dir", default=str(ROOT / "corpus"))
    a = sub.add_parser("context", help="输出任务上下文 JSON")
    a.add_argument("--task", default="T1")
    a = sub.add_parser("save", help="写入任务状态")
    a.add_argument("--task", default="T1")
    a.add_argument("--done", action="append", default=[])
    a.add_argument("--todo", action="append", default=[])
    a = sub.add_parser("verify", help="应用补丁、跑前后测试、回写状态")
    a.add_argument("--task", default="T1")
    sub.add_parser("reset", help="清空本地状态")

    args = p.parse_args(argv)
    if args.cmd == "init":
        print("现有资料条数:", store.init_schema(args.reset))
    elif args.cmd == "import":
        print("导入资料条数:", store.import_dir(args.dir))
    elif args.cmd == "context":
        print(assemble.to_json(assemble.build_context(args.task)))
    elif args.cmd == "save":
        print(json.dumps(state.save(args.task, done=args.done, todo=args.todo), ensure_ascii=False, indent=2))
    elif args.cmd == "verify":
        return cmd_verify(args)
    elif args.cmd == "reset":
        state.reset()
        print("状态已清空")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
