"""中英文混合的分词。

分词同时用于**写入**（建立倒排索引）和**查询**（构造检索表达式），
必须保证两侧一致，否则检索会漏召回。
"""

from __future__ import annotations

import re

_ASCII_WORD = re.compile(r"[A-Za-z][A-Za-z0-9]*")
_CJK_RUN = re.compile(r"[\u4e00-\u9fff]+")
_CODEISH = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:\-]*")

_CAMEL = re.compile(r"[A-Z]?[a-z]+|[A-Z]+(?![a-z])|\d+")


def _split_identifier(word: str) -> list[str]:
    """把 ``apply_coupon`` / ``ApplyCoupon`` 拆成 ``apply`` / ``coupon``。"""
    parts: list[str] = []
    for chunk in re.split(r"[_\-.]+", word):
        if not chunk:
            continue
        if chunk.isupper() or not chunk[:1].isalpha():
            parts.append(chunk.lower())
            continue
        parts.extend(m.lower() for m in _CAMEL.findall(chunk) if m)
    return parts


def tokenize(text: str) -> list[str]:
    """把文本切成检索用的 token 列表（保留重复，调用方自行统计权重）。"""
    if not text:
        return []
    tokens: list[str] = []

    for match in _CODEISH.finditer(text):
        raw = match.group(0).strip("._:-")
        if not raw:
            continue
        lowered = raw.lower()
        tokens.append(lowered)
        tokens.extend(_split_identifier(raw))

    for match in _ASCII_WORD.finditer(text):
        word = match.group(0)
        lowered = word.lower()
        if lowered not in tokens:
            tokens.append(lowered)
        tokens.extend(p for p in _split_identifier(word) if p != lowered)

    for match in _CJK_RUN.finditer(text):
        run = match.group(0)
        tokens.append(run)
        for i in range(len(run) - 1):
            tokens.append(run[i : i + 2])

    return [t for t in tokens if t]


def token_weights(text: str) -> dict[str, int]:
    """统计 token 出现次数，得到 term frequency。"""
    weights: dict[str, int] = {}
    for token in tokenize(text):
        weights[token] = weights.get(token, 0) + 1
    return weights


def query_tokens(text: str) -> list[str]:
    """查询侧 token：去重后按长度优先（更具体的标识符权重更高）。"""
    seen: dict[str, None] = {}
    for token in tokenize(text):
        seen.setdefault(token, None)
    return sorted(seen, key=lambda t: (-len(t), t))


def search_text(*parts: str) -> str:
    """拼接检索文本。"""
    return "\n".join(p for p in parts if p)
