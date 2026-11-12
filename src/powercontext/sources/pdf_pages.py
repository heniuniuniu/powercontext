#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
# http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Page-level PDF capture so cited evidence traces back to one physical page."""

from __future__ import annotations

import asyncio
import hashlib
import io
import itertools
import math
import re
import unicodedata
from collections import Counter
from dataclasses import dataclass, replace
from typing import TYPE_CHECKING, Protocol
from urllib.parse import quote

from pydantic import BaseModel
from pypdf import PdfReader

from powercontext.artifacts import MemoryCitation
from powercontext.builtin.runtime.models import (
    GetMemoryEntryRequest,
    MemoryEntryRecord,
    MemorySearchPage,
    SearchMemoryRequest,
)
from powercontext.errors import InvalidSourceReferenceError
from powercontext.limits import MAX_SOURCE_ID_LENGTH
from powercontext.sources.definitions import SourceDefinition
from powercontext.sources.models import Source, SourceMaterialization
from powercontext.sources.projections import TEXT_EVIDENCE_PROJECTION_KEY, TextEvidence

if TYPE_CHECKING:
    from powercontext.builtin.artifacts.memory.models import MemoryEntryVersion

PDF_PAGE_SOURCE_NAME = "pdf-page"
"""Stable registration name of the page-level PDF Source Definition."""

PDF_PAGE_SOURCE_VERSION = "1"
"""Durable version of the page-level PDF Source Definition."""

MAX_EVIDENCE_TEXT_BYTES = 8192
"""Upper bound of the UTF-8 bytes one stored page body may occupy."""

_CANDIDATE_POOL_MULTIPLIER = 20
"""How many retrieved pages one requested citation may draw its main pool from."""

_MIN_CANDIDATE_POOL = 200
"""Smallest number of retrieved pages the reranking stage may inspect."""

_MAX_CANDIDATE_POOL = 800
"""Largest number of retrieved pages the reranking stage may inspect."""

_CONCEPT_POOL = 150
"""How many retrieved pages one focused question part may contribute."""

_HEADING_LINES = 8
"""How many leading lines of a page count as its heading."""

_COVERAGE_WEIGHT = 1.0
"""Weight of the question-term coverage in the reranking score."""

_PHRASE_WEIGHT = 0.5
"""Weight of adjacent-question-term matches in the reranking score."""

_STATEMENT_WEIGHT = 0.8
"""Weight of the statement-heading match in the reranking score."""

_SECTION_WEIGHT = 0.6
"""Weight of the report-section match in the reranking score."""

_TOPIC_WEIGHT = 0.8
"""Weight of the note-topic match in the reranking score."""

_YEAR_WEIGHT = 0.3
"""Weight of the fiscal-year match in the reranking score."""

_PROPER_WEIGHT = 0.3
"""Weight of the named-entity match in the reranking score."""

_AFFINITY_WEIGHT = 0.3
"""Weight of the document affinity in the reranking score."""

_DOCUMENT_WEIGHT = 0.5
"""Weight of the document that a question names by name."""

_PRIOR_WEIGHT = 0.25
"""Weight of the retrieval order in the reranking score."""

_ADJACENT_WEIGHT = 0.25
"""Weight of the neighbouring-page score in the reranking score."""

_QUOTA_SHARE = 0.6
"""Share of the returned pages reserved for the page kinds the question names."""

_MIN_QUOTA = 2
"""Smallest number of pages one named page kind may contribute."""

_MAX_QUOTA = 3
"""Largest number of pages one named page kind may contribute."""

_TOKEN_PATTERN = re.compile(r"[a-z0-9]+")
"""Lexical token pattern shared by the question and the retrieved page bodies."""

_YEAR_PATTERN = re.compile(r"^(?:fy)?((?:19|20)\d{2})$")
"""Pattern of a fiscal year written with or without its prefix."""

_SHORT_YEAR_PATTERN = re.compile(r"^fy(\d{2})$")
"""Pattern of a two digit fiscal year."""

_PROPER_PATTERN = re.compile(r"\b([A-Z][A-Za-z0-9&.]*)\b")
"""Pattern of a capitalised word that may name the reported company."""

_STOP_WORDS = frozenset(
    {
        "a",
        "about",
        "after",
        "all",
        "also",
        "an",
        "and",
        "any",
        "are",
        "as",
        "at",
        "be",
        "been",
        "before",
        "being",
        "between",
        "both",
        "but",
        "by",
        "can",
        "could",
        "did",
        "do",
        "does",
        "doing",
        "during",
        "each",
        "for",
        "from",
        "had",
        "has",
        "have",
        "having",
        "how",
        "if",
        "in",
        "into",
        "is",
        "it",
        "its",
        "me",
        "my",
        "of",
        "on",
        "or",
        "our",
        "over",
        "per",
        "same",
        "should",
        "since",
        "so",
        "some",
        "such",
        "than",
        "that",
        "the",
        "their",
        "them",
        "then",
        "there",
        "these",
        "they",
        "this",
        "those",
        "through",
        "to",
        "under",
        "up",
        "was",
        "we",
        "were",
        "what",
        "when",
        "where",
        "which",
        "while",
        "who",
        "why",
        "will",
        "with",
        "would",
        "you",
        "your",
    },
)
"""Question words that carry no evidence about one financial statement page."""

_SENTENCE_WORDS = frozenset(
    {
        "a",
        "an",
        "and",
        "answer",
        "are",
        "as",
        "assume",
        "assuming",
        "based",
        "be",
        "by",
        "calculate",
        "considering",
        "could",
        "define",
        "defined",
        "determine",
        "did",
        "do",
        "does",
        "explain",
        "following",
        "for",
        "from",
        "give",
        "given",
        "how",
        "i",
        "if",
        "in",
        "is",
        "it",
        "its",
        "me",
        "my",
        "of",
        "on",
        "or",
        "our",
        "please",
        "rely",
        "relying",
        "respond",
        "response",
        "show",
        "shown",
        "state",
        "such",
        "than",
        "that",
        "the",
        "their",
        "then",
        "there",
        "these",
        "this",
        "those",
        "to",
        "us",
        "use",
        "used",
        "using",
        "was",
        "we",
        "were",
        "what",
        "when",
        "where",
        "which",
        "who",
        "why",
        "will",
        "with",
        "within",
        "would",
        "you",
        "your",
    },
)
"""Question words that open or close a sentence instead of naming evidence."""

_FINANCE_ACRONYMS = frozenset(
    {
        "cfo",
        "cogs",
        "ebit",
        "ebitda",
        "eps",
        "fcf",
        "fy",
        "gaap",
        "ir",
        "ltm",
        "roe",
        "roi",
        "usd",
        "yoy",
    },
)
"""Acronyms that describe a measure instead of naming the reported company."""

