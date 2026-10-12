import pytest

from app.extraction.errors import UnreadableDocument, UnsupportedDocumentType
from app.extraction.render import render_document
from extraction_builders import make_jpg, make_pdf, make_png

PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"


def test_single_page_pdf_renders_one_png():
    images = render_document(make_pdf(1), "invoice.pdf")
    assert len(images) == 1
    assert images[0].startswith(PNG_SIGNATURE)


def test_multi_page_pdf_renders_every_page():
    images = render_document(make_pdf(3), "invoice.pdf")
    assert len(images) == 3
    assert all(image.startswith(PNG_SIGNATURE) for image in images)


def test_pdf_above_page_limit_is_rejected_not_truncated():
    with pytest.raises(UnreadableDocument, match="6 pages; the maximum is 5"):
        render_document(make_pdf(6), "invoice.pdf", max_pages=5)


def test_higher_dpi_gives_a_larger_image():
    low = render_document(make_pdf(1), "invoice.pdf", dpi=72)[0]
    high = render_document(make_pdf(1), "invoice.pdf", dpi=200)[0]
    assert len(high) > len(low)


def test_png_is_accepted():
    images = render_document(make_png(), "scan.png")
    assert len(images) == 1 and images[0].startswith(PNG_SIGNATURE)


def test_jpg_is_reencoded_as_png():
    images = render_document(make_jpg(), "scan.JPG")
    assert len(images) == 1 and images[0].startswith(PNG_SIGNATURE)


def test_corrupt_pdf_is_unreadable():
    with pytest.raises(UnreadableDocument):
        render_document(b"%PDF-1.7 this is not really a pdf", "invoice.pdf")


def test_corrupt_png_is_unreadable():
    with pytest.raises(UnreadableDocument):
        render_document(PNG_SIGNATURE + b"garbage" * 20, "scan.png")


def test_content_not_matching_extension_is_unreadable():
    with pytest.raises(UnreadableDocument, match="does not match"):
        render_document(make_png(), "invoice.pdf")


def test_empty_file_is_unreadable():
    with pytest.raises(UnreadableDocument, match="empty"):
        render_document(b"", "invoice.pdf")


def test_unsupported_type_is_rejected():
    with pytest.raises(UnsupportedDocumentType, match="not supported"):
        render_document(b"some text", "invoice.docx")


def test_file_above_size_limit_is_rejected():
    with pytest.raises(UnreadableDocument, match="1 MB limit"):
        render_document(make_pdf(1) + b"0" * (1024 * 1024), "invoice.pdf", max_bytes=1024 * 1024)
