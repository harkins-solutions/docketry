"""Redaction tests.

The OCR-dependent tests skip when tesseract/poppler are absent so the suite
still runs on a bare box; the geometry, refusal and overlay tests do not need
them and always run.
"""
import shutil
import tempfile
import unittest
from pathlib import Path

from docketry.tools.redact import (
    MARKER,
    Box,
    RedactionError,
    _overlay,
    _intersects,
    _tokens,
    normalise,
)

HAS_OCR = shutil.which("tesseract") is not None and shutil.which("pdftoppm") is not None


class TestGeometry(unittest.TestCase):
    def test_reversed_box_is_ordered(self):
        b = normalise(Box(page=1, x0=0.8, y0=0.9, x1=0.2, y1=0.1))
        self.assertEqual((b.x0, b.x1), (0.2, 0.8))
        self.assertEqual((b.y0, b.y1), (0.1, 0.9))

    def test_out_of_range_box_is_clamped(self):
        b = normalise(Box(page=1, x0=-3.0, y0=-1.0, x1=9.0, y1=4.0))
        self.assertEqual((b.x0, b.y0, b.x1, b.y1), (0.0, 0.0, 1.0, 1.0))

    def test_zero_area_redaction_is_refused(self):
        # The trap: it draws nothing but still costs the page its text layer,
        # so the file looks redacted and hides nothing.
        with self.assertRaises(RedactionError) as ctx:
            normalise(Box(page=2, x0=0.5, y0=0.5, x1=0.5, y1=0.9))
        self.assertIn("no area", str(ctx.exception))

    def test_zero_area_highlight_is_allowed(self):
        b = normalise(Box(page=1, x0=0.5, y0=0.5, x1=0.5, y1=0.5, kind="highlight"))
        self.assertEqual(b.kind, "highlight")

    def test_page_numbers_are_one_based(self):
        with self.assertRaises(RedactionError):
            normalise(Box(page=0, x0=0.1, y0=0.1, x1=0.2, y1=0.2))

    def test_intersects(self):
        box = Box(page=1, x0=0.2, y0=0.2, x1=0.5, y1=0.5)
        self.assertTrue(_intersects((0.4, 0.4, 0.6, 0.6), box))
        self.assertFalse(_intersects((0.5, 0.5, 0.7, 0.7), box))   # touching, not overlapping
        self.assertFalse(_intersects((0.0, 0.0, 0.1, 0.1), box))


class TestVerifyTokens(unittest.TestCase):
    def test_short_tokens_are_ignored(self):
        self.assertEqual(_tokens("a of Doe"), {"doe"})

    def test_punctuation_splits(self):
        self.assertEqual(_tokens("123-45-6789"), {"123", "6789"})


class TestOverlay(unittest.TestCase):
    def test_overlay_is_a_readable_pdf(self):
        pypdf = __import__("pypdf")
        import io
        data = _overlay(612.0, 792.0, [Box(page=1, x0=0.1, y0=0.1,
                                                     x1=0.4, y1=0.2, kind="highlight")])
        reader = pypdf.PdfReader(io.BytesIO(data))
        self.assertEqual(len(reader.pages), 1)
        self.assertAlmostEqual(float(reader.pages[0].mediabox.width), 612.0, places=1)

    def test_overlay_flips_to_pdf_origin(self):
        # A box at the TOP of the page (y0 small) must land at a HIGH y in PDF
        # space, which is bottom-left origin. Getting this backwards put marks
        # at the wrong end of the page.
        data = _overlay(100.0, 200.0,
                                  [Box(page=1, x0=0.0, y0=0.0, x1=1.0, y1=0.1,
                                       kind="highlight")])
        self.assertIn(b"180.00", data)   # 200 - (0.1 * 200)


