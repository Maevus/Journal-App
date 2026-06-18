"""Tests for the GoodNotes OCR helpers: blank detection and PDF splitting,
including the malformed-PDF and blank/missing-text edge cases."""
import io

import pypdf
import pytest

from services.ingestion import _is_blank_transcript, _split_pdf_pages


@pytest.mark.parametrize(
    "text",
    ["", "   ", "[BLANK]", "[blank]", "No text on this page", "blank", "nothing to transcribe"],
)
def test_blank_transcripts_detected(text):
    assert _is_blank_transcript(text) is True


def test_real_content_not_blank():
    assert _is_blank_transcript("Today I climbed my first V3 and felt amazing.") is False


def test_long_text_mentioning_blank_is_not_flagged():
    text = (
        "The page in front of me was a blank canvas and I had no idea what to "
        "write, but I kept going anyway for a good long while."
    )
    assert len(text) >= 80
    assert _is_blank_transcript(text) is False


def _make_pdf(num_pages: int, path):
    writer = pypdf.PdfWriter()
    for _ in range(num_pages):
        writer.add_blank_page(width=200, height=200)
    with open(path, "wb") as fh:
        writer.write(fh)


def test_split_pdf_returns_one_blob_per_page(tmp_path):
    pdf = tmp_path / "notebook.pdf"
    _make_pdf(3, pdf)
    pages = _split_pdf_pages(pdf)
    assert len(pages) == 3
    for blob in pages:
        assert isinstance(blob, bytes)
        assert len(pypdf.PdfReader(io.BytesIO(blob)).pages) == 1


def test_malformed_pdf_raises(tmp_path):
    # Documents current behaviour: _split_pdf_pages has no guard, so a corrupt
    # PDF propagates the pypdf error (which would abort a GoodNotes scan).
    bad = tmp_path / "broken.pdf"
    bad.write_bytes(b"this is not a pdf at all")
    with pytest.raises(Exception):
        _split_pdf_pages(bad)
