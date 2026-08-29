"""Redaction: the words are removed, not covered — and the page stays searchable.

A black rectangle drawn over text is not a redaction. The glyphs are still in
the file and any extractor lifts them straight back out; that mistake has put
real firms in real newspapers. Docketry rasterises every page that carries a
redaction, burns the boxes into the pixels, and then runs OCR over the burned
image to lay a fresh, invisible text layer back down. What sat under the box is
gone from the file. What did not is still selectable and searchable.

The trade is stated rather than hidden: a redacted page stops being vector text
and becomes an image plus an OCR text layer, so its text is only as good as the
OCR — which is why every rasterised page carries its mean OCR confidence, and
why pages with no redaction are copied through untouched and keep their
original text. Redaction is per-page, never document-wide.

Highlights are the opposite kind of mark: non-destructive. On a page that is
being rasterised anyway they are burned in with the redactions; on a page that
is otherwise untouched they are composited as a transparent overlay so the
page keeps its live text.

Nothing here decides WHAT to redact. A human names the terms or draws the
boxes. This module removes them, and then proves they are gone — verify()
re-reads the finished file and reports any term that survived.
"""
from __future__ import annotations

import io
import re
import shutil
import subprocess
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

MARKER = "[REDACTED]"
DEFAULT_DPI = 300
LOW_CONFIDENCE = 60.0

# Verification ignores tokens this short: a two-letter fragment surviving
# somewhere else in a 300-page production is noise, not a leak.
MIN_VERIFY_TOKEN = 3


class RedactionError(RuntimeError):
    pass


@dataclass
class Box:
    """A mark on a page, in fractions of the page (0..1), top-left origin.

    Page coordinates are normalised rather than absolute so a box drawn on a
    screen preview lands in the same place on a 300 dpi render.
    """
    page: int                     # 1-based
    x0: float
    y0: float
    x1: float
    y1: float
    kind: str = "redact"          # redact | highlight
    note: str = ""

    @property
    def is_redaction(self) -> bool:
        return self.kind == "redact"


@dataclass
class RedactionResult:
    out_path: Path
    pages_rasterised: list[int] = field(default_factory=list)
    pages_untouched: list[int] = field(default_factory=list)
    words_removed: list[str] = field(default_factory=list)
    page_confidence: dict[int, float] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)
    survivors: list[str] = field(default_factory=list)
    also_appears: list[str] = field(default_factory=list)
    unverifiable: list[str] = field(default_factory=list)

    @property
    def verified(self) -> bool:
        return not self.survivors


def _require(module: str, extra: str):
    try:
        return __import__(module)
    except ImportError:
        raise RedactionError(
            f"redaction needs the '{extra}' extra: pip install 'docketry[{extra}]'"
        ) from None


def _require_binaries() -> None:
    for binary, package in (("tesseract", "tesseract-ocr"), ("pdftoppm", "poppler-utils")):
        if shutil.which(binary) is None:
            raise RedactionError(
                f"redaction needs the '{binary}' system binary (install {package})"
            )


def normalise(box: Box) -> Box:
    """Order and clamp a box, refusing the two shapes that cause silent harm.

    A reversed box (x1 < x0) raises straight out of the drawing library. A
    zero-area box draws nothing at all — but on the redaction path it still
    costs the page its entire text layer, so the file looks redacted, reads as
    redacted, and hides nothing. Both are caller bugs and both fail loudly
    here rather than producing a plausible-looking document.
    """
    x0, x1 = sorted((float(box.x0), float(box.x1)))
    y0, y1 = sorted((float(box.y0), float(box.y1)))
    x0, x1 = max(0.0, min(1.0, x0)), max(0.0, min(1.0, x1))
    y0, y1 = max(0.0, min(1.0, y0)), max(0.0, min(1.0, y1))
    if box.is_redaction and (x1 - x0 <= 0 or y1 - y0 <= 0):
        raise RedactionError(
            f"page {box.page}: a redaction box with no area would strip the"
            " page's text layer and hide nothing"
        )
    if box.page < 1:
        raise RedactionError(f"page numbers are 1-based, got {box.page}")
    return Box(page=box.page, x0=x0, y0=y0, x1=x1, y1=y1, kind=box.kind, note=box.note)


