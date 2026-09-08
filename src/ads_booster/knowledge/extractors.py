from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum, unique
from hashlib import sha256
from html.parser import HTMLParser
from io import BytesIO
from typing import Final, final, override

from pypdf import PdfReader
from pypdf.errors import PdfReadError, PdfStreamError

from ads_booster.knowledge.contract_types import SourceCompleteness, SourceExtractionStatus
from ads_booster.knowledge.source_contracts import SegmentLocator

EXTRACTOR_VERSION: Final = "text-html-pdf-v1"
MAX_INPUT_BYTES: Final = 50 * 1024 * 1024
MAX_EXTRACTED_TEXT_BYTES: Final = 2 * 1024 * 1024
MAX_PDF_PAGES: Final = 1_000
_TEXT_MIME_TYPES: Final = frozenset({"text/plain", "text/markdown", "text/x-markdown"})
_HIDDEN_HTML_TAGS: Final = frozenset({"head", "noscript", "script", "style", "template"})
_BLOCK_HTML_TAGS: Final = frozenset(
    {
        "address",
        "article",
        "aside",
        "blockquote",
        "br",
        "dd",
        "div",
        "dl",
        "dt",
        "fieldset",
        "figcaption",
        "figure",
        "footer",
        "form",
        "h1",
        "h2",
        "h3",
        "h4",
        "h5",
        "h6",
        "header",
        "hr",
        "li",
        "main",
        "nav",
        "ol",
        "p",
        "pre",
        "section",
        "table",
        "td",
        "th",
        "tr",
        "ul",
    }
)


@unique
class ExtractionLocatorBasis(StrEnum):
    TEXT_LINES = "text_lines"
    HTML_VISIBLE_BLOCKS = "html_visible_blocks"
    PDF_PAGES = "pdf_pages"
    NONE = "none"


@unique
class ExtractionErrorCode(StrEnum):
    INPUT_TOO_LARGE = "input_too_large"
    INVALID_UTF8_REPLACED = "invalid_utf8_replaced"
    TEXT_LIMIT_REACHED = "text_limit_reached"
    UNSUPPORTED_MIME = "unsupported_mime"
    INVALID_PDF = "invalid_pdf"
    PDF_ENCRYPTED = "pdf_encrypted"
    PDF_NO_EXTRACTABLE_TEXT = "pdf_no_extractable_text"
    PDF_PAGE_WITHOUT_TEXT = "pdf_page_without_text"
    PDF_PAGE_LIMIT_REACHED = "pdf_page_limit_reached"


@dataclass(frozen=True, slots=True)
class ExtractedSegment:
    content: str
    content_sha256: str
    quote_start: int
    quote_end: int
    locator: SegmentLocator


@dataclass(frozen=True, slots=True)
class ExtractionResult:
    extractor_version: str
    extraction_status: SourceExtractionStatus
    completeness: SourceCompleteness
    locator_basis: ExtractionLocatorBasis
    text: str
    text_sha256: str
    segments: tuple[ExtractedSegment, ...]
    error_code: ExtractionErrorCode | None


@dataclass(frozen=True, slots=True)
class _DraftSegment:
    text: str
    locator: SegmentLocator


@dataclass(frozen=True, slots=True)
class _ExtractionDraft:
    text: str
    segments: tuple[_DraftSegment, ...]
    locator_basis: ExtractionLocatorBasis
    extraction_status: SourceExtractionStatus
    completeness: SourceCompleteness
    error_code: ExtractionErrorCode | None


@final
class _VisibleTextParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._blocks: list[str] = []
        self._current: list[str] = []
        self._hidden_depth: int = 0

    @property
    def blocks(self) -> tuple[str, ...]:
        self._flush()
        return tuple(self._blocks)

    @override
    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        del attrs
        normalized_tag = tag.lower()
        if normalized_tag in _HIDDEN_HTML_TAGS:
            self._hidden_depth += 1
            return
        if self._hidden_depth == 0 and normalized_tag in _BLOCK_HTML_TAGS:
            self._flush()

    @override
    def handle_endtag(self, tag: str) -> None:
        normalized_tag = tag.lower()
        if normalized_tag in _HIDDEN_HTML_TAGS:
            if self._hidden_depth > 0:
                self._hidden_depth -= 1
            return
        if self._hidden_depth == 0 and normalized_tag in _BLOCK_HTML_TAGS:
            self._flush()

    @override
    def handle_data(self, data: str) -> None:
        if self._hidden_depth == 0:
            self._current.append(data)

    def _flush(self) -> None:
        text = " ".join("".join(self._current).split())
        self._current.clear()
        if text:
            self._blocks.append(text)


