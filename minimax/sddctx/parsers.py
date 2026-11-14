"""把规格文档、Python 源码和测试文件解析成结构化条目。"""

from __future__ import annotations

import ast
import hashlib
import pathlib
import re
from typing import Any

from .models import STATUS_ACTIVE, STATUS_SUPERSEDED, CodeSymbol, SpecItem, TestCase

_SECTION_RE = re.compile(
    r"^##\s+(?P<id>(?:REQ|ADR)-[A-Za-z0-9\-]+)\s+(?P<title>.+?)\s*$"
)
_META_RE = re.compile(r"^-\s*(?P<key>[^：:]+)[：:]\s*(?P<value>.+?)\s*$")

_STATUS_MAP = {
    "生效中": STATUS_ACTIVE,
    "有效": STATUS_ACTIVE,
    "active": STATUS_ACTIVE,
    "已失效": STATUS_SUPERSEDED,
    "失效": STATUS_SUPERSEDED,
    "已废弃": STATUS_SUPERSEDED,
    "superseded": STATUS_SUPERSEDED,
}

_KIND_BY_PREFIX = {"REQ": "requirement", "ADR": "decision"}


def _split_list(raw: str) -> list[str]:
    parts = re.split(r"[,，;；]", raw)
    return [p.strip() for p in parts if p.strip()]


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _normalize_status(raw: str | None) -> str:
    if not raw:
        return STATUS_ACTIVE
    return _STATUS_MAP.get(raw.strip(), raw.strip() or STATUS_ACTIVE)


# ----------------------------------------------------------------------
# 规格 / 决策
# ----------------------------------------------------------------------
def parse_spec_file(path: pathlib.Path, root: pathlib.Path) -> list[SpecItem]:
    """解析一个 Markdown 规格文件。

    约定：``## <ID> <标题>`` 开头是小节，小节内 ``- 键：值`` 是元信息。
    """
    text = path.read_text(encoding="utf-8")
    rel = path.resolve().relative_to(root.resolve()).as_posix()

    header_meta: dict[str, str] = {}
    lines = text.splitlines()
    section_starts: list[tuple[int, re.Match[str]]] = []
    for index, line in enumerate(lines):
        if line.startswith("## "):
            match = _SECTION_RE.match(line)
            if match:
                section_starts.append((index, match))
        elif not section_starts and _META_RE.match(line):
            meta = _META_RE.match(line)
            assert meta is not None
            header_meta[meta.group("key").strip()] = meta.group("value").strip()

    doc_version = header_meta.get("版本", "0")
    default_module = header_meta.get("适用模块", "")
    default_symbols = _split_list(header_meta.get("关键符号", ""))

    items: list[SpecItem] = []
    for position, (start, match) in enumerate(section_starts):
        end = section_starts[position + 1][0] if position + 1 < len(section_starts) else len(lines)
        block_lines = lines[start + 1 : end]
        title = match.group("title").strip()
        spec_id = match.group("id").strip()

        meta: dict[str, str] = {}
        body_lines: list[str] = []
        in_meta = True
        for line in block_lines:
            if in_meta and not line.strip():
                continue  # 元信息区允许空行
            meta_match = _META_RE.match(line) if in_meta else None
            if meta_match is not None:
                meta[meta_match.group("key").strip()] = meta_match.group("value").strip()
                continue
            in_meta = False
            body_lines.append(line)
        body = "\n".join(body_lines).strip()

        status = _normalize_status(meta.get("状态"))
        module = meta.get("适用模块", default_module)
        symbols = _split_list(meta.get("关键符号", "")) or default_symbols
        tags = _split_list(meta.get("标签", ""))

        items.append(
            SpecItem(
                spec_id=spec_id,
                spec_kind=_KIND_BY_PREFIX.get(spec_id.split("-")[0], "requirement"),
                title=title,
                body=body,
                status=status,
                module=module,
                symbols=symbols,
                tags=tags,
                source_file=rel,
                source_line=start + 1,
                doc_version=doc_version,
                superseded_by=meta.get("取代"),
                retire_reason=meta.get("失效原因"),
                content_sha256=_sha256(body),
            )
        )
    return items