def _intersects(word_box: tuple[float, float, float, float], box: Box) -> bool:
    wx0, wy0, wx1, wy1 = word_box
    return not (wx1 <= box.x0 or wx0 >= box.x1 or wy1 <= box.y0 or wy0 >= box.y1)


def _render_page(pdf: Path, page: int, dpi: int, tmp: str):
    """One page of the PDF as a PIL image, via poppler."""
    from PIL import Image
    prefix = f"{tmp}/p{page}"
    subprocess.run(
        ["pdftoppm", "-r", str(dpi), "-png", "-f", str(page), "-l", str(page),
         str(pdf), prefix],
        check=True, capture_output=True,
    )
    images = sorted(Path(tmp).glob(f"p{page}-*.png")) or sorted(Path(tmp).glob(f"p{page}.png"))
    if not images:
        raise RedactionError(f"pdftoppm produced no image for page {page}")
    img = Image.open(images[0])
    img.load()
    return img


def _word_boxes(img, pytesseract) -> list[tuple[str, tuple[float, float, float, float], float]]:
    """OCR words with normalised boxes and confidence."""
    data = pytesseract.image_to_data(img, output_type=pytesseract.Output.DICT)
    W, H = img.size
    out = []
    for text, left, top, width, height, conf in zip(
        data["text"], data["left"], data["top"], data["width"], data["height"], data["conf"]
    ):
        if not text.strip() or float(conf) < 0:
            continue
        out.append((
            text,
            (left / W, top / H, (left + width) / W, (top + height) / H),
            float(conf),
        ))
    return out


def find_terms(pdf: str | Path, terms: list[str], *, dpi: int = DEFAULT_DPI) -> list[Box]:
    """Every place a term appears, as redaction boxes — a PREVIEW, writes nothing.

    Matching is case-insensitive and substring-based on the OCR word, so
    'Doe' finds 'Doe,' and '123-45-6789' finds it inside a longer run. Terms
    are found by looking at the page as rendered, which means this works on
    scans as well as native text.
    """
    pytesseract = _require("pytesseract", "ocr")
    _require("PIL", "ocr")
    _require_binaries()
    pdf = Path(pdf)
    if not terms:
        raise RedactionError("no terms given; refusing to scan for nothing")
    needles = [t.strip().lower() for t in terms if t.strip()]
    if not needles:
        raise RedactionError("no terms given; refusing to scan for nothing")

    pypdf = _require("pypdf", "pdf")
    n_pages = len(pypdf.PdfReader(str(pdf)).pages)
    found: list[Box] = []
    with tempfile.TemporaryDirectory() as tmp:
        for page in range(1, n_pages + 1):
            img = _render_page(pdf, page, dpi, tmp)
            try:
                for text, wb, _conf in _word_boxes(img, pytesseract):
                    low = text.lower()
                    if any(n in low for n in needles):
                        found.append(Box(page=page, x0=wb[0], y0=wb[1],
                                         x1=wb[2], y1=wb[3], note=text))
            finally:
                img.close()
    return found


def _pad(box: Box, pad: float) -> Box:
    """Grow a box slightly so glyph edges and descenders fall inside it."""
    return Box(
        page=box.page,
        x0=max(0.0, box.x0 - pad), y0=max(0.0, box.y0 - pad),
        x1=min(1.0, box.x1 + pad), y1=min(1.0, box.y1 + pad),
        kind=box.kind, note=box.note,
    )


def _blank(img, boxes: list[Box]):
    """Destroy the redacted pixels — filled WHITE, not black.

    The bar the reader sees is drawn later, as vector graphics on top. What
    gets OCR'd and embedded is this: the page with the secret replaced by
    blank paper. Two reasons it is white rather than black. OCR reads the
    edges of a black bar as glyphs, which injected junk like "SS\\" and "(x"
    into the rebuilt text layer and dropped page confidence from 95 to 76.
    And white is not a weaker redaction than black: in both cases the
    original pixels are gone from the embedded image, so even someone who
    strips the drawn bars finds blank paper underneath.
    """
    out = img.convert("RGB")
    if not any(b.is_redaction for b in boxes):
        return out
    from PIL import ImageDraw
    W, H = out.size
    dr = ImageDraw.Draw(out)
    for b in boxes:
        if b.is_redaction:
            dr.rectangle([b.x0 * W, b.y0 * H, b.x1 * W, b.y1 * H], fill=(255, 255, 255))
    return out