def extract(data: bytes, *, mime_type: str) -> ExtractionResult:
    """Extract bounded text from trusted in-memory bytes without source identity or I/O."""
    if len(data) > MAX_INPUT_BYTES:
        return _result(
            _ExtractionDraft(
                text="",
                segments=(),
                locator_basis=ExtractionLocatorBasis.NONE,
                extraction_status=SourceExtractionStatus.FAILED,
                completeness=SourceCompleteness.UNKNOWN,
                error_code=ExtractionErrorCode.INPUT_TOO_LARGE,
            )
        )

    media_type = mime_type.partition(";")[0].strip().lower()
    match media_type:
        case "application/pdf":
            return _result(_extract_pdf(data))
        case "text/html" | "application/xhtml+xml":
            return _result(_extract_html(data))
        case value if value in _TEXT_MIME_TYPES:
            return _result(_extract_text(data))
        case _:
            return _result(
                _ExtractionDraft(
                    text="",
                    segments=(),
                    locator_basis=ExtractionLocatorBasis.NONE,
                    extraction_status=SourceExtractionStatus.NEEDS_EXTRACTOR,
                    completeness=SourceCompleteness.UNKNOWN,
                    error_code=ExtractionErrorCode.UNSUPPORTED_MIME,
                )
            )


def _extract_text(data: bytes) -> _ExtractionDraft:
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        text = data.decode("utf-8", errors="replace")
        extraction_status = SourceExtractionStatus.PARTIAL
        completeness = SourceCompleteness.PARTIAL
        error_code: ExtractionErrorCode | None = ExtractionErrorCode.INVALID_UTF8_REPLACED
    else:
        extraction_status = SourceExtractionStatus.COMPLETE
        completeness = SourceCompleteness.FULL
        error_code = None
    return _ExtractionDraft(
        text=text,
        segments=_line_segments(text),
        locator_basis=ExtractionLocatorBasis.TEXT_LINES,
        extraction_status=extraction_status,
        completeness=completeness,
        error_code=error_code,
    )


def _extract_html(data: bytes) -> _ExtractionDraft:
    text_draft = _extract_text(data)
    parser = _VisibleTextParser()
    parser.feed(text_draft.text)
    parser.close()
    blocks = parser.blocks
    return _draft_from_segments(
        tuple(
            _DraftSegment(text=block, locator=SegmentLocator(paragraph=index))
            for index, block in enumerate(blocks, start=1)
        ),
        locator_basis=ExtractionLocatorBasis.HTML_VISIBLE_BLOCKS,
        extraction_status=text_draft.extraction_status,
        completeness=text_draft.completeness,
        error_code=text_draft.error_code,
    )


def _extract_pdf(data: bytes) -> _ExtractionDraft:
    with BytesIO(data) as input_stream:
        try:
            reader = PdfReader(input_stream, strict=False)
            if reader.is_encrypted:
                return _failed_pdf(
                    ExtractionErrorCode.PDF_ENCRYPTED,
                    SourceExtractionStatus.NEEDS_EXTRACTOR,
                )
            page_count = len(reader.pages)
        except PdfReadError, PdfStreamError:
            return _failed_pdf(ExtractionErrorCode.INVALID_PDF, SourceExtractionStatus.FAILED)

        pages_to_extract = min(page_count, MAX_PDF_PAGES)
        page_texts: list[str] = []
        page_segments: list[_DraftSegment] = []
        has_empty_page = False
        try:
            for page_index in range(pages_to_extract):
                page = reader.pages[page_index]
                if "/Contents" not in page:
                    has_empty_page = True
                    continue
                page_text = page.extract_text(extraction_mode="layout").strip()
                if page_text:
                    page_texts.append(page_text)
                    page_segments.append(
                        _DraftSegment(text=page_text, locator=SegmentLocator(page=page_index + 1))
                    )
                else:
                    has_empty_page = True
        except PdfReadError, PdfStreamError:
            return _failed_pdf(ExtractionErrorCode.INVALID_PDF, SourceExtractionStatus.FAILED)

        if not page_texts:
            return _failed_pdf(
                ExtractionErrorCode.PDF_NO_EXTRACTABLE_TEXT,
                SourceExtractionStatus.NEEDS_EXTRACTOR,
            )
        if page_count > MAX_PDF_PAGES:
            extraction_status = SourceExtractionStatus.PARTIAL
            completeness = SourceCompleteness.PARTIAL
            error_code: ExtractionErrorCode | None = ExtractionErrorCode.PDF_PAGE_LIMIT_REACHED
        elif has_empty_page:
            extraction_status = SourceExtractionStatus.PARTIAL
            completeness = SourceCompleteness.PARTIAL
            error_code = ExtractionErrorCode.PDF_PAGE_WITHOUT_TEXT
        else:
            extraction_status = SourceExtractionStatus.COMPLETE
            completeness = SourceCompleteness.FULL
            error_code = None
        return _draft_from_segments(
            tuple(page_segments),
            locator_basis=ExtractionLocatorBasis.PDF_PAGES,
            extraction_status=extraction_status,
            completeness=completeness,
            error_code=error_code,
        )


