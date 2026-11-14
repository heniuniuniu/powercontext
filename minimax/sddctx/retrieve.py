"""检索：从 seekdb 中挑出与任务相关的规格、代码与测试。

检索融合三路信号：
1. **倒排索引命中**（``doc_token``）——精确、可解释，保证标识符一定被召回；
2. **原生全文检索**（seekdb FULLTEXT）——利用数据库的 BM25 相关度；
3. **结构化加权**——任务里点名的符号、模块、需求编号会被显著加权。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from .config import Settings
from .models import STATUS_ACTIVE, ContextHit
from .store import SeekdbStore
from .tokenize import tokenize

_CJK = re.compile(r"^[\u4e00-\u9fff]+$")


def query_weights(text: str) -> dict[str, float]:
    """给查询 token 赋权：标识符 > 英文单词 > 中文词组 > 中文二元组。"""
    weights: dict[str, float] = {}
    for token in tokenize(text):
        if token in weights:
            continue
        if re.fullmatch(r"[a-z0-9]+", token):
            weight = 3.0 if len(token) >= 8 else (2.0 if len(token) >= 4 else 1.2)
        elif any(ch.isdigit() for ch in token) or any(ch in token for ch in "-._"):
            weight = 3.0
        elif _CJK.match(token):
            weight = min(3.0, 0.8 + 0.25 * len(token))
        else:
            weight = 1.0
        weights[token] = weight
    return weights


@dataclass
class RetrievalResult:
    """一次检索的完整结果与可解释信息。"""

    task: str
    hits: list[ContextHit] = field(default_factory=list)
    diagnostics: dict[str, Any] = field(default_factory=dict)

    def by_type(self, doc_type: str) -> list[ContextHit]:
        return [h for h in self.hits if h.doc_type == doc_type]

    def to_dict(self) -> dict[str, Any]:
        return {
            "task": self.task,
            "hits": [h.to_dict() for h in self.hits],
            "diagnostics": self.diagnostics,
        }


def retrieve(
    store: SeekdbStore,
    settings: Settings,
    task: str,
    *,
    modules: list[str] | None = None,
    top_k: int | None = None,
) -> RetrievalResult:
    """根据任务描述检索上下文候选。"""
    limit = top_k or max(settings.top_k_specs, settings.top_k_code, settings.top_k_tests) * 6
    weights = query_weights(task)
    lowered_task = task.lower()

    token_hits = store.search_by_tokens(weights, limit=limit)
    fts_hits = store.search_by_fts(list(weights), limit=limit) if store.fts_available() else []

    token_scores = {row["doc_id"]: float(row["score"]) for row in token_hits}
    matched_counts = {row["doc_id"]: int(row["matched"]) for row in token_hits}
    max_token = max(token_scores.values()) if token_scores else 1.0
    fts_scores = {row["doc_id"]: float(row["score"]) for row in fts_hits}
    max_fts = max(fts_scores.values()) if fts_scores else 1.0

    doc_ids = list(dict.fromkeys(list(token_scores) + list(fts_scores)))
    if not doc_ids:
        return RetrievalResult(task=task, diagnostics={"candidates": 0, "fts": store.fts_available()})

    docs = {row["doc_id"]: row for row in store.load_docs(doc_ids)}

    hits: list[ContextHit] = []
    for doc_id in doc_ids:
        doc = docs.get(doc_id)
        if doc is None:
            continue
        reasons: list[str] = []
        score = 0.0

        if doc_id in token_scores:
            normalized = token_scores[doc_id] / max_token if max_token else 0.0
            score += 0.60 * normalized
            reasons.append(
                f"倒排索引命中 {matched_counts.get(doc_id, 0)} 个查询词（得分 {token_scores[doc_id]:.1f}）"
            )
        if doc_id in fts_scores and max_fts:
            normalized = fts_scores[doc_id] / max_fts
            score += 0.30 * normalized
            reasons.append(f"seekdb 全文相关度 {fts_scores[doc_id]:.3f}")

        ref_id = str(doc["ref_id"])
        tail = ref_id.replace("::", ".").split(".")[-1].lower()
        if tail and tail in lowered_task:
            score += 0.45
            reasons.append(f"任务文本直接点名 `{tail}`")
        if ref_id.lower() in lowered_task:
            score += 0.35
            reasons.append(f"任务文本直接点名 `{ref_id}`")

        module = str(doc["module"] or "")
        if module and module.lower() in lowered_task:
            score += 0.12
            reasons.append(f"任务涉及模块 `{module}`")
        if modules and any(m.lower() == module.lower() for m in modules):
            score += 0.10
            reasons.append("命中指定模块范围")

        if doc["doc_type"] == "spec" and doc["status"] != STATUS_ACTIVE:
            score -= 0.05
            reasons.append("条目已失效，仅供追溯")

        if score < settings.min_score:
            continue

        hits.append(
            ContextHit(
                doc_type=str(doc["doc_type"]),
                ref_id=ref_id,
                title=str(doc["title"]),
                module=module,
                file_path=str(doc["file_path"]),
                line_start=int(doc["line_start"]),
                status=str(doc["status"]),
                score=score,
                reasons=reasons,
                payload={"doc_id": doc_id},
            )
        )

    hits.sort(key=lambda h: (-h.score, h.doc_type, h.ref_id))
    return RetrievalResult(
        task=task,
        hits=hits,
        diagnostics={
            "candidates": len(doc_ids),
            "query_tokens": len(weights),
            "token_index_hits": len(token_scores),
            "fts_hits": len(fts_scores),
            "fts_enabled": store.fts_available(),
            "top_weights": sorted(weights.items(), key=lambda kv: -kv[1])[:12],
            "selected": len(hits),
        },
    )