def _overlay(width: float, height: float, boxes: list[Box],
             marker: str | None = MARKER) -> bytes:
    """One vector layer carrying every visible mark, over live or OCR'd text.

    Redaction bars are opaque black rectangles; highlights are translucent
    yellow; the marker is real text placed in each bar — drawn visibly in grey
    where the bar can hold it legibly, in invisible text mode where it cannot.
    Either way the marker reaches the text layer, so an extractor reads
    "... WITNESS: MARGARET [REDACTED] ..." instead of a silent gap that
    implies nothing was ever there.
    """
    esc = ""
    if marker:
        esc = marker.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
    ops: list[str] = []

    for b in boxes:
        if b.is_redaction:
            continue
        x, w = b.x0 * width, (b.x1 - b.x0) * width
        y, h = height - (b.y1 * height), (b.y1 - b.y0) * height
        ops += ["q", "/GS0 gs", "1 0.92 0 rg", f"{x:.2f} {y:.2f} {w:.2f} {h:.2f} re f", "Q"]

    for b in boxes:
        if not b.is_redaction:
            continue
        bw, bh = (b.x1 - b.x0) * width, (b.y1 - b.y0) * height
        x, y = b.x0 * width, height - (b.y1 * height)
        ops += ["q", "0 g", f"{x:.2f} {y:.2f} {bw:.2f} {bh:.2f} re f", "Q"]
        if not marker:
            continue
        # Helvetica averages ~0.55em per character; leave a margin.
        size = max(6.0, min(bh * 0.62, 14.0))
        text_w = len(marker) * size * 0.55
        visible = text_w < bw * 0.92 and bh > 9
        tx = x + max(0.0, (bw - text_w) / 2)
        ty = y + (bh - size * 0.72) / 2
        ops += [
            "BT", f"/F1 {size:.2f} Tf",
            "0.745 0.745 0.745 rg" if visible else "3 Tr",
            f"1 0 0 1 {tx:.2f} {ty:.2f} Tm", f"({esc}) Tj", "ET",
        ]

    if not ops:
        return b""
    stream = "\n".join(ops).encode("ascii")
    objs = [
        b"<</Type/Catalog/Pages 2 0 R>>",
        b"<</Type/Pages/Kids[3 0 R]/Count 1>>",
        b"<</Type/Page/Parent 2 0 R/MediaBox[0 0 "
        + f"{width:.2f} {height:.2f}".encode("ascii")
        + b"]/Resources<</Font<</F1 5 0 R>>/ExtGState<</GS0<</Type/ExtGState"
          b"/ca 0.35/BM/Multiply>>>>>>/Contents 4 0 R>>",
        b"<</Length " + str(len(stream)).encode("ascii") + b">>\nstream\n" + stream + b"\nendstream",
        b"<</Type/Font/Subtype/Type1/BaseFont/Helvetica>>",
    ]
    out = bytearray(b"%PDF-1.4\n")
    offsets = []
    for i, body in enumerate(objs, start=1):
        offsets.append(len(out))
        out += f"{i} 0 obj\n".encode("ascii") + body + b"\nendobj\n"
    xref_at = len(out)
    out += f"xref\n0 {len(objs) + 1}\n".encode("ascii") + b"0000000000 65535 f \n"
    for off in offsets:
        out += f"{off:010d} 00000 n \n".encode("ascii")
    out += (f"trailer\n<</Size {len(objs) + 1}/Root 1 0 R>>\nstartxref\n{xref_at}\n"
            .encode("ascii") + b"%%EOF\n")
    return bytes(out)


def _tokens(s: str) -> set[str]:
    return {t for t in re.split(r"[^0-9A-Za-z]+", s.lower()) if len(t) >= MIN_VERIFY_TOKEN}


