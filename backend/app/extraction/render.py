"""Turn an uploaded file into one or more PNG images for the model.

Limits (DL-42), all configurable through Settings:
  - file size: 10 MB by default;
  - PDF pages: 5 by default. A longer PDF is rejected, not truncated, so no page is
    silently ignored;
  - PDF rasterization: 170 DPI by default.
"""

from pathlib import Path

import pymupdf

from app.extraction.errors import UnreadableDocument, UnsupportedDocumentType

_SIGNATURES = {
    ".pdf": (b"%PDF",),
    ".png": (b"\x89PNG\r\n\x1a\n",),
    ".jpg": (b"\xff\xd8\xff",),
    ".jpeg": (b"\xff\xd8\xff",),
}
SUPPORTED_EXTENSIONS = tuple(_SIGNATURES)


def render_document(
    data: bytes,
    filename: str,
    *,
    dpi: int = 170,
    max_pages: int = 5,
    max_bytes: int = 10 * 1024 * 1024,
) -> list[bytes]:
    """Return the document as a list of PNG images, one per page."""
    extension = Path(filename).suffix.lower()
    if extension not in _SIGNATURES:
        raise UnsupportedDocumentType(
            f"File type '{extension or 'none'}' is not supported. Upload a PDF, PNG or JPG."
        )
    if not data:
        raise UnreadableDocument("The file is empty.")
    if len(data) > max_bytes:
        raise UnreadableDocument(
            f"The file is larger than the {max_bytes // (1024 * 1024)} MB limit."
        )
    if not data.startswith(_SIGNATURES[extension]):
        raise UnreadableDocument(
            f"The file content does not match its '{extension}' extension or is corrupt."
        )

    if extension == ".pdf":
        return _render_pdf(data, dpi=dpi, max_pages=max_pages)
    return [_render_image(data)]


def _render_pdf(data: bytes, *, dpi: int, max_pages: int) -> list[bytes]:
    try:
        document = pymupdf.open(stream=data, filetype="pdf")
    except Exception as exc:  # pymupdf raises several unrelated exception types
        raise UnreadableDocument("The PDF is corrupt or cannot be opened.") from exc
    with document:
        if document.needs_pass:
            raise UnreadableDocument("The PDF is password protected.")
        if document.page_count == 0:
            raise UnreadableDocument("The PDF has no pages.")
        if document.page_count > max_pages:
            raise UnreadableDocument(
                f"The PDF has {document.page_count} pages; the maximum is {max_pages}."
            )
        try:
            return [page.get_pixmap(dpi=dpi).tobytes("png") for page in document]
        except Exception as exc:
            raise UnreadableDocument("The PDF pages cannot be rendered.") from exc


def _render_image(data: bytes) -> bytes:
    """Decode the image and re-encode it as PNG. Decoding also detects corrupt files."""
    try:
        pixmap = pymupdf.Pixmap(data)
        if pixmap.width == 0 or pixmap.height == 0:
            raise ValueError("empty image")
        if pixmap.alpha or pixmap.colorspace is None or pixmap.colorspace.n > 3:
            pixmap = pymupdf.Pixmap(pymupdf.csRGB, pixmap, 0)  # drop alpha / CMYK
        return pixmap.tobytes("png")
    except Exception as exc:
        raise UnreadableDocument("The image is corrupt or cannot be decoded.") from exc
