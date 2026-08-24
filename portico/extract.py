"""Text extraction layer: attachment in, text with a page map out.

One interface for every downstream tool (classifier, citation verifier,
linter). Provenance and confidence travel with the text:

- PDF: native text per page via pypdf (extra: pdf). Pages with no native
  text are reported as warnings; when OCR is requested/available the whole
  scanned document is OCR'd page by page and every OCR page carries a mean
  word confidence (0-100).
- OCR: requires the Tesseract binary + poppler's pdftoppm on the system, and
  the pytesseract/Pillow packages (extra: ocr). Missing pieces raise
  ExtractionError naming exactly what to install — low-confidence text is
  flagged, garbage is never passed downstream silently.
- DOCX: python-docx paragraphs + table text (extra: docx). Word documents
  have no fixed pages, so the result is one logical page and a warning says
  pin-citing to page numbers is not possible from this format.
- TXT: stdlib.
"""
from __future__ import annotations

import shutil
import subprocess
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

LOW_CONFIDENCE = 60.0


class ExtractionError(RuntimeError):
    pass


@dataclass
class Page:
    number: int
    text: str
    method: str                      # native | ocr | docx | text
    confidence: float | None = None  # OCR mean word confidence, None otherwise


@dataclass
class Extraction:
    pages: list[Page]
    method: str
    warnings: list[str] = field(default_factory=list)

    @property
    def full_text(self) -> str:
        return "\n".join(p.text for p in self.pages)

    def page_for_offset(self, offset: int) -> int | None:
        """Map a character offset in full_text back to a page number."""
        pos = 0
        for p in self.pages:
            end = pos + len(p.text)
            if offset <= end:
                return p.number
            pos = end + 1  # the joining newline
        return self.pages[-1].number if self.pages else None


def _require(module: str, extra: str):
    try:
        return __import__(module)
    except ImportError:
        raise ExtractionError(
            f"extracting this file type needs the '{extra}' extra:"
            f" pip install 'portico-legal[{extra}]'"
        ) from None


def _extract_pdf(path: Path, *, ocr: str) -> Extraction:
    pypdf = _require("pypdf", "pdf")
    reader = pypdf.PdfReader(str(path))
    pages: list[Page] = []
    warnings: list[str] = []
    empty = 0
    for i, page in enumerate(reader.pages, start=1):
        text = (page.extract_text() or "").strip()
        if not text:
            empty += 1
            warnings.append(f"page {i}: no extractable text (likely scanned)")
        pages.append(Page(number=i, text=text, method="native"))

    mostly_empty = empty > len(pages) / 2 if pages else False
    if ocr == "always" or (ocr == "auto" and mostly_empty):
        try:
            return _ocr_pdf(path)
        except ExtractionError:
            if ocr == "always":
                raise
            warnings.append(
                f"{empty}/{len(pages)} pages have no native text and OCR is"
                " unavailable — install the 'ocr' extra plus tesseract and"
                " poppler-utils for scanned documents"
            )
    return Extraction(pages=pages, method="native", warnings=warnings)


def _ocr_pdf(path: Path) -> Extraction:
    pytesseract = _require("pytesseract", "ocr")
    _require("PIL", "ocr")
    from PIL import Image

    for binary, package in (("tesseract", "tesseract-ocr"), ("pdftoppm", "poppler-utils")):
        if shutil.which(binary) is None:
            raise ExtractionError(
                f"OCR needs the '{binary}' system binary (install {package})"
            )

    pages: list[Page] = []
    warnings: list[str] = []
    with tempfile.TemporaryDirectory() as tmp:
        subprocess.run(
            ["pdftoppm", "-r", "300", "-png", str(path), f"{tmp}/page"],
            check=True, capture_output=True,
        )
        images = sorted(Path(tmp).glob("page*.png"))
        if not images:
            raise ExtractionError("pdftoppm produced no page images")
        for i, img_path in enumerate(images, start=1):
            with Image.open(img_path) as img:
                data = pytesseract.image_to_data(
                    img, output_type=pytesseract.Output.DICT
                )
            words, confs = [], []
            for word, conf in zip(data["text"], data["conf"]):
                if word.strip() and float(conf) >= 0:
                    words.append(word)
                    confs.append(float(conf))
            confidence = sum(confs) / len(confs) if confs else 0.0
            if confidence < LOW_CONFIDENCE:
                warnings.append(
                    f"page {i}: OCR confidence {confidence:.0f} is below"
                    f" {LOW_CONFIDENCE:.0f} — text may be unreliable"
                )
            pages.append(
                Page(number=i, text=" ".join(words), method="ocr", confidence=confidence)
            )
    return Extraction(pages=pages, method="ocr", warnings=warnings)


def _extract_docx(path: Path) -> Extraction:
    docx = _require("docx", "docx")
    document = docx.Document(str(path))
    parts = [p.text for p in document.paragraphs if p.text.strip()]
    for table in document.tables:
        for row in table.rows:
            cells = [c.text.strip() for c in row.cells if c.text.strip()]
            if cells:
                parts.append(" | ".join(cells))
    return Extraction(
        pages=[Page(number=1, text="\n".join(parts), method="docx")],
        method="docx",
        warnings=["DOCX has no fixed pages; page-level pin cites are not"
                  " derivable from this format"],
    )


def _extract_txt(path: Path) -> Extraction:
    text = path.read_bytes().decode("utf-8", "replace")
    return Extraction(pages=[Page(number=1, text=text, method="text")], method="text")


_DISPATCH = {
    ".pdf": _extract_pdf,
    ".docx": _extract_docx,
    ".txt": _extract_txt,
    ".md": _extract_txt,
}


def extract_path(path: str | Path, *, ocr: str = "auto") -> Extraction:
    """Extract text from a file. ocr: "auto" | "always" | "never"."""
    if ocr not in ("auto", "always", "never"):
        raise ValueError(f"ocr must be auto/always/never, not {ocr!r}")
    path = Path(path)
    if not path.exists():
        raise ExtractionError(f"no such file: {path}")
    handler = _DISPATCH.get(path.suffix.lower())
    if handler is None:
        raise ExtractionError(
            f"unsupported file type '{path.suffix}' (supported:"
            f" {', '.join(sorted(_DISPATCH))})"
        )
    if handler is _extract_pdf:
        if ocr == "never":
            return _extract_pdf_no_ocr(path)
        return handler(path, ocr=ocr)
    return handler(path)


def _extract_pdf_no_ocr(path: Path) -> Extraction:
    return _extract_pdf(path, ocr="off")