_STATEMENT_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("comprehensive", re.compile(r"statements?\s+of\s+comprehensive")),
    ("equity", re.compile(r"statements?\s+of\s+(?:shareholders|stockholders)")),
    ("balance", re.compile(r"balance\s+sheets?")),
    ("income", re.compile(r"statements?\s+of\s+(?:operations|income|earnings)")),
    ("cash", re.compile(r"statements?\s+of\s+cash\s+flows?")),
    ("segment", re.compile(r"summary\s+of\s+business\s+segment\s+data")),
    ("segment", re.compile(r"segment\s+(?:information|reporting|data)")),
    ("revenue", re.compile(r"consolidated\s+operating\s+revenues")),
)
"""Heading patterns that name the statement one page shows."""

_ITEM_HEADING_PATTERN = re.compile(r"^\s*item\s*(\d+[a-z]?)\b", re.IGNORECASE)
"""Pattern of the item heading that opens one part of a report."""

_ITEM_SECTIONS: dict[str, str] = {
    "1": "business",
    "1a": "risk",
    "1b": "business",
    "1c": "cybersecurity",
    "2": "properties",
    "3": "legal",
    "4": "mine_safety",
    "5": "equity_market",
    "6": "reserved",
    "7": "mda",
    "7a": "mda_risk",
    "8": "statements",
    "9": "controls",
    "9a": "controls",
    "9b": "disclosure",
    "9c": "disclosure",
    "10": "reserved",
    "11": "executive",
    "12": "graph",
    "13": "graph",
    "14": "graph",
    "15": "exhibits",
}
"""Item headings of a report mapped to the section they open."""

_NOTES_HEADING_PATTERN = re.compile(
    r"^\s*notes?\s+to\s+(?:the\s+)?(?:unaudited\s+)?consolidated\s+financial\s+statements",
    re.IGNORECASE | re.MULTILINE,
)
"""Pattern of the heading that opens the notes to the financial statements."""

_NOTE_TITLE_PATTERN = re.compile(r"^\s*note\s+\d+[.:)]?\s*([^\n]{0,90})", re.IGNORECASE | re.MULTILINE)
"""Pattern of one note title inside a page."""

_NOTE_TITLE_TOPICS: tuple[tuple[str, str], ...] = (
    ("restructur", "restructuring"),
    ("impair", "impairment"),
    ("debt", "debt"),
    ("borrow", "debt"),
    ("notes payable", "debt"),
    ("segment", "segment"),
    ("concentration", "concentration"),
    ("credit risk", "concentration"),
    ("geograph", "geographic"),
    ("disaggregat", "revenue"),
    ("revenue", "revenue"),
    ("contract", "revenue"),
    ("litigation", "litigation"),
    ("contingenc", "litigation"),
    ("commitments", "litigation"),
    ("lease", "leases"),
    ("income tax", "taxes"),
    ("goodwill", "goodwill"),
    ("inventor", "inventories"),
    ("receivab", "receivables"),
    ("pension", "benefits"),
    ("benefit", "benefits"),
    ("fair value", "fair_value"),
    ("share-based", "stock_comp"),
    ("stock-based", "stock_comp"),
    ("earnings per share", "eps"),
)
"""Note titles mapped to the topic the note discusses."""

_TOPIC_KEYWORDS: dict[str, tuple[str, ...]] = {
    "restructuring": ("restructuring", "productivity plan", "severance", "workforce reduction"),
    "impairment": ("impairment", "write-down", "goodwill"),
    "debt": ("long-term debt", "long term debt", "notes payable", "debt obligations", "borrowings", "credit facility"),
    "segment": ("reportable segment", "business segment", "segment information", "chief operating decision maker"),
    "concentration": (
        "concentration",
        "credit risk",
        "significant customer",
        "major customer",
        "largest customer",
        "one customer",
        "significant portion of our",
        "substantial portion of our",
        "of our consolidated net revenue",
        "of our consolidated revenue",
    ),
    "geographic": ("geographic", "geographies", "long-lived assets"),
    "litigation": ("litigation", "lawsuit", "legal proceeding", "legal matter", "class action", "contingenc"),
    "taxes": ("income tax", "provision for income taxes", "effective tax rate", "deferred tax"),
    "leases": ("operating lease", "finance lease", "lease obligation"),
    "inventories": ("inventor",),
    "revenue": ("disaggregat", "revenue recognition", "contract liability", "performance obligation"),
    "benefits": ("benefit plan", "postretirement", "defined benefit"),
    "eps": ("earnings per share",),
    "fair_value": ("fair value",),
    "stock_comp": ("share-based", "stock-based"),
    "property": ("property, plant and equipment", "property plant and equipment"),
    "equity": ("treasury stock", "additional paid-in capital", "common stock"),
}
"""Body keywords mapped to the topic one page discusses."""

_TOPIC_MIN_HITS = 1
"""How many topic markers one page needs before it counts as that topic."""

_MAX_TOPICS = 3
"""How many topics one page may carry at once."""

_WORD_SYNONYMS: dict[str, str] = {
    "increased": "increase",
    "decreased": "decrease",
    "reported": "report",
    "reporting": "report",
    "outlined": "outline",
    "operates": "operate",
    "operating": "operation",
    "operations": "operation",
    "intensity": "intensive",
    "geographies": "geography",
    "geographic": "geography",
    "lawsuit": "litigation",
    "lawsuits": "litigation",
    "sale": "revenue",
    "sales": "revenue",
    "inventories": "inventory",
    "receivables": "receivable",
    "liabilities": "liability",
}
"""Word forms that mean the same thing once both sides are normalised."""


@dataclass(frozen=True, slots=True)
class _Concept:
    """One financial concept and the report places that carry its figures."""

    phrase: str
    terms: tuple[str, ...]
    statements: tuple[str, ...] = ()
    sections: tuple[str, ...] = ()
    topics: tuple[str, ...] = ()