def _failed_pdf(
    error_code: ExtractionErrorCode,
    extraction_status: SourceExtractionStatus,
) -> _ExtractionDraft:
    return _ExtractionDraft(
        text="",
        segments=(),
        locator_basis=ExtractionLocatorBasis.PDF_PAGES,
        extraction_status=extraction_status,
        completeness=SourceCompleteness.UNKNOWN,
        error_code=error_code,
    )


def _line_segments(text: str) -> tuple[_DraftSegment, ...]:
    if not text:
        return ()
    return (
        _DraftSegment(
            text=text,
            locator=SegmentLocator(line_start=1, line_end=text.count("\n") + 1),
        ),
    )


def _draft_from_segments(
    segments: tuple[_DraftSegment, ...],
    *,
    locator_basis: ExtractionLocatorBasis,
    extraction_status: SourceExtractionStatus,
    completeness: SourceCompleteness,
    error_code: ExtractionErrorCode | None,
) -> _ExtractionDraft:
    text = "\n\n".join(segment.text for segment in segments)
    return _ExtractionDraft(
        text=text,
        segments=segments,
        locator_basis=locator_basis,
        extraction_status=extraction_status,
        completeness=completeness,
        error_code=error_code,
    )


def _result(draft: _ExtractionDraft) -> ExtractionResult:
    text = _bounded_text(draft.text)
    was_truncated = text != draft.text
    if was_truncated:
        extraction_status = SourceExtractionStatus.PARTIAL
        completeness = SourceCompleteness.PARTIAL
        error_code: ExtractionErrorCode | None = ExtractionErrorCode.TEXT_LIMIT_REACHED
    else:
        extraction_status = draft.extraction_status
        completeness = draft.completeness
        error_code = draft.error_code
    segments = _result_segments(draft.segments, text)
    return ExtractionResult(
        extractor_version=EXTRACTOR_VERSION,
        extraction_status=extraction_status,
        completeness=completeness,
        locator_basis=draft.locator_basis,
        text=text,
        text_sha256=_digest(text),
        segments=segments,
        error_code=error_code,
    )


def _bounded_text(text: str) -> str:
    encoded = text.encode()
    if len(encoded) <= MAX_EXTRACTED_TEXT_BYTES:
        return text
    return encoded[:MAX_EXTRACTED_TEXT_BYTES].decode("utf-8", errors="ignore")


def _result_segments(
    draft_segments: tuple[_DraftSegment, ...],
    text: str,
) -> tuple[ExtractedSegment, ...]:
    result: list[ExtractedSegment] = []
    cursor = 0
    for index, draft_segment in enumerate(draft_segments):
        if index > 0:
            cursor += 2
        quote_end = cursor + len(draft_segment.text)
        if cursor < len(text):
            content = text[cursor : min(quote_end, len(text))]
            result.append(
                ExtractedSegment(
                    content=content,
                    content_sha256=_digest(content),
                    quote_start=cursor,
                    quote_end=cursor + len(content),
                    locator=draft_segment.locator,
                )
            )
        cursor = quote_end
    return tuple(result)


def _digest(text: str) -> str:
    return sha256(text.encode()).hexdigest()