def _text_pdf(path, lines, *, width=612.0, height=792.0):
    """A real one-page PDF with selectable text, hand-built with no dependency.

    The fixture has to carry live text or the end-to-end test proves nothing:
    the whole claim is that text which WAS extractable no longer is.
    """
    ops = ["BT", "/F1 18 Tf"]
    y = height - 90
    for line in lines:
        ops.append(f"1 0 0 1 72 {y:.2f} Tm ({line}) Tj")
        y -= 40
    ops.append("ET")
    stream = "\n".join(ops).encode("ascii")
    objs = [
        b"<</Type/Catalog/Pages 2 0 R>>",
        b"<</Type/Pages/Kids[3 0 R]/Count 1>>",
        b"<</Type/Page/Parent 2 0 R/MediaBox[0 0 "
        + f"{width:.2f} {height:.2f}".encode("ascii")
        + b"]/Resources<</Font<</F1 5 0 R>>>>/Contents 4 0 R>>",
        b"<</Length " + str(len(stream)).encode("ascii") + b">>\nstream\n" + stream + b"\nendstream",
        b"<</Type/Font/Subtype/Type1/BaseFont/Helvetica>>",
    ]
    out = bytearray(b"%PDF-1.4\n")
    offsets = []
    for i, body in enumerate(objs, start=1):
        offsets.append(len(out))
        out += f"{i} 0 obj\n".encode("ascii") + body + b"\nendobj\n"
    xref_at = len(out)
    out += f"xref\n0 {len(objs)+1}\n".encode("ascii") + b"0000000000 65535 f \n"
    for off in offsets:
        out += f"{off:010d} 00000 n \n".encode("ascii")
    out += (f"trailer\n<</Size {len(objs)+1}/Root 1 0 R>>\nstartxref\n{xref_at}\n"
            .encode("ascii") + b"%%EOF\n")
    Path(path).write_bytes(bytes(out))
    return path



class TestMarkerText(unittest.TestCase):
    def test_big_box_draws_the_marker_visibly(self):
        data = _overlay(612.0, 792.0,
                               [Box(page=1, x0=0.1, y0=0.1, x1=0.9, y1=0.16)])
        self.assertIn(b"([REDACTED]) Tj", data)
        self.assertIn(b"rg", data)          # a fill colour = drawn visibly
        self.assertNotIn(b"3 Tr", data)     # not invisible mode

    def test_tiny_box_still_reaches_the_text_layer(self):
        # Too small to letter legibly, but the extractor must still report it.
        data = _overlay(612.0, 792.0,
                               [Box(page=1, x0=0.1, y0=0.1, x1=0.13, y1=0.108)])
        self.assertIn(b"([REDACTED]) Tj", data)
        self.assertIn(b"3 Tr", data)        # invisible text render mode

    def test_highlights_get_no_marker(self):
        data = _overlay(612.0, 792.0,
                        [Box(page=1, x0=0.1, y0=0.1, x1=0.9, y1=0.2,
                             kind="highlight")])
        self.assertNotIn(b"[REDACTED]", data)   # a highlight hides nothing
        self.assertIn(b"1 0.92 0 rg", data)     # but it is still drawn

    def test_no_marks_produces_no_overlay(self):
        self.assertEqual(_overlay(612.0, 792.0, []), b"")

    def test_parens_in_a_custom_marker_are_escaped(self):
        data = _overlay(612.0, 792.0,
                               [Box(page=1, x0=0.1, y0=0.1, x1=0.9, y1=0.16)],
                               marker="(withheld)")
        self.assertIn(rb"(\(withheld\)) Tj", data)


