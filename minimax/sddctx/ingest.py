"""导入流水线：把工程资料写进 seekdb／OceanBase。"""

from __future__ import annotations

import datetime as _dt
import uuid
from pathlib import Path
from typing import Any

from .config import Settings
from .parsers import parse_python_file, parse_spec_file, parse_test_file
from .store import SeekdbStore


def _run_id() -> str:
    return _dt.datetime.now().strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:6]


def _rel(path: Path, root: Path) -> str:
    return path.resolve().relative_to(root.resolve()).as_posix()


def collect_sources(settings: Settings) -> dict[str, list[Path]]:
    """列出本次要导入的规格、代码与测试文件。"""
    root = settings.project_root.resolve()
    specs = sorted(p for p in settings.specs_dir.rglob("*.md") if p.is_file())
    tests_root = settings.tests_dir.resolve()

    code: list[Path] = []
    for path in sorted(settings.code_dir.rglob("*.py")):
        if not path.is_file():
            continue
        try:
            path.resolve().relative_to(tests_root)
            continue  # 测试文件单独导入
        except ValueError:
            pass
        code.append(path)

    tests = sorted(p for p in settings.tests_dir.rglob("test_*.py") if p.is_file())
    return {"specs": specs, "code": code, "tests": tests}


def ingest_all(store: SeekdbStore, settings: Settings) -> dict[str, Any]:
    """执行一次完整导入，返回变更摘要。"""
    run_id = _run_id()
    root = settings.project_root.resolve()
    sources = collect_sources(settings)

    actions: list[dict[str, Any]] = []
    counts = {"spec": 0, "code": 0, "test": 0}

    for path in sources["specs"]:
        rel = _rel(path, root)
        specs = parse_spec_file(path, root)
        for action in store.ingest_specs(run_id, specs, rel):
            actions.append({**action, "source_file": rel})
        counts["spec"] += len(specs)

    for path in sources["code"]:
        rel = _rel(path, root)
        symbols = parse_python_file(path, root)
        for action in store.ingest_symbols(run_id, symbols, rel):
            actions.append({**action, "source_file": rel})
        counts["code"] += len(symbols)

    for path in sources["tests"]:
        rel = _rel(path, root)
        tests = parse_test_file(path, root)
        for action in store.ingest_tests(run_id, tests, rel):
            actions.append({**action, "source_file": rel})
        counts["test"] += len(tests)

    summary = {
        "run_id": run_id,
        "finished_at": _dt.datetime.now().isoformat(timespec="seconds"),
        "sources": {k: [str(p) for p in v] for k, v in sources.items()},
        "counts": counts,
        "actions": actions,
        "changed": [a for a in actions if a["action"] in ("inserted", "updated", "removed")],
        "table_stats": store.stats(),
    }
    return summary