_CONCEPTS: tuple[_Concept, ...] = (
    _Concept(
        phrase="effective tax rate",
        terms=("income before income taxes", "provision for income taxes", "income tax expense", "net income"),
        statements=("income",),
        topics=("taxes",),
    ),
    _Concept(
        phrase="current ratio",
        terms=("current assets", "current liabilities", "working capital"),
        statements=("balance",),
    ),
    _Concept(
        phrase="quick ratio",
        terms=("cash and cash equivalents", "short-term investments", "accounts receivable", "current liabilities"),
        statements=("balance",),
    ),
    _Concept(
        phrase="working capital",
        terms=("current assets", "current liabilities", "working capital"),
        statements=("balance",),
    ),
    _Concept(
        phrase="return on equity",
        terms=("net income", "total equity"),
        statements=("income", "balance"),
    ),
    _Concept(
        phrase="return on assets",
        terms=("net income", "total assets"),
        statements=("income", "balance"),
    ),
    _Concept(
        phrase="debt to equity",
        terms=("total debt", "long-term debt", "total equity"),
        statements=("balance",),
        topics=("debt",),
    ),
    _Concept(
        phrase="interest coverage",
        terms=("interest expense", "operating income"),
        statements=("income",),
    ),
    _Concept(
        phrase="days sales outstanding",
        terms=("accounts receivable", "net revenue"),
        statements=("balance",),
    ),
    _Concept(
        phrase="days inventory outstanding",
        terms=("inventories", "cost of sales"),
        statements=("balance", "income"),
    ),
    _Concept(
        phrase="inventory turnover",
        terms=("inventories", "cost of sales"),
        statements=("balance", "income"),
    ),
    _Concept(
        phrase="gross margin",
        terms=("gross profit", "cost of sales", "net revenue"),
        statements=("income",),
        sections=("mda",),
    ),
    _Concept(
        phrase="operating margin",
        terms=("operating income", "gross profit", "net revenue"),
        statements=("income",),
        sections=("mda",),
    ),
    _Concept(
        phrase="net margin",
        terms=("net income", "net revenue"),
        statements=("income",),
    ),
    _Concept(
        phrase="gross profit",
        terms=("gross profit", "cost of sales"),
        statements=("income",),
    ),
    _Concept(
        phrase="free cash flow",
        terms=("net cash provided by operating activities", "purchases of property plant and equipment"),
        statements=("cash",),
    ),
    _Concept(
        phrase="capital expenditure",
        terms=("capital expenditures", "purchases of property plant and equipment", "property plant and equipment"),
        statements=("cash", "balance"),
    ),
    _Concept(
        phrase="capital intensive",
        terms=(
            "capital expenditures",
            "property plant and equipment",
            "net revenue",
            "total assets",
            "operating income",
        ),
        statements=("income", "balance", "revenue"),
        sections=("mda",),
    ),
    _Concept(
        phrase="book value",
        terms=("total equity", "common stock", "shares outstanding"),
        statements=("balance",),
    ),
    _Concept(
        phrase="share repurchase",
        terms=("repurchase", "treasury stock", "share repurchase program"),
        sections=("equity_market",),
        statements=("cash",),
    ),
    _Concept(
        phrase="shareholders equity",
        terms=("total equity", "common stock"),
        statements=("balance",),
    ),
    _Concept(
        phrase="stockholders equity",
        terms=("total equity", "common stock"),
        statements=("balance",),
    ),
    _Concept(
        phrase="total liabilities",
        terms=("total liabilities", "current liabilities"),
        statements=("balance",),
    ),
    _Concept(
        phrase="total assets",
        terms=("total assets", "current assets", "property plant and equipment"),
        statements=("balance",),
    ),
    _Concept(
        phrase="long term debt",
        terms=("long-term debt", "current portion", "notes payable"),
        statements=("balance",),
        topics=("debt",),
    ),
    _Concept(
        phrase="accounts receivable",
        terms=("accounts receivable",),
        statements=("balance",),
    ),
    _Concept(
        phrase="cost of sales",
        terms=("cost of sales", "cost of goods sold"),
        statements=("income",),
    ),
    _Concept(
        phrase="cost of goods sold",
        terms=("cost of sales", "cost of goods sold"),
        statements=("income",),
    ),
    _Concept(
        phrase="operating income",
        terms=("operating income", "income from operations"),
        statements=("income",),
    ),
    _Concept(
        phrase="interest expense",
        terms=("interest expense",),
        statements=("income",),
    ),
    _Concept(
        phrase="net income",
        terms=("net income", "net earnings"),
        statements=("income",),
    ),
    _Concept(
        phrase="free cash",
        terms=("operating activities", "capital expenditures"),
        statements=("cash",),
    ),
    _Concept(
        phrase="ebitda",
        terms=("operating income", "depreciation", "amortization"),
        statements=("income", "cash"),
    ),
    _Concept(
        phrase="ebit",
        terms=("operating income",),
        statements=("income",),
    ),
    _Concept(
        phrase="capex",
        terms=("capital expenditures", "purchases of property plant and equipment", "property plant and equipment"),
        statements=("cash", "balance"),
    ),
    _Concept(
        phrase="revenue",
        terms=("net revenue", "total revenues", "net sales"),
        statements=("income",),
    ),
    _Concept(
        phrase="net sales",
        terms=("net sales", "net revenue"),
        statements=("income",),
    ),
    _Concept(
        phrase="dividend",
        terms=("cash dividends", "dividends declared", "per share"),
        sections=("equity_market",),
        statements=("cash",),
    ),
    _Concept(
        phrase="primary customer",
        terms=(
            "customer",
            "revenue",
            "portion",
            "percentage",
            "commercial",
            "government",
            "contract",
            "concentration",
        ),
        sections=("business", "risk"),
        topics=("concentration",),
    ),
    _Concept(
        phrase="customer concentration",
        terms=(
            "customer",
            "revenue",
            "portion",
            "percentage",
            "commercial",
            "government",
            "contract",
            "concentration",
        ),
        sections=("business", "risk"),
        topics=("concentration",),
    ),
    _Concept(
        phrase="geographies",
        terms=("region", "americas", "europe", "asia", "africa", "international", "worldwide"),
        sections=("business",),
        topics=("geographic", "segment"),
    ),
    _Concept(
        phrase="geographic",
        terms=("region", "americas", "europe", "asia", "africa", "international", "worldwide"),
        sections=("business",),
        topics=("geographic", "segment"),
    ),
    _Concept(
        phrase="legal battle",
        terms=("legal", "litigation", "lawsuit", "proceeding", "claim", "contingency", "attorney", "investigation"),
        sections=("legal",),
        topics=("litigation",),
    ),
    _Concept(
        phrase="legal proceeding",
        terms=("legal", "litigation", "lawsuit", "proceeding", "claim", "contingency", "attorney", "investigation"),
        sections=("legal",),
        topics=("litigation",),
    ),
    _Concept(
        phrase="litigation",
        terms=("legal", "litigation", "lawsuit", "proceeding", "claim", "contingency", "attorney", "investigation"),
        sections=("legal",),
        topics=("litigation",),
    ),
    _Concept(
        phrase="lawsuit",
        terms=("legal", "litigation", "lawsuit", "proceeding", "claim", "contingency", "attorney"),
        sections=("legal",),
        topics=("litigation",),
    ),
    _Concept(
        phrase="restructuring",
        terms=("restructuring", "productivity plan", "severance", "impairment", "charge"),
        topics=("restructuring", "impairment"),
    ),
    _Concept(
        phrase="impairment",
        terms=("impairment", "goodwill", "charge"),
        topics=("impairment", "restructuring"),
    ),
    _Concept(
        phrase="segment",
        terms=("segment", "reportable segment", "net revenue", "operating income"),
        statements=("segment",),
        sections=("mda",),
        topics=("segment",),
    ),
    _Concept(
        phrase="product categories",
        terms=("products", "services", "revenue", "segment"),
        statements=("segment",),
        topics=("segment",),
    ),
    _Concept(
        phrase="service categories",
        terms=("products", "services", "revenue", "segment"),
        statements=("segment",),
        topics=("segment",),
    ),
    _Concept(
        phrase="products and services",
        terms=("products", "services", "business"),
        sections=("business",),
    ),
    _Concept(
        phrase="major products",
        terms=("products", "services", "business"),
        sections=("business",),
    ),
    _Concept(
        phrase="production rate",
        terms=("production rate", "production system", "deliveries", "aircraft"),
        sections=("business", "risk"),
    ),
    _Concept(
        phrase="cyclical",
        terms=("cyclical", "cycle", "seasonal", "demand", "industry"),
        sections=("business", "risk"),
    ),
    _Concept(
        phrase="drove",
        terms=("increase", "decrease", "driven", "growth", "segment"),
        sections=("mda",),
    ),
    _Concept(
        phrase="driven by",
        terms=("increase", "decrease", "driven", "growth", "segment"),
        sections=("mda",),
    ),
    _Concept(
        phrase="debt",
        terms=("debt", "notes payable", "maturities", "interest rate", "borrowings"),
        topics=("debt",),
    ),
)
"""Financial concepts mapped to the report places that answer them.

The mapping is a domain thesaurus: it names the statement lines, report sections
and note topics an analyst has to read for one concept, and it never refers to a
document, a page or an answer.
"""