@unittest.skipUnless(HAS_OCR, "needs tesseract + poppler")
class TestApplyEndToEnd(unittest.TestCase):
    """The claim under test: text that WAS extractable is no longer in the file."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.src = _text_pdf(self.tmp / "src.pdf",
                             ["PLAINTIFF ZEBRAFISH CORP", "SECRET WITNESS QUAGGA"])
        self.out = self.tmp / "out.pdf"

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_source_text_starts_extractable(self):
        from docketry.tools.extract import extract_path
        self.assertIn("QUAGGA", extract_path(self.src, ocr="never").full_text.upper())

    def test_redacted_word_is_gone_and_page_stays_searchable(self):
        from docketry.tools.redact import Box, apply
        from docketry.tools.extract import extract_path
        # Second line sits lower on the page; box it.
        res = apply(self.src,
                    [Box(page=1, x0=0.05, y0=0.13, x1=0.75, y1=0.20)],
                    self.out, verify_terms=["QUAGGA"])
        text = extract_path(self.out, ocr="never").full_text.upper()
        self.assertNotIn("QUAGGA", text)
        self.assertEqual(res.pages_rasterised, [1])
        self.assertEqual(res.survivors, [])
        # The re-added text layer is the point: the page is still searchable.
        self.assertTrue(text.strip(), "redacted page lost its text layer entirely")


    def test_text_layer_says_redacted_rather_than_going_silent(self):
        from docketry.tools.redact import Box, apply
        from docketry.tools.extract import extract_path
        apply(self.src, [Box(page=1, x0=0.05, y0=0.13, x1=0.75, y1=0.20)],
              self.out, verify_terms=["QUAGGA"])
        text = extract_path(self.out, ocr="never").full_text.upper()
        # A hole with no marker reads as "nothing was ever here".
        self.assertIn("REDACTED", text)
        self.assertNotIn("QUAGGA", text)

    def test_marker_is_not_reported_as_a_survivor(self):
        from docketry.tools.redact import Box, apply
        res = apply(self.src, [Box(page=1, x0=0.05, y0=0.13, x1=0.75, y1=0.20)],
                    self.out, verify_terms=["QUAGGA"])
        self.assertEqual(res.survivors, [])


    def test_a_word_repeated_elsewhere_is_not_a_leak(self):
        """The bug real exhibits exposed: verifying vocabulary, not place.

        Redact one occurrence of a word that also appears elsewhere in the
        document unmarked. Checking whether the word appears ANYWHERE flags
        this and would refuse every real filing, whose prose repeats headers
        and labels dozens of times. The burn is fine; the question was wrong.
        """
        from docketry.tools.redact import Box, apply
        src = _text_pdf(self.tmp / "repeat.pdf", [
            "EXHIBIT LIST", "WITNESS EXHIBIT ALPHA", "EXHIBIT INDEX",
        ])
        res = apply(src, [Box(page=1, x0=0.05, y0=0.13, x1=0.75, y1=0.20)],
                    self.out)
        self.assertEqual(res.survivors, [],
                         "a word standing elsewhere is not a failed burn")
        # It is still worth SAYING so, just never as a block.
        self.assertTrue(hasattr(res, "also_appears"))


    def test_output_page_keeps_the_source_page_size(self):
        """A redacted exhibit must stay letter-sized.

        Tesseract sizes its PDF page from the pixel count at an assumed 72 dpi
        unless told otherwise, turning a 300 dpi letter page into 36x47
        inches. It looks right on screen because the whole page scales
        uniformly; it is wrong the moment anyone prints or stamps it.
        """
        from pypdf import PdfReader
        from docketry.tools.redact import Box, apply
        before = PdfReader(str(self.src)).pages[0].mediabox
        apply(self.src, [Box(page=1, x0=0.05, y0=0.13, x1=0.75, y1=0.20)], self.out)
        after = PdfReader(str(self.out)).pages[0].mediabox
        self.assertAlmostEqual(float(before.width), float(after.width), delta=2.0)
        self.assertAlmostEqual(float(before.height), float(after.height), delta=2.0)


    def test_a_box_over_no_text_is_unverifiable_not_verified(self):
        """A box over a signature or a photo earns no proof.

        The pixels are destroyed either way, but there are no words to look
        for afterwards, so reporting it as verified would hand an image
        redaction a guarantee the check never made.
        """
        from docketry.tools.redact import Box, apply
        # Bottom half of the fixture holds no text at all.
        res = apply(self.src, [Box(page=1, x0=0.1, y0=0.60, x1=0.6, y1=0.72)],
                    self.out)
        self.assertEqual(res.survivors, [])
        self.assertEqual(len(res.unverifiable), 1)
        self.assertIn("by eye", res.unverifiable[0])


    def test_find_terms_locates_a_term_and_writes_nothing(self):
        from docketry.tools.redact import find_terms
        before = sorted(p.name for p in self.tmp.iterdir())
        hits = find_terms(self.src, ["QUAGGA"])
        self.assertEqual(len(hits), 1)
        self.assertEqual(hits[0].page, 1)
        self.assertIn("QUAGGA", hits[0].note.upper())
        # A preview that writes is not a preview.
        self.assertEqual(sorted(p.name for p in self.tmp.iterdir()), before)

    def test_find_terms_matches_inside_a_longer_word(self):
        from docketry.tools.redact import find_terms
        self.assertTrue(find_terms(self.src, ["quag"]))


    def test_no_marker_leaves_the_text_layer_silent(self):
        """Opt-out exists, and the cost of taking it is real.

        Without a marker the bar is unlabelled and the extracted text has an
        unexplained hole — which is exactly why it is not the default.
        """
        from docketry.tools.redact import Box, apply
        from docketry.tools.extract import extract_path
        apply(self.src, [Box(page=1, x0=0.05, y0=0.13, x1=0.75, y1=0.20)],
              self.out, marker=None)
        text = extract_path(self.out, ocr="never").full_text.upper()
        self.assertNotIn("QUAGGA", text)
        self.assertNotIn("REDACTED", text)

    def test_a_named_term_left_standing_elsewhere_is_advisory_not_a_failure(self):
        """The common miss: a name redacted in one place, standing in another.

        The burn worked, so this must not block — but nobody should have to
        notice the second occurrence on their own.
        """
        from docketry.tools.redact import Box, apply, find_terms
        src = _text_pdf(self.tmp / "twice.pdf", [
            "WITNESS QUAGGA TESTIFIED", "SEE ALSO QUAGGA EXHIBIT",
        ])
        first = [b for b in find_terms(src, ["QUAGGA"]) if b.y0 < 0.15][:1]
        self.assertTrue(first, "fixture should place one occurrence up top")
        res = apply(src, first, self.out, verify_terms=["QUAGGA"])
        self.assertEqual(res.survivors, [])          # the burn is sound
        self.assertEqual(res.also_appears, ["quagga"])  # but say it is elsewhere

    def test_untouched_page_keeps_original_text(self):
        from docketry.tools.redact import Box, apply
        from docketry.tools.extract import extract_path
        src2 = _text_pdf(self.tmp / "two.pdf", ["ONLY PAGE ZEBRAFISH"])
        res = apply(src2, [Box(page=1, x0=0.05, y0=0.85, x1=0.5, y1=0.95,
                               kind="highlight")], self.out)
        self.assertEqual(res.pages_rasterised, [])
        self.assertIn("ZEBRAFISH", extract_path(self.out, ocr="never").full_text.upper())

    def test_verify_reports_a_survivor(self):
        from docketry.tools.redact import verify
        self.assertEqual(verify(self.src, ["QUAGGA"]), ["quagga"])


class TestRefusals(unittest.TestCase):
    def test_apply_refuses_empty_box_list(self):
        from docketry.tools.redact import apply
        with self.assertRaises(RedactionError):
            apply("nonexistent.pdf", [], "out.pdf")

    def test_find_terms_refuses_empty_terms(self):
        from docketry.tools.redact import find_terms
        with self.assertRaises(RedactionError):
            find_terms("nonexistent.pdf", [])
        with self.assertRaises(RedactionError):
            find_terms("nonexistent.pdf", ["  "])


if __name__ == "__main__":
    unittest.main()


class TestVerifyRefusesAnEmptyCheck(unittest.TestCase):
    def test_all_terms_too_short_raises_instead_of_reporting_clean(self):
        from docketry.tools.redact import RedactionError, verify
        with self.assertRaises(RedactionError) as ctx:
            verify(__file__, ["x", "an"])
        self.assertIn("nothing to check", str(ctx.exception))

    def test_a_usable_term_alongside_a_short_one_still_checks(self):
        from docketry.tools.redact import verify
        tmp = Path(tempfile.mkdtemp()) / "doc.txt"
        tmp.write_text("the witness QUAGGA testified")
        # The long term is checkable, so the check runs and finds it.
        self.assertEqual(verify(tmp, ["x", "QUAGGA"]), ["quagga"])


class TestMissingDependencyMessage(unittest.TestCase):
    """What to do about it differs by how you installed Docketry."""

    def test_a_pip_install_is_told_to_add_the_extra(self):
        from docketry.tools.redact import _missing
        msg = _missing("pytesseract", "ocr", "redaction")
        self.assertIn("pip install 'docketry[ocr]'", msg)
        self.assertNotIn("this build", msg)

    def test_a_packaged_binary_is_not_told_to_pip_into_itself(self):
        # The 0.15.0 executable said "pip install docketry[ocr]", which the
        # person running a one-file build cannot do — it reads as broken
        # rather than incomplete.
        import sys
        from docketry.tools.redact import _missing
        sys.frozen = True
        try:
            msg = _missing("pytesseract", "ocr", "redaction")
        finally:
            del sys.frozen
        self.assertIn("does not include redaction support", msg)
        self.assertIn("pytesseract", msg)