def apply(
    pdf: str | Path,
    boxes: list[Box],
    out_path: str | Path,
    *,
    dpi: int = DEFAULT_DPI,
    marker: str | None = MARKER,
    pad: float = 0.002,
    verify_terms: list[str] | None = None,
) -> RedactionResult:
    """Write a redacted copy. The source file is never modified.

    Pages carrying a redaction are rasterised, burned, and given a fresh OCR
    text layer. Every other page is copied through with its original text
    intact. Document metadata is dropped rather than carried over, and the
    output is written whole — never as an incremental update, which would
    leave the original page objects sitting in the file behind the new ones.
    """
    pytesseract = _require("pytesseract", "ocr")
    _require("PIL", "ocr")
    pypdf = _require("pypdf", "pdf")
    _require_binaries()
    from pypdf import PdfReader, PdfWriter

    pdf, out_path = Path(pdf), Path(out_path)
    if not boxes:
        raise RedactionError("no boxes given; refusing to write a no-op copy")
    marks = [normalise(b) for b in boxes]

    reader = PdfReader(str(pdf))
    n_pages = len(reader.pages)
    for b in marks:
        if b.page > n_pages:
            raise RedactionError(
                f"box on page {b.page} but the document has {n_pages} page(s)"
            )

    by_page: dict[int, list[Box]] = {}
    for b in marks:
        by_page.setdefault(b.page, []).append(b)

    result = RedactionResult(out_path=out_path)
    per_box: dict[tuple[int, int], list[str]] = {}
    redaction_pages: dict[int, list[Box]] = {}
    writer = PdfWriter()

    with tempfile.TemporaryDirectory() as tmp:
        for page_no in range(1, n_pages + 1):
            page_marks = by_page.get(page_no, [])
            redactions = [b for b in page_marks if b.is_redaction]

            if not page_marks:
                writer.add_page(reader.pages[page_no - 1])
                result.pages_untouched.append(page_no)
                continue

            if not redactions:
                # Highlights only: keep the live text, composite an overlay.
                page = reader.pages[page_no - 1]
                w = float(page.mediabox.width)
                h = float(page.mediabox.height)
                ov = PdfReader(io.BytesIO(_overlay(w, h, page_marks, marker)))
                # Attach to the writer BEFORE compositing: pypdf's merge on a
                # detached page rewrites content it does not own, which it now
                # warns about and has never done reliably.
                writer.add_page(page)
                writer.pages[-1].merge_page(ov.pages[0])
                result.pages_untouched.append(page_no)
                continue

            img = _render_page(pdf, page_no, dpi, tmp)
            try:
                padded = [_pad(b, pad) if b.is_redaction else b for b in page_marks]
                # Record what is about to disappear, so verify() has something
                # concrete to look for afterwards.
                for text, wb, _c in _word_boxes(img, pytesseract):
                    for bi, b in enumerate(padded):
                        if b.is_redaction and _intersects(wb, b):
                            result.words_removed.append(text)
                            per_box.setdefault((page_no, bi), []).append(text)
                blanked = _blank(img, padded)
                # --dpi is REQUIRED, not a tuning knob. Without it tesseract
                # assumes 72 dpi and sizes the page from the pixel count, so a
                # 300 dpi render of a letter page comes back as a 36x47 inch
                # PDF. It renders correctly — everything scales together — so
                # the defect survives a visual check and only shows up when
                # someone prints or Bates-stamps the result.
                page_pdf = pytesseract.image_to_pdf_or_hocr(
                    blanked, extension="pdf", config=f"--dpi {dpi}"
                )
                confs = [c for _t, _b, c in _word_boxes(blanked, pytesseract)]
                mean = sum(confs) / len(confs) if confs else 0.0
                result.page_confidence[page_no] = mean
                if mean < LOW_CONFIDENCE:
                    result.warnings.append(
                        f"page {page_no}: rebuilt text layer has OCR confidence"
                        f" {mean:.0f} — the words are gone, but what remains may"
                        " not be reliably searchable"
                    )
                writer.add_page(PdfReader(io.BytesIO(page_pdf)).pages[0])
                # Second pass over the same page: the OCR'd image carries
                # blank paper where the secret was; this draws the bars the
                # reader sees and puts the marker into the text layer.
                placed = writer.pages[-1]
                ov = _overlay(float(placed.mediabox.width),
                              float(placed.mediabox.height), padded, marker)
                if ov:
                    placed.merge_page(PdfReader(io.BytesIO(ov)).pages[0])
                redaction_pages[page_no] = padded
                result.pages_rasterised.append(page_no)
            finally:
                img.close()

    writer.add_metadata({})  # do not carry the source document's metadata across
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "wb") as fh:
        writer.write(fh)

    # THE GATE: verify by PLACE, not by vocabulary. Asking whether a redacted
    # word appears anywhere in the finished document flags ordinary repeated
    # prose — headers, labels, party names occurring dozens of times — and on
    # real filings that refuses essentially every export. What actually has to
    # be true is narrower and checkable: the words that sat under a given bar
    # must not still be readable INSIDE that bar.
    result.survivors, result.unverifiable = _verify_regions(
        out_path, redaction_pages, per_box, pytesseract, dpi=dpi
    )
    # Advisory, never blocking: a term the human NAMED, still standing
    # somewhere else in the finished file. Usually a second occurrence nobody
    # marked. This deliberately checks only what a person asked to remove —
    # checking every word we happened to lift out of a box is the vocabulary
    # test that flags ordinary repeated prose and refuses real documents.
    result.also_appears = _terms_elsewhere(out_path, verify_terms or [])
    return result