# ----------------------------------------------------------------------
# 代码符号
# ----------------------------------------------------------------------
def _module_name(rel_path: str) -> str:
    path = rel_path[:-3] if rel_path.endswith(".py") else rel_path
    return path.replace("/", ".").replace("\\", ".")


def _header_text(source_lines: list[str], node: ast.AST) -> str:
    """取 ``def`` / ``class`` 的声明行（含参数与返回注解）。"""
    lineno = getattr(node, "lineno", 0)
    body = getattr(node, "body", None)
    end = getattr(body[0], "lineno", lineno) if body else lineno
    chunk = source_lines[lineno - 1 : end]
    return " ".join(part.strip() for part in chunk if part.strip())


def parse_python_file(path: pathlib.Path, root: pathlib.Path) -> list[CodeSymbol]:
    """用 ``ast`` 提取一个 Python 文件里的顶层函数、类与方法。"""
    source = path.read_text(encoding="utf-8")
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return []

    rel = path.resolve().relative_to(root.resolve()).as_posix()
    module = _module_name(rel)
    source_lines = source.splitlines()
    symbols: list[CodeSymbol] = []

    def visit(node: ast.AST, prefix: str) -> None:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                name = f"{prefix}{child.name}"
                symbols.append(
                    CodeSymbol(
                        symbol_id=f"{module}.{name}",
                        module=module,
                        file_path=rel,
                        symbol=child.name,
                        kind="function" if not prefix else "method",
                        signature=_header_text(source_lines, child),
                        body="\n".join(source_lines[child.lineno - 1 : child.end_lineno]),
                        line_start=child.lineno,
                        line_end=child.end_lineno or child.lineno,
                    )
                )
            elif isinstance(child, ast.ClassDef):
                symbols.append(
                    CodeSymbol(
                        symbol_id=f"{module}.{child.name}",
                        module=module,
                        file_path=rel,
                        symbol=child.name,
                        kind="class",
                        signature=_header_text(source_lines, child),
                        body="\n".join(source_lines[child.lineno - 1 : child.end_lineno]),
                        line_start=child.lineno,
                        line_end=child.end_lineno or child.lineno,
                    )
                )
                visit(child, f"{child.name}.")

    visit(tree, "")
    return symbols


# ----------------------------------------------------------------------
# 测试用例
# ----------------------------------------------------------------------
_TARGET_MARKERS = ("目标功能测试", "目标测试")
_REGRESSION_MARKERS = ("回归测试",)


def parse_test_file(path: pathlib.Path, root: pathlib.Path) -> list[TestCase]:
    """提取测试文件中的 ``test_*`` 函数，并标注 target / regression。"""
    source = path.read_text(encoding="utf-8")
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return []

    rel = path.resolve().relative_to(root.resolve()).as_posix()
    module_doc = ast.get_docstring(tree) or ""
    kind = "other"
    for marker in _TARGET_MARKERS:
        if marker in module_doc:
            kind = "target"
            break
    else:
        for marker in _REGRESSION_MARKERS:
            if marker in module_doc:
                kind = "regression"
                break

    source_lines = source.splitlines()
    tests: list[TestCase] = []
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        if not node.name.startswith("test_"):
            continue
        tests.append(
            TestCase(
                test_id=f"{rel}::{node.name}",
                file_path=rel,
                test_name=node.name,
                kind=kind,
                module=_module_name(rel).replace(".tests.", "."),
                docstring=(ast.get_docstring(node) or "").strip(),
                body="\n".join(source_lines[node.lineno - 1 : node.end_lineno]),
                line_start=node.lineno,
                line_end=node.end_lineno or node.lineno,
            )
        )
    return tests


def parse_all_python(paths: list[pathlib.Path], root: pathlib.Path) -> dict[str, Any]:
    """批量解析，返回 ``{"symbols": [...], "tests": [...]}``。"""
    symbols: list[CodeSymbol] = []
    tests: list[TestCase] = []
    for path in paths:
        symbols.extend(parse_python_file(path, root))
        tests.extend(parse_test_file(path, root))
    return {"symbols": symbols, "tests": tests}
