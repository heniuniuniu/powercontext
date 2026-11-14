"""函数级补丁：把模型给出的函数实现拼接回原文件。

为什么不用「整文件替换」：整文件替换需要模型完整回显文件，
既容易出现无关改动（import 顺序、docstring、空行），也让输出预算被浪费。
函数级替换用**结构保证**替代提示词约束——

* 只有目标函数的源码行会被替换，文件其余部分**逐字节不变**；
* 替换后做 ``ast`` 语法校验；
* 记录 unified diff，可核对改动确实只落在目标逻辑上。
"""

from __future__ import annotations

import ast
import difflib
import pathlib
from dataclasses import dataclass
from typing import Any


@dataclass
class FunctionPatch:
    """一次函数级替换。"""

    file_path: str
    function_name: str
    new_source: str

    @property
    def key(self) -> tuple[str, str]:
        return (self.file_path, self.function_name)


def _read_text(path: pathlib.Path) -> str:
    """按原样读取文本，**不转换行尾**（否则 Windows 上会把整个文件改成 CRLF）。"""
    with path.open("r", encoding="utf-8", newline="") as handle:
        return handle.read()


def _write_text(path: pathlib.Path, text: str) -> None:
    """按原样写回文本，不做行尾转换。"""
    with path.open("w", encoding="utf-8", newline="") as handle:
        handle.write(text)


def _detect_newline(text: str) -> str:
    """探测文件使用的行尾风格。"""
    return "\r\n" if "\r\n" in text else "\n"


def _normalize(text: str) -> str:
    """统一成 ``\\n``，用于 AST 解析与 diff 计算。"""
    return text.replace("\r\n", "\n").replace("\r", "\n")


def _unchanged_bytes(original: str, content: str) -> int:
    """统计**逐字节未被改动**的内容大小（改动行之外的原文）。"""
    a = original.splitlines(keepends=True)
    b = content.splitlines(keepends=True)
    matcher = difflib.SequenceMatcher(a=a, b=b, autojunk=False)
    total = 0
    for tag, i1, i2, _j1, _j2 in matcher.get_opcodes():
        if tag == "equal":
            total += len("".join(a[i1:i2]).encode("utf-8"))
    return total


def _find_function(tree: ast.AST, name: str) -> ast.FunctionDef | ast.AsyncFunctionDef | None:
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name:
            return node
    return None


def splice_function(
    original: str, function_name: str, new_source: str
) -> tuple[str, dict[str, Any]]:
    """把 ``new_source`` 替换掉原文件中的 ``function_name`` 实现。

    Returns:
        ``(新文件内容, 变更元信息)``。
    """
    tree = ast.parse(original)
    node = _find_function(tree, function_name)
    if node is None:
        raise RuntimeError(f"原文件中找不到函数 {function_name}")

    lines = original.splitlines(keepends=True)
    start = node.lineno - 1
    end = node.end_lineno or node.lineno

    # 装饰器保留在原处，替换体时不重复。
    decorator_lines = [d.lineno for d in getattr(node, "decorator_list", [])]
    preserved = ""
    if decorator_lines:
        first_decorator = min(decorator_lines) - 1
        preserved = "".join(lines[first_decorator:start])
        start = first_decorator

    body = new_source if new_source.endswith("\n") else new_source + "\n"
    new_content = "".join(lines[:start]) + preserved + body + "".join(lines[end:])

    diff = "".join(
        difflib.unified_diff(
            original.splitlines(keepends=True),
            new_content.splitlines(keepends=True),
            fromfile=f"a/{function_name}",
            tofile=f"b/{function_name}",
            n=3,
        )
    )
    meta = {
        "function": function_name,
        "original_line_range": [node.lineno, node.end_lineno],
        "added_lines": sum(
            1 for line in diff.splitlines() if line.startswith("+") and not line.startswith("+++")
        ),
        "removed_lines": sum(
            1 for line in diff.splitlines() if line.startswith("-") and not line.startswith("---")
        ),
        "diff": diff,
    }
    return new_content, meta


def apply_function_patches(
    patches: list[FunctionPatch], root: pathlib.Path
) -> list[dict[str, Any]]:
    """把函数级补丁写回磁盘，返回逐文件的变更记录。"""
    by_file: dict[str, list[FunctionPatch]] = {}
    for patch in patches:
        by_file.setdefault(patch.file_path, []).append(patch)

    changes: list[dict[str, Any]] = []
    for rel_path, file_patches in by_file.items():
        target = (root / rel_path).resolve()
        try:
            target.relative_to(root.resolve())
        except ValueError:
            raise RuntimeError(f"模型试图写入工程根目录之外的路径：{rel_path}") from None
        if not target.exists():
            raise RuntimeError(f"补丁目标文件不存在：{rel_path}")

        original = _read_text(target)
        newline = _detect_newline(original)
        original = _normalize(original)
        content = original
        metas: list[dict[str, Any]] = []
        for patch in file_patches:
            content, meta = splice_function(content, patch.function_name, patch.new_source)
            metas.append(meta)

        try:
            ast.parse(content)
        except SyntaxError as exc:
            raise RuntimeError(f"补丁后的 {rel_path} 语法不合法，已拒绝写入：{exc}") from exc

        diff = "".join(
            difflib.unified_diff(
                original.splitlines(keepends=True),
                content.splitlines(keepends=True),
                fromfile=f"a/{rel_path}",
                tofile=f"b/{rel_path}",
                n=3,
            )
        )
        # 写回时还原原文件的行尾风格，避免把整个文件改写成另一种行尾。
        _write_text(target, content.replace("\n", newline))
        unchanged_bytes = _unchanged_bytes(original, content)
        changes.append(
            {
                "path": rel_path,
                "changed": original != content,
                "mode": "function-splice",
                "functions": [patch.function_name for patch in file_patches],
                "added_lines": sum(
                    1
                    for line in diff.splitlines()
                    if line.startswith("+") and not line.startswith("+++")
                ),
                "removed_lines": sum(
                    1
                    for line in diff.splitlines()
                    if line.startswith("-") and not line.startswith("---")
                ),
                "unchanged_bytes": unchanged_bytes,
                "diff": diff,
                "function_diffs": metas,
            }
        )
    return changes