_STATEMENT_PHRASES: dict[str, str] = {
    "income statement": "income",
    "statements of income": "income",
    "statement of income": "income",
    "statement of operations": "income",
    "statements of operations": "income",
    "p&l statement": "income",
    "balance sheet": "balance",
    "balance sheets": "balance",
    "statement of financial position": "balance",
    "cash flow statement": "cash",
    "statement of cash flow": "cash",
    "statements of cash flow": "cash",
    "statement of cash flows": "cash",
    "statements of cash flows": "cash",
    "comprehensive income": "comprehensive",
    "statement of equity": "equity",
    "statements of equity": "equity",
}
"""Statements named in a question mapped to the statement kind they belong to."""

_SECTION_PHRASES: dict[str, str] = {
    "risk factors": "risk",
    "item 1a": "risk",
    "legal proceedings": "legal",
    "item 3": "legal",
    "properties": "properties",
    "item 2": "properties",
    "management's discussion": "mda",
    "item 7": "mda",
    "market for registrant": "equity_market",
    "item 5": "equity_market",
    "financial statements and supplementary data": "statements",
    "item 8": "statements",
}
"""Report sections named in a question mapped to the section they open."""

_TOPIC_PHRASES: dict[str, str] = {
    "debt": "debt",
    "borrowings": "debt",
    "restructuring": "restructuring",
    "impairment": "impairment",
    "segment": "segment",
    "concentration": "concentration",
    "geographic": "geographic",
    "geographies": "geographic",
    "litigation": "litigation",
    "lawsuit": "litigation",
    "legal proceeding": "litigation",
    "income tax": "taxes",
    "lease": "leases",
    "goodwill": "goodwill",
    "earnings per share": "eps",
    "inventory": "inventories",
    "receivable": "receivables",
}
"""Note topics named in a question mapped to the topic they discuss."""


class PDFDocumentInput(BaseModel):
    """One raw PDF document offered to the capture pipeline."""

    document_id: str
    content: bytes


class PDFPageInput(BaseModel):
    """One captured PDF page addressed by its physical page number."""

    document_id: str
    document_sha256: str
    page_number: int
    text: str


class PDFPageSource(Source):
    """Captured PDF page kept with the document revision it was read from."""

    source_id: str
    document_id: str
    document_sha256: str
    page_number: int
    text: str


class PageSearchScope(Protocol):
    """Read-only Memory scope used to retrieve evidence pages."""

    async def search(self, request: SearchMemoryRequest, /) -> MemorySearchPage: ...

    async def get(self, request: GetMemoryEntryRequest, /) -> MemoryEntryRecord: ...


def pdf_page_source_id(document_id: str, document_sha256: str, page_number: int) -> str:
    """Return the stable identity of one captured PDF page."""

    _validate_document_id(document_id)
    _validate_document_sha256(document_sha256)
    _validate_page_number(page_number)
    encoded = quote(document_id, safe="")
    source_id = f"{PDF_PAGE_SOURCE_NAME}:{encoded}:{document_sha256}:{page_number}"
    if len(source_id) > MAX_SOURCE_ID_LENGTH:
        digest = hashlib.sha256(document_id.encode("utf-8")).hexdigest()
        source_id = f"{PDF_PAGE_SOURCE_NAME}:{digest}:{document_sha256}:{page_number}"
    if len(source_id) > MAX_SOURCE_ID_LENGTH:
        raise InvalidSourceReferenceError(
            "source_id",
            f"must not exceed {MAX_SOURCE_ID_LENGTH} characters",
        )
    return source_id


def _read_pdf_pages(content: bytes, document_id: str) -> list[PDFPageInput]:
    """Open one PDF and return every physical page it holds."""

    reader = PdfReader(io.BytesIO(content))
    if reader.is_encrypted and not reader.decrypt(""):
        raise ValueError("the document cannot be opened with an empty password")  # noqa: TRY003
    document_sha256 = hashlib.sha256(content).hexdigest()
    pages: list[PDFPageInput] = []
    for index, page in enumerate(reader.pages, start=1):
        text = unicodedata.normalize("NFC", page.extract_text() or "")
        pages.append(
            PDFPageInput(
                document_id=document_id,
                document_sha256=document_sha256,
                page_number=index,
                text=_fit_page_text(document_id, text),
            )
        )
    return pages