def _verify_regions(out_path, redaction_pages, per_box, pytesseract, *, dpi):
    """Re-read each bar in the FINISHED file and look for what it buried.

    Returns (leaks, unverifiable). A leak is a word we set out to remove that
    is still readable inside its own bar. Unverifiable is the honest third
    answer: the box covered no text, so there is nothing to check.
    """
    leaks: list[str] = []
    unverifiable: list[str] = []
    if not redaction_pages:
        return leaks, unverifiable
    with tempfile.TemporaryDirectory() as tmp:
        for page_no, boxes in redaction_pages.items():
            # Untouched pages are written too, so output page numbers match
            # the source's.
            img = _render_page(out_path, page_no, dpi, tmp)
            try:
                W, H = img.size
                for bi, b in enumerate(boxes):
                    if not b.is_redaction:
                        continue
                    buried = {w.lower() for w in per_box.get((page_no, bi), [])}
                    if not buried:
                        # A box over a signature, a photo or a chart destroys
                        # pixels but leaves the check nothing to confirm.
                        # Calling that "verified" would hand an image
                        # redaction a proof it never earned.
                        unverifiable.append(
                            f"page {page_no}: box covered no readable text —"
                            " content was destroyed but cannot be machine-"
                            "verified; review this one by eye"
                        )
                        continue
                    crop = img.crop((int(b.x0 * W), int(b.y0 * H),
                                     max(int(b.x1 * W), int(b.x0 * W) + 1),
                                     max(int(b.y1 * H), int(b.y0 * H) + 1)))
                    seen = {t.lower() for t, _wb, _c in _word_boxes(crop, pytesseract)}
                    # Only words we set out to remove count. OCR noise off a
                    # black bar is not evidence of a leak.
                    still = sorted(buried & seen)
                    if still:
                        leaks.append(
                            f"page {page_no}: still readable inside the bar: "
                            + ", ".join(still)
                        )
            finally:
                img.close()
    return leaks, unverifiable


def _terms_elsewhere(out_path, terms):
    """Named terms still readable somewhere in the finished file."""
    from .extract import extract_path
    wanted = [t.strip().lower() for t in terms if t.strip()]
    if not wanted:
        return []
    body = re.sub(r"\s+", " ", extract_path(out_path, ocr="never").full_text).lower()
    return sorted({t for t in wanted if t in body})


def verify(pdf: str | Path, terms: list[str]) -> list[str]:
    """Re-read a finished file and report any term still extractable from it.

    This is the check that separates a redaction from a rectangle, and it runs
    against the written output rather than against what we believe we wrote.
    A survivor is not always a failed burn — the same word may appear
    elsewhere in the document, unmarked. Either way the human needs to see it.
    """
    from .extract import extract_path
    wanted = set()
    for t in terms:
        wanted |= _tokens(t)
    if not wanted:
        # Every term was shorter than MIN_VERIFY_TOKEN, so there is nothing to
        # look for. Returning "none found" here would be a clean bill of health
        # on a check that never ran, which is the one thing this function
        # exists to never do.
        raise RedactionError(
            f"nothing to check: every term given is shorter than"
            f" {MIN_VERIFY_TOKEN} characters ({', '.join(terms)}). A term that"
            " short would match half the document; give the actual value you"
            " redacted."
        )
    found = _tokens(extract_path(pdf, ocr="never").full_text)
    # The marker we inject is not a leak of the source document.
    return sorted((wanted & found) - _tokens(MARKER))
