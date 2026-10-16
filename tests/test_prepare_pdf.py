import sys
import tempfile
import unittest
from pathlib import Path

from pypdf import PdfWriter
from pypdf.generic import DecodedStreamObject, NameObject, DictionaryObject

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from prepare_jobs import prepare_job


class PDFTests(unittest.TestCase):
    def test_pdf_text_and_link_are_preserved(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "job.pdf"
            writer = PdfWriter()
            page = writer.add_blank_page(width=600, height=800)
            font = DictionaryObject({NameObject("/Type"): NameObject("/Font"),
                                     NameObject("/Subtype"): NameObject("/Type1"),
                                     NameObject("/BaseFont"): NameObject("/Helvetica")})
            page[NameObject("/Resources")] = DictionaryObject({NameObject("/Font"):
                DictionaryObject({NameObject("/F1"): font})})
            stream = DecodedStreamObject()
            stream.set_data(b"BT /F1 12 Tf 50 700 Td (Roblox internship requirements) Tj ET")
            page[NameObject("/Contents")] = writer._add_object(stream)
            from pypdf.annotations import Link
            writer.add_annotation(0, Link(rect=(50, 650, 150, 670), url="https://example.com/apply"))
            writer.write(path)
            text = prepare_job(path)
            self.assertIn("Roblox internship requirements", text)
            self.assertIn("https://example.com/apply", text)

    def test_blank_pdf_explains_ocr_limitation(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "scan.pdf"
            writer = PdfWriter()
            writer.add_blank_page(width=600, height=800)
            writer.write(path)
            with self.assertRaisesRegex(ValueError, "OCR"):
                prepare_job(path)

    def test_html_still_keeps_application_links(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "job.html"
            path.write_text('<p>Job description</p><a href="https://example.com/apply">Apply</a>')
            self.assertIn("https://example.com/apply", prepare_job(path))


if __name__ == "__main__":
    unittest.main()