def parse_pdf_document(value: PDFDocumentInput) -> tuple[PDFPageInput, ...]:
    """Parse one raw PDF document into the physical pages it captures."""

    document_id = value.document_id
    _validate_document_id(document_id)
    content = value.content
    if not isinstance(content, (bytes, bytearray)) or not content:
        raise ValueError(f"PDF document {document_id!r} is empty")  # noqa: TRY003
    try:
        pages = _read_pdf_pages(bytes(content), document_id)
    except Exception as error:
        raise ValueError(f"PDF document {document_id!r} cannot be parsed: {error}") from error  # noqa: TRY003
    if not pages:
        raise ValueError(f"PDF document {document_id!r} has no physical page")  # noqa: TRY003
    return tuple(pages)


@dataclass(frozen=True, slots=True)
class PDFPageTextEvidenceProjection:
    """Project one captured PDF page into the standard textual evidence shape."""

    name: str = TEXT_EVIDENCE_PROJECTION_KEY.name
    version: str = TEXT_EVIDENCE_PROJECTION_KEY.version
    source_class: type[PDFPageSource] = PDFPageSource
    output_class: type[TextEvidence] = TextEvidence

    def project(self, source: PDFPageSource, /) -> TextEvidence:
        """Render the captured page as its document identity and page body."""

        return TextEvidence(
            source_type=PDF_PAGE_SOURCE_NAME,
            source_id=source.source_id,
            content=f"{source.document_id}\n{source.text}",
            metadata={
                "document_id": source.document_id,
                "document_sha256": source.document_sha256,
                "page_number": source.page_number,
            },
        )


@dataclass(frozen=True, slots=True)
class PDFPageSourceDefinition:
    """Bind captured PDF pages to the durable Source contract."""

    version: str = PDF_PAGE_SOURCE_VERSION
    projections: tuple[PDFPageTextEvidenceProjection, ...] = (PDFPageTextEvidenceProjection(),)

    @property
    def input_class(self) -> type[PDFPageInput]:
        return PDFPageInput

    @property
    def name(self) -> str:
        return PDF_PAGE_SOURCE_NAME

    @property
    def source_class(self) -> type[PDFPageSource]:
        return PDFPageSource

    async def resolve(self, value: PDFPageInput, /) -> PDFPageSource:
        """Capture one parsed page as a materialized Source."""

        source_id = pdf_page_source_id(value.document_id, value.document_sha256, value.page_number)
        return PDFPageSource(
            name=source_id,
            definition_version=self.version,
            materialization=SourceMaterialization.CAPTURED,
            description=None,
            source_id=source_id,
            document_id=value.document_id,
            document_sha256=value.document_sha256,
            page_number=value.page_number,
            text=value.text,
        )

    async def read(self, source: PDFPageSource, /) -> PDFPageInput:
        """Read one captured page back as the value it was captured from."""

        return PDFPageInput(
            document_id=source.document_id,
            document_sha256=source.document_sha256,
            page_number=source.page_number,
            text=source.text,
        )


PDF_PAGE_SOURCE_DEFINITION: SourceDefinition[PDFPageInput, PDFPageSource, PDFPageInput] = PDFPageSourceDefinition()
"""Definition used to capture PDF pages and project their evidence."""


@dataclass(frozen=True, slots=True)
class _PageProfile:
    """One stored page reduced to the signals the reranking stage reads."""

    document_id: str
    page_number: int
    text: str
    counts: Counter[str]
    statements: frozenset[str]
    sections: frozenset[str]
    topics: frozenset[str]
    inherited_sections: frozenset[str]
    citation: MemoryCitation | None = None
    rank: int = 0

    def kinds(self) -> frozenset[str]:
        """Return the statement kinds and the sections this page belongs to."""

        return self.statements | self.sections | self.inherited_sections


@dataclass(frozen=True, slots=True)
class _QuestionPlan:
    """The parts of one question that drive page retrieval and reranking."""

    raw: str
    core_terms: tuple[str, ...]
    expanded_terms: tuple[str, ...]
    concept_groups: tuple[tuple[str, ...], ...]
    statement_kinds: tuple[str, ...]
    section_kinds: tuple[str, ...]
    topics: tuple[str, ...]
    years: tuple[str, ...]
    proper_terms: tuple[str, ...]
    phrases: tuple[tuple[re.Pattern[str], float], ...]

    def named_kinds(self) -> tuple[tuple[str, str], ...]:
        """Return the page kinds this question asks for, as kind and name."""

        named: list[tuple[str, str]] = []
        for kind in self.statement_kinds:
            named.append(("statement", kind))
        for kind in self.section_kinds:
            named.append(("section", kind))
        for topic in self.topics:
            named.append(("topic", topic))
        return tuple(dict.fromkeys(named))


def _plan_question(question: str) -> _QuestionPlan:
    """Read one question into the terms and places its retrieval needs."""

    lowered = question.casefold()
    tokens = _TOKEN_PATTERN.findall(lowered)
    years = _question_years(tokens)
    core = tuple(
        dict.fromkeys(
            token for token in tokens if token not in _STOP_WORDS and len(token) > 1 and not _YEAR_PATTERN.match(token)
        )
    )
    spoken = lowered.replace("-", " ").replace("–", " ").replace("—", " ")  # noqa: RUF001
    groups: list[tuple[str, ...]] = []
    statements: list[str] = []
    sections: list[str] = []
    topics: list[str] = []
    for concept in _CONCEPTS:
        if not _contains_phrase(spoken, concept.phrase):
            continue
        groups.append(_concept_terms(concept.terms))
        statements.extend(concept.statements)
        sections.extend(concept.sections)
        topics.extend(concept.topics)
    for phrase, kind in _STATEMENT_PHRASES.items():
        if _contains_phrase(spoken, phrase):
            statements.append(kind)
    for phrase, kind in _SECTION_PHRASES.items():
        if _contains_phrase(spoken, phrase):
            sections.append(kind)
    for phrase, topic in _TOPIC_PHRASES.items():
        if _contains_phrase(spoken, phrase):
            topics.append(topic)
    expanded = tuple(dict.fromkeys(term for group in groups for term in group))
    return _QuestionPlan(
        raw=question,
        core_terms=core,
        expanded_terms=tuple(dict.fromkeys((*core, *expanded, *years))),
        concept_groups=tuple(groups),
        statement_kinds=tuple(dict.fromkeys(statements)),
        section_kinds=tuple(dict.fromkeys(sections)),
        topics=tuple(dict.fromkeys(topics)),
        years=years,
        proper_terms=_question_proper_terms(question),
        phrases=_question_phrases(tokens),
    )


