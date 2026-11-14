"""上下文模块的核心数据结构。"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

# 规格 / 决策状态
STATUS_ACTIVE = "active"  # 生效中
STATUS_SUPERSEDED = "superseded"  # 已失效

STATUS_LABELS = {
    STATUS_ACTIVE: "生效中",
    STATUS_SUPERSEDED: "已失效",
}


@dataclass
class SpecItem:
    """一条规格条目（需求或决策）。"""

    spec_id: str
    spec_kind: str  # requirement | decision
    title: str
    body: str
    status: str
    module: str
    symbols: list[str]
    tags: list[str]
    source_file: str
    source_line: int
    doc_version: str
    superseded_by: str | None = None
    retire_reason: str | None = None
    content_sha256: str = ""

    @property
    def is_active(self) -> bool:
        return self.status == STATUS_ACTIVE

    def source_ref(self) -> str:
        return f"{self.source_file}:{self.source_line}#{self.spec_id}"

    def to_dict(self) -> dict[str, Any]:
        return {
            "spec_id": self.spec_id,
            "spec_kind": self.spec_kind,
            "title": self.title,
            "body": self.body,
            "status": self.status,
            "status_label": STATUS_LABELS.get(self.status, self.status),
            "module": self.module,
            "symbols": self.symbols,
            "tags": self.tags,
            "source_file": self.source_file,
            "source_line": self.source_line,
            "doc_version": self.doc_version,
            "superseded_by": self.superseded_by,
            "retire_reason": self.retire_reason,
            "content_sha256": self.content_sha256,
            "source_ref": self.source_ref(),
        }


@dataclass
class CodeSymbol:
    """一个可定位的代码符号。"""

    symbol_id: str
    module: str
    file_path: str
    symbol: str
    kind: str  # function | class | method
    signature: str
    body: str
    line_start: int
    line_end: int

    def source_ref(self) -> str:
        return f"{self.file_path}:{self.line_start}-{self.line_end}#{self.symbol}"

    def to_dict(self) -> dict[str, Any]:
        return {
            "symbol_id": self.symbol_id,
            "module": self.module,
            "file_path": self.file_path,
            "symbol": self.symbol,
            "kind": self.kind,
            "signature": self.signature,
            "body": self.body,
            "line_start": self.line_start,
            "line_end": self.line_end,
            "source_ref": self.source_ref(),
        }


@dataclass
class TestCase:
    """一个测试用例。"""

    test_id: str
    file_path: str
    test_name: str
    kind: str  # target | regression
    module: str
    docstring: str
    body: str
    line_start: int
    line_end: int

    def source_ref(self) -> str:
        return f"{self.file_path}:{self.line_start}-{self.line_end}::{self.test_name}"

    def to_dict(self) -> dict[str, Any]:
        return {
            "test_id": self.test_id,
            "file_path": self.file_path,
            "test_name": self.test_name,
            "kind": self.kind,
            "module": self.module,
            "docstring": self.docstring,
            "body": self.body,
            "line_start": self.line_start,
            "line_end": self.line_end,
            "source_ref": self.source_ref(),
        }


@dataclass
class ContextHit:
    """一条检索命中。"""

    doc_type: str  # spec | code | test
    ref_id: str
    title: str
    module: str
    file_path: str
    line_start: int
    status: str
    score: float
    reasons: list[str] = field(default_factory=list)
    payload: dict[str, Any] = field(default_factory=dict)

    def source_ref(self) -> str:
        if self.doc_type == "spec":
            return f"{self.file_path}:{self.line_start}#{self.ref_id}"
        if self.doc_type == "code":
            return f"{self.file_path}:{self.line_start}#{self.ref_id}"
        return f"{self.file_path}:{self.line_start}::{self.ref_id}"

    def to_dict(self) -> dict[str, Any]:
        return {
            "doc_type": self.doc_type,
            "ref_id": self.ref_id,
            "title": self.title,
            "module": self.module,
            "file_path": self.file_path,
            "line_start": self.line_start,
            "status": self.status,
            "status_label": STATUS_LABELS.get(self.status, self.status),
            "score": round(self.score, 4),
            "reasons": self.reasons,
            "source_ref": self.source_ref(),
            "payload": self.payload,
        }


@dataclass
class TaskState:
    """任务进展状态（持久化在 PowerContext）。"""

    task_id: str
    task_text: str
    status: str = "in_progress"  # in_progress | done | blocked
    done_items: list[str] = field(default_factory=list)
    todo_items: list[str] = field(default_factory=list)
    validation_results: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    updated_at: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "task_id": self.task_id,
            "task_text": self.task_text,
            "status": self.status,
            "done_items": self.done_items,
            "todo_items": self.todo_items,
            "validation_results": self.validation_results,
            "notes": self.notes,
            "updated_at": self.updated_at,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "TaskState":
        return cls(
            task_id=str(data.get("task_id", "")),
            task_text=str(data.get("task_text", "")),
            status=str(data.get("status", "in_progress")),
            done_items=list(data.get("done_items", [])),
            todo_items=list(data.get("todo_items", [])),
            validation_results=list(data.get("validation_results", [])),
            notes=list(data.get("notes", [])),
            updated_at=str(data.get("updated_at", "")),
        )


@dataclass
class ContextBundle:
    """交给开发助手的任务上下文。"""

    task: str
    specs: list[SpecItem] = field(default_factory=list)
    retired_specs: list[SpecItem] = field(default_factory=list)
    code: list[CodeSymbol] = field(default_factory=list)
    tests: list[TestCase] = field(default_factory=list)
    target_tests: list[TestCase] = field(default_factory=list)
    regression_tests: list[TestCase] = field(default_factory=list)
    state: TaskState | None = None
    retrieval: dict[str, Any] = field(default_factory=dict)
    generated_at: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "task": self.task,
            "generated_at": self.generated_at,
            "specs": [s.to_dict() for s in self.specs],
            "retired_specs": [s.to_dict() for s in self.retired_specs],
            "code": [c.to_dict() for c in self.code],
            "tests": [t.to_dict() for t in self.tests],
            "target_tests": [t.to_dict() for t in self.target_tests],
            "regression_tests": [t.to_dict() for t in self.regression_tests],
            "state": self.state.to_dict() if self.state else None,
            "retrieval": self.retrieval,
        }

    def to_markdown(self) -> str:
        """渲染成给开发助手的 Markdown 上下文。"""
        lines: list[str] = []
        lines.append("# SDD 任务上下文")
        lines.append("")
        lines.append(f"## 任务\n\n{self.task}")
        lines.append("")

        lines.append("## 适用规格条目（当前有效，必须遵守）")
        lines.append("")
        if self.specs:
            for spec in self.specs:
                lines.append(f"### {spec.spec_id} {spec.title}")
                lines.append(f"- 来源：{spec.source_ref()}")
                lines.append(f"- 状态：{STATUS_LABELS.get(spec.status, spec.status)}")
                if spec.symbols:
                    lines.append(f"- 涉及符号：{', '.join(spec.symbols)}")
                lines.append("")
                lines.append(spec.body.strip())
                lines.append("")
        else:
            lines.append("_（无命中）_")
            lines.append("")

        if self.retired_specs:
            lines.append("## 已失效条目（仅供追溯，**不得**作为约束）")
            lines.append("")
            for spec in self.retired_specs:
                lines.append(
                    f"- {spec.spec_id} {spec.title} —— 来源：{spec.source_ref()}"
                    + (f"；失效原因：{spec.retire_reason}" if spec.retire_reason else "")
                )
            lines.append("")

        lines.append("## 需要修改的代码")
        lines.append("")
        if self.code:
            for sym in self.code:
                lines.append(f"### {sym.symbol_id}  ({sym.kind})")
                lines.append(f"- 来源：{sym.source_ref()}")
                lines.append(f"- 签名：{sym.signature}")
                lines.append("")
                lines.append("```python")
                lines.append(sym.body.rstrip())
                lines.append("```")
                lines.append("")
        else:
            lines.append("_（无命中）_")
            lines.append("")

        lines.append("## 验证测试")
        lines.append("")
        if self.target_tests:
            lines.append("### 目标功能测试（修改前失败、修改后必须通过）")
            lines.append("")
            for test in self.target_tests:
                lines.append(f"- `{test.test_name}` —— {test.source_ref()}")
            lines.append("")
        if self.regression_tests:
            lines.append("### 回归测试（修改前后都必须通过）")
            lines.append("")
            for test in self.regression_tests:
                lines.append(f"- `{test.test_name}` —— {test.source_ref()}")
            lines.append("")
        if not self.target_tests and not self.regression_tests:
            lines.append("_（无命中）_")
            lines.append("")

        if self.state:
            lines.append("## 任务进展（来自 PowerContext 恢复）")
            lines.append("")
            lines.append(f"- 任务 ID：{self.state.task_id}")
            lines.append(f"- 状态：{self.state.status}")
            if self.state.done_items:
                lines.append("- 已完成：")
                lines.extend(f"  - {item}" for item in self.state.done_items)
            if self.state.todo_items:
                lines.append("- 待办：")
                lines.extend(f"  - {item}" for item in self.state.todo_items)
            if self.state.validation_results:
                lines.append("- 已有验证结果：")
                lines.extend(f"  - {item}" for item in self.state.validation_results)
            lines.append("")

        return "\n".join(lines)
