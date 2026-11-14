"""上下文组装：把检索结果收敛成一份交给开发助手的任务上下文。"""

from __future__ import annotations

import datetime as _dt
from typing import Any

from .config import Settings
from .models import (
    STATUS_ACTIVE,
    ContextBundle,
    ContextHit,
    SpecItem,
    TaskState,
)
from .retrieve import RetrievalResult, retrieve
from .store import SeekdbStore


def _group(hits: list[ContextHit], doc_type: str) -> list[ContextHit]:
    return [h for h in hits if h.doc_type == doc_type]


def build_bundle(
    store: SeekdbStore,
    settings: Settings,
    task: str,
    *,
    state: TaskState | None = None,
    modules: list[str] | None = None,
    result: RetrievalResult | None = None,
) -> ContextBundle:
    """检索并组装任务上下文。

    规则：
    * 规格条目按状态分流——``active`` 作为**约束**给出，``superseded`` 作为
      **已失效条目**单独列出并明确标注不得遵守；
    * 代码符号按模块与得分排序；
    * 测试按 ``target`` / ``regression`` 分组，保证目标行为与既有行为都可见。
    """
    retrieval = result or retrieve(store, settings, task, modules=modules)

    spec_hits = _group(retrieval.hits, "spec")
    code_hits = _group(retrieval.hits, "code")
    test_hits = _group(retrieval.hits, "test")

    active_spec_hits = [h for h in spec_hits if h.status == STATUS_ACTIVE]
    retired_spec_hits = [h for h in spec_hits if h.status != STATUS_ACTIVE]

    specs = store.load_specs([h.ref_id for h in active_spec_hits[: settings.top_k_specs]])
    retired = store.load_specs([h.ref_id for h in retired_spec_hits[: settings.top_k_specs]])
    code = store.load_symbols([h.ref_id for h in code_hits[: settings.top_k_code]])
    tests = store.load_tests([h.ref_id for h in test_hits[: settings.top_k_tests]])

    # 一个测试文件里的用例共同构成「既有行为契约」：只要其中一条被检索命中，
    # 整个文件的用例都应出现在上下文里，否则「必须保持的行为」会出现缺口。
    hit_files = {t.file_path for t in tests}
    missing = [
        test
        for test in store.load_tests_from_files(sorted(hit_files))
        if test.test_id not in {t.test_id for t in tests}
    ]
    tests.extend(missing)

    order = {h.ref_id: index for index, h in enumerate(retrieval.hits)}
    specs.sort(key=lambda s: order.get(s.spec_id, 1_000))
    retired.sort(key=lambda s: order.get(s.spec_id, 1_000))
    code.sort(key=lambda c: order.get(c.symbol_id, 1_000))
    tests.sort(key=lambda t: (t.file_path, order.get(t.test_id, 1_000)))

    target_tests = [t for t in tests if t.kind == "target"]
    regression_tests = [t for t in tests if t.kind == "regression"]
    other_tests = [t for t in tests if t.kind not in ("target", "regression")]

    return ContextBundle(
        task=task,
        specs=specs,
        retired_specs=retired,
        code=code,
        tests=tests + other_tests,
        target_tests=target_tests,
        regression_tests=regression_tests,
        state=state,
        retrieval=retrieval.to_dict(),
        generated_at=_dt.datetime.now().isoformat(timespec="seconds"),
    )


def bundle_summary(bundle: ContextBundle) -> dict[str, Any]:
    """给 CLI 用的摘要。"""
    return {
        "task": bundle.task,
        "generated_at": bundle.generated_at,
        "spec_count": len(bundle.specs),
        "retired_spec_count": len(bundle.retired_specs),
        "code_count": len(bundle.code),
        "target_test_count": len(bundle.target_tests),
        "regression_test_count": len(bundle.regression_tests),
        "spec_ids": [s.spec_id for s in bundle.specs],
        "retired_spec_ids": [s.spec_id for s in bundle.retired_specs],
        "symbols": [c.symbol_id for c in bundle.code],
        "target_tests": [t.test_name for t in bundle.target_tests],
        "regression_tests": [t.test_name for t in bundle.regression_tests],
        "has_state": bundle.state is not None,
    }