def _concept_terms(terms: tuple[str, ...]) -> tuple[str, ...]:
    """Reduce the readable phrases of one concept to the words it expands to."""

    words: list[str] = []
    for term in terms:
        for word in _TOKEN_PATTERN.findall(term.casefold()):
            if word not in _STOP_WORDS and len(word) > 1:
                words.append(word)
    return tuple(dict.fromkeys(words))


def _profile_page(document_id: str, page_number: int, text: str) -> _PageProfile:
    """Reduce one stored page body to the signals the reranking stage reads."""

    lowered = text.casefold()
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    heading = " ".join(lines[:_HEADING_LINES]).casefold()
    counts = Counter(_stem(token) for token in _TOKEN_PATTERN.findall(lowered))
    return _PageProfile(
        document_id=document_id,
        page_number=page_number,
        text=text,
        counts=counts,
        statements=_detect_statements(heading),
        sections=_detect_sections(lines),
        topics=_detect_topics(lowered, lines),
        inherited_sections=frozenset(),
    )


def _detect_statements(heading: str) -> frozenset[str]:
    """Return the statement kinds the heading of one page names."""

    kinds = {kind for kind, pattern in _STATEMENT_PATTERNS if pattern.search(heading)}
    return frozenset(kinds)


def _detect_sections(lines: list[str]) -> frozenset[str]:
    """Return the report sections one page opens or continues."""

    sections: set[str] = set()
    for line in lines:
        match = _ITEM_HEADING_PATTERN.match(line)
        if match is not None:
            kind = _ITEM_SECTIONS.get(match.group(1).casefold())
            if kind is not None:
                sections.add(kind)
        if _NOTES_HEADING_PATTERN.match(line) is not None:
            sections.add("notes")
    if len(sections) >= 4:
        return frozenset()
    if not sections:
        head = " ".join(lines[:_HEADING_LINES]).casefold()
        if "risk factors" in head:
            sections.add("risk")
        elif "management's discussion" in head or "management’s discussion" in head:  # noqa: RUF001
            sections.add("mda")
    return frozenset(sections)


def _detect_topics(body: str, lines: list[str]) -> frozenset[str]:
    """Return the note topics one page discusses."""

    topics: set[str] = set()
    for line in lines:
        match = _NOTE_TITLE_PATTERN.match(line)
        if match is None:
            continue
        title = match.group(1).casefold()
        for keyword, topic in _NOTE_TITLE_TOPICS:
            if keyword in title:
                topics.add(topic)
                break
    hits: dict[str, int] = {}
    for topic, keywords in _TOPIC_KEYWORDS.items():
        found = sum(1 for keyword in keywords if keyword in body)
        if found >= _TOPIC_MIN_HITS:
            hits[topic] = found
    for topic, _ in sorted(hits.items(), key=lambda item: (-item[1], item[0]))[:_MAX_TOPICS]:
        topics.add(topic)
    return frozenset(topics)


def _rank_pages(profiles: list[_PageProfile], plan: _QuestionPlan, limit: int) -> list[_PageProfile]:
    """Order the stored pages by how well they cover the question and its places."""

    if not profiles or limit <= 0:
        return []
    scores = _score_pages(profiles, plan)
    scores = _apply_document_affinity(profiles, scores, plan)
    scores = _smooth_neighbours(profiles, scores)
    scores = _apply_prior(profiles, scores)
    ranked = sorted(
        zip(profiles, scores, strict=True),
        key=lambda item: (-item[1], item[0].document_id, item[0].page_number),
    )
    return _select_pages(ranked, plan, limit)


def _apply_prior(profiles: list[_PageProfile], scores: list[float]) -> list[float]:
    """Keep the retrieval order as a small tiebreak between equally relevant pages."""

    strongest = max(scores, default=0.0) or 1.0
    return [
        score + _PRIOR_WEIGHT * strongest / (60 + profile.rank) for profile, score in zip(profiles, scores, strict=True)
    ]


def _apply_document_affinity(
    profiles: list[_PageProfile],
    scores: list[float],
    plan: _QuestionPlan,
) -> list[float]:
    """Tell which stored document a question asks about, and favour its pages."""

    best: dict[str, float] = {}
    for profile, score in zip(profiles, scores, strict=True):
        best[profile.document_id] = max(best.get(profile.document_id, 0.0), score)
    strongest = max(best.values(), default=0.0) or 1.0
    named = {profile.document_id for profile in profiles if _names_document(profile.document_id, plan)}
    return [
        score
        + _AFFINITY_WEIGHT * best[profile.document_id] / strongest
        + (_DOCUMENT_WEIGHT if profile.document_id in named else 0.0)
        for profile, score in zip(profiles, scores, strict=True)
    ]


def _names_document(document_id: str, plan: _QuestionPlan) -> bool:
    """Tell whether the stored identity of one document names what the question asks."""

    identity = document_id.casefold()
    return any(len(term) > 2 and term in identity for term in plan.proper_terms)


def _score_pages(profiles: list[_PageProfile], plan: _QuestionPlan) -> list[float]:
    """Score every stored page against the question it should answer."""

    terms = tuple(_stem(term) for term in plan.expanded_terms)
    weights = _term_weights(profiles, terms)
    weight_total = sum(weights.values()) or 1.0
    phrase_total = sum(weight for _, weight in plan.phrases) or 1.0
    return [_page_score(profile, plan, weights, weight_total, phrase_total) for profile in profiles]


def _term_weights(profiles: list[_PageProfile], terms: tuple[str, ...]) -> dict[str, float]:
    """Weigh the question terms by how rare they are among the stored pages."""

    frequency: Counter[str] = Counter()
    for profile in profiles:
        frequency.update(set(terms) & profile.counts.keys())
    total = len(profiles)
    return {term: math.log((total + 1) / (frequency[term] + 0.5)) for term in terms if frequency.get(term, 0) > 0}


def _page_score(
    profile: _PageProfile,
    plan: _QuestionPlan,
    weights: dict[str, float],
    weight_total: float,
    phrase_total: float,
) -> float:
    """Score one stored page against the question it should answer."""

    matched = sum(weight for term, weight in weights.items() if term in profile.counts)
    score = _COVERAGE_WEIGHT * matched / weight_total
    body = profile.text.casefold()
    matched_phrase = sum(weight for pattern, weight in plan.phrases if pattern.search(body))
    score += _PHRASE_WEIGHT * matched_phrase / phrase_total
    score += _STATEMENT_WEIGHT * _kind_match(profile.statements, plan.statement_kinds)
    score += _SECTION_WEIGHT * _kind_match(profile.sections | profile.inherited_sections, plan.section_kinds)
    score += _TOPIC_WEIGHT * _kind_match(profile.topics, plan.topics)
    score += _YEAR_WEIGHT * _year_match(body, plan.years)
    if plan.proper_terms:
        named = sum(1 for term in plan.proper_terms if _stem(term) in profile.counts)
        score += _PROPER_WEIGHT * named / len(plan.proper_terms)
    return score


def _kind_match(available: frozenset[str], wanted: tuple[str, ...]) -> float:
    """Score how many of the wanted page kinds one page offers."""

    if not wanted:
        return 0.0
    return sum(1 for kind in wanted if kind in available) / len(wanted)


def _year_match(body: str, years: tuple[str, ...]) -> float:
    """Score how many of the fiscal years one page mentions."""

    if not years:
        return 0.0
    return sum(1 for year in years if year in body) / len(years)


def _smooth_neighbours(profiles: list[_PageProfile], scores: list[float]) -> list[float]:
    """Let a strong page lift the discussion that continues on its neighbours."""

    by_document: dict[str, list[tuple[int, int]]] = {}
    for index, profile in enumerate(profiles):
        by_document.setdefault(profile.document_id, []).append((profile.page_number, index))
    smoothed = list(scores)
    for positions in by_document.values():
        positions.sort()
        for order, (_, index) in enumerate(positions):
            best = 0.0
            if order:
                best = max(best, scores[positions[order - 1][1]])
            if order + 1 < len(positions):
                best = max(best, scores[positions[order + 1][1]])
            smoothed[index] += _ADJACENT_WEIGHT * best
    return smoothed


def _select_pages(
    ranked: list[tuple[_PageProfile, float]],
    plan: _QuestionPlan,
    limit: int,
) -> list[_PageProfile]:
    """Pick the pages to return, covering every place the question asks about."""

    named = plan.named_kinds()
    groups = _kind_groups(ranked, named)
    quotas = _kind_quotas(groups, limit)
    selected: list[_PageProfile] = []
    chosen: set[tuple[str, int]] = set()
    for key, items in groups.items():
        taken = 0
        for profile, _ in items:
            if taken >= quotas[key] or len(selected) >= limit:
                break
            identity = (profile.document_id, profile.page_number)
            if identity in chosen:
                continue
            selected.append(profile)
            chosen.add(identity)
            taken += 1
    for profile, _ in ranked:
        if len(selected) >= limit:
            break
        identity = (profile.document_id, profile.page_number)
        if identity in chosen:
            continue
        selected.append(profile)
        chosen.add(identity)
    return selected[:limit]


def _kind_groups(
    ranked: list[tuple[_PageProfile, float]],
    named: tuple[tuple[str, str], ...],
) -> dict[tuple[str, str], list[tuple[_PageProfile, float]]]:
    """Group the ranked pages by the places the question asks about."""

    groups: dict[tuple[str, str], list[tuple[_PageProfile, float]]] = {}
    for profile, score in ranked:
        for key in named:
            kind, name = key
            if _has_kind(profile, kind, name):
                groups.setdefault(key, []).append((profile, score))
    return groups


def _kind_quotas(
    groups: dict[tuple[str, str], list[tuple[_PageProfile, float]]],
    limit: int,
) -> dict[tuple[str, str], int]:
    """Share the returned pages between the places the question asks about."""

    if not groups:
        return {}
    strength = {key: sum(score for _, score in items[:3]) for key, items in groups.items()}
    total = sum(strength.values()) or 1.0
    quotas = {key: max(_MIN_QUOTA, min(_MAX_QUOTA, round(limit * value / total))) for key, value in strength.items()}
    return quotas


def _has_kind(profile: _PageProfile, kind: str, name: str) -> bool:
    """Tell whether one page belongs to the place the question asks for."""

    if kind == "statement":
        return name in profile.statements
    if kind == "section":
        return name in profile.sections or name in profile.inherited_sections
    return name in profile.topics


def _question_years(tokens: list[str]) -> tuple[str, ...]:
    """Return the fiscal years one question names, written as plain years."""

    years: list[str] = []
    for token in tokens:
        match = _YEAR_PATTERN.match(token)
        if match:
            years.append(match.group(1))
            continue
        short = _SHORT_YEAR_PATTERN.match(token)
        if short:
            years.append(f"20{short.group(1)}")
    return tuple(dict.fromkeys(years))


def _question_proper_terms(question: str) -> tuple[str, ...]:
    """Return the capitalised words that may name the reported company."""

    terms: list[str] = []
    for index, match in enumerate(_PROPER_PATTERN.finditer(question)):
        word = match.group(1)
        if index == 0 or word.casefold() in _SENTENCE_WORDS:
            continue
        lowered = word.casefold()
        if len(word) < 2 or lowered in _STOP_WORDS or lowered in _FINANCE_ACRONYMS:
            continue
        if _YEAR_PATTERN.match(lowered) or _SHORT_YEAR_PATTERN.match(lowered):
            continue
        terms.append(lowered)
    return tuple(dict.fromkeys(terms))


def _question_phrases(tokens: list[str]) -> tuple[tuple[re.Pattern[str], float], ...]:
    """Return the adjacent question terms that stay together on one page."""

    content = [token for token in tokens if token not in _STOP_WORDS and len(token) > 1]
    phrases: list[tuple[re.Pattern[str], float]] = []
    for left, right in itertools.pairwise(content):
        pattern = re.compile(rf"\b{re.escape(left)}(?:\s+[a-z0-9]+){{0,2}}\s+{re.escape(right)}\b")
        phrases.append((pattern, len(left) + len(right)))
    return tuple(phrases)


def _stem(token: str) -> str:
    """Reduce one token to the stem that lets related word forms meet."""

    if len(token) > 4 and token.endswith("ies"):
        token = f"{token[:-3]}y"
    elif (len(token) > 4 and token.endswith("sses")) or (
        len(token) > 4 and token.endswith("es") and not token.endswith(("ses", "ces", "xes", "zes", "ches", "shes"))
    ):
        token = token[:-2]
    elif len(token) > 3 and token.endswith("s") and not token.endswith(("ss", "us", "is")):
        token = token[:-1]
    if len(token) > 6 and token.endswith("ity"):
        token = token[:-3]
    return _WORD_SYNONYMS.get(token, token)


def _contains_phrase(lowered: str, phrase: str) -> bool:
    """Tell whether one phrase appears in a question, allowing a plural ending."""

    pattern = rf"(?<![a-z0-9]){re.escape(phrase)}(?:s)?(?![a-z0-9])"
    return re.search(pattern, lowered) is not None


def _page_identity(entry: MemoryEntryVersion) -> tuple[str, int] | None:
    """Return the document and physical page one stored entry was captured from."""

    for reference in entry.sources:
        if reference.source_type != PDF_PAGE_SOURCE_NAME:
            continue
        page_number = _page_number(reference.source_id)
        document_id = entry.text.split("\n", 1)[0].strip()
        if page_number is None or not document_id:
            return None
        return (document_id, page_number)
    return None


def _page_number(source_id: str) -> int | None:
    prefix, separator, tail = source_id.rpartition(":")
    if not separator or not prefix.startswith(f"{PDF_PAGE_SOURCE_NAME}:"):
        return None
    try:
        return int(tail)
    except ValueError:
        return None


def _fit_page_text(document_id: str, text: str) -> str:
    """Keep one page body inside the byte bound of one stored Memory entry."""

    budget = MAX_EVIDENCE_TEXT_BYTES - len(f"{document_id}\n".encode())
    encoded = text.encode("utf-8")
    if len(encoded) <= budget:
        return text
    clipped = encoded[:budget].decode("utf-8", errors="ignore")
    boundary = clipped.rfind("\n")
    return clipped[:boundary] if boundary > 0 else clipped


def _validate_document_id(document_id: str) -> None:
    if not isinstance(document_id, str) or not document_id.strip():
        raise InvalidSourceReferenceError("document_id", "must be a non-empty string")
    if document_id != document_id.strip():
        raise InvalidSourceReferenceError(
            "document_id",
            "must not contain leading or trailing whitespace",
        )
    if "\n" in document_id or "\r" in document_id:
        raise InvalidSourceReferenceError("document_id", "must not contain line breaks")


def _validate_document_sha256(document_sha256: str) -> None:
    if not isinstance(document_sha256, str) or len(document_sha256) != 64:
        raise InvalidSourceReferenceError("document_sha256", "must be a 64 character digest")
    if document_sha256.strip("0123456789abcdef"):
        raise InvalidSourceReferenceError("document_sha256", "must be lowercase hexadecimal")


def _validate_page_number(page_number: int) -> None:
    if not isinstance(page_number, int) or isinstance(page_number, bool) or page_number < 1:
        raise InvalidSourceReferenceError("page_number", "must be a positive integer")


async def search_pdf_pages(
    memory: PageSearchScope,
    question: str,
    limit: int = 10,
) -> tuple[MemoryCitation, ...]:
    """Return exact Memory citations for the pages most likely to answer one question.

    Every citation points at one stored page, so the caller can read the page body
    back from Memory and quote it without trusting any offline answer.
    """

    if limit <= 0 or not question.strip():
        return ()
    plan = _plan_question(question)
    profiles = await _collect_pages(memory, plan, limit)
    if not profiles:
        return ()
    citations: list[MemoryCitation] = []
    seen: set[tuple[str, int]] = set()
    for profile in _rank_pages(profiles, plan, limit):
        citation = profile.citation
        if citation is None:
            continue
        record = await memory.get(GetMemoryEntryRequest(citation=citation))
        identity = _page_identity(record.entry)
        if identity is None or identity in seen:
            continue
        seen.add(identity)
        citations.append(record.citation)
        if len(citations) >= limit:
            break
    return tuple(citations)


async def _collect_pages(
    memory: PageSearchScope,
    plan: _QuestionPlan,
    limit: int,
) -> list[_PageProfile]:
    """Read the stored pages one scope holds, newest revision first."""

    lister = getattr(memory, "list", None)
    if callable(lister):
        page = await lister()
        profiles = []
        for record in page.entries:
            profile = _profile_from_record(record)
            if profile is not None:
                profiles.append(profile)
        if profiles:
            return _apply_outline(profiles)
    return await _collect_by_search(memory, plan, limit)


def _profile_from_record(record: MemoryEntryRecord) -> _PageProfile | None:
    """Turn one stored Memory entry into the profile the reranker reads."""

    identity = _page_identity(record.entry)
    if identity is None:
        return None
    document_id, page_number = identity
    body = record.entry.text.split("\n", 1)
    text = body[1] if len(body) > 1 else ""
    profile = _profile_page(document_id, page_number, text)
    return replace(
        profile,
        citation=MemoryCitation(
            memory_ref=record.memory_ref,
            entry_id=record.entry.entry_id,
            entry_version_id=record.entry.entry_version_id,
        ),
    )


def _apply_outline(profiles: list[_PageProfile]) -> list[_PageProfile]:
    """Carry the section of a report forward to the pages that continue it."""

    by_document: dict[str, list[_PageProfile]] = {}
    for profile in profiles:
        by_document.setdefault(profile.document_id, []).append(profile)
    outlined: list[_PageProfile] = []
    for document_profiles in by_document.values():
        document_profiles.sort(key=lambda profile: profile.page_number)
        current: frozenset[str] = frozenset()
        for profile in document_profiles:
            outlined.append(replace(profile, inherited_sections=current))
            if profile.sections:
                current = profile.sections
    return outlined


async def _collect_by_search(
    memory: PageSearchScope,
    plan: _QuestionPlan,
    limit: int,
) -> list[_PageProfile]:
    """Read the stored pages one scope holds through the retrieval search only."""

    pool = min(max(limit * _CANDIDATE_POOL_MULTIPLIER, _MIN_CANDIDATE_POOL), _MAX_CANDIDATE_POOL)
    queries: list[tuple[str, int]] = [(plan.raw, pool)]
    focused = " ".join(dict.fromkeys((*plan.core_terms, *plan.expanded_terms)))
    if focused:
        queries.append((focused, pool))
    for group in plan.concept_groups:
        query = " ".join(group)
        if query:
            queries.append((query, _CONCEPT_POOL))
    pages = await asyncio.gather(
        *(memory.search(SearchMemoryRequest(query=query, mode="fts", limit=size)) for query, size in queries)
    )
    collected: dict[tuple[str, str], tuple[int, MemoryCitation]] = {}
    for page in pages:
        for rank, hit in enumerate(page.hits):
            key = (hit.entry_id, hit.entry_version_id)
            existing = collected.get(key)
            citation = MemoryCitation(
                memory_ref=hit.memory_ref,
                entry_id=hit.entry_id,
                entry_version_id=hit.entry_version_id,
            )
            if existing is None or rank < existing[0]:
                collected[key] = (rank, citation)
    profiles: list[_PageProfile] = []
    for _, (rank, citation) in collected.items():
        record = await memory.get(GetMemoryEntryRequest(citation=citation))
        profile = _profile_from_record(record)
        if profile is None:
            continue
        profiles.append(replace(profile, rank=rank))
    return _apply_outline(profiles)
