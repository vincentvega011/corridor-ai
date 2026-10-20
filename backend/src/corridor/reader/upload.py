"""Upload handling for the quarantined reader (docs/spec/modules.md 5.5).

The file type comes from magic bytes, never from the extension or the client's content type. Images are
decoded, converted to plain RGB, downscaled and re-encoded as PNG, which drops every metadata block. PDFs
are rasterised to an image with pypdfium2 (PDFium, BSD-3-Clause and Apache-2.0); the text layer is never
read, so hidden or white text in a PDF has no more influence than pixels do. Only the sanitised PNG is
stored, named by the SHA-256 of the original bytes, under ``data/uploads/`` (gitignored).
"""

import hashlib
import io
import threading
from dataclasses import dataclass
from pathlib import Path

import pypdfium2 as pdfium
from PIL import Image, UnidentifiedImageError

from corridor.config import REPO_ROOT

MAX_UPLOAD_BYTES = 10 * 1024 * 1024
MAX_SIDE = 1568
MAX_PIXELS = 40_000_000
MAX_PDF_PAGES = 20
UPLOAD_DIR = REPO_ROOT / "data" / "uploads"
Image.MAX_IMAGE_PIXELS = MAX_PIXELS  # a decompression bomb raises instead of exhausting memory


_PDFIUM_LOCK = threading.Lock()


class UploadError(Exception):
    """The upload was refused. ``code`` is a short machine readable reason."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def detect_kind(data: bytes) -> str:
    """``png``, ``jpeg`` or ``pdf`` from the first bytes, else raise ``UploadError``."""
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "png"
    if data.startswith(b"\xff\xd8\xff"):
        return "jpeg"
    if data.startswith(b"%PDF-"):
        return "pdf"
    raise UploadError("unsupported_type")


def _encode(img: Image.Image) -> bytes:
    rgb = img.convert("RGB")  # drops alpha, palette and metadata
    rgb.thumbnail((MAX_SIDE, MAX_SIDE), Image.Resampling.LANCZOS)
    clean = Image.frombytes("RGB", rgb.size, rgb.tobytes())  # a fresh image: no info dict, no EXIF
    out = io.BytesIO()
    clean.save(out, format="PNG")
    return out.getvalue()


def sanitize_image(data: bytes) -> bytes:
    """Decode, downscale and re-encode an image as metadata free PNG bytes."""
    try:
        with Image.open(io.BytesIO(data)) as img:
            if img.size[0] * img.size[1] > MAX_PIXELS:  # refuse before decoding a single pixel
                raise UploadError("image_too_large")
            img.load()
            return _encode(img)
    except UploadError:
        raise
    except (UnidentifiedImageError, OSError, Image.DecompressionBombError, ValueError) as exc:
        raise UploadError("unreadable_image") from exc


def rasterise_pdf(data: bytes) -> bytes:
    """Render the first page of a PDF to PNG bytes. The text layer is never used.

    PDFium is not thread safe, so rendering is serialised by a module lock."""
    with _PDFIUM_LOCK:
        return _rasterise_pdf(data)


def _rasterise_pdf(data: bytes) -> bytes:
    try:
        pdf = pdfium.PdfDocument(data)
    except Exception as exc:
        raise UploadError("unreadable_pdf") from exc
    try:
        if len(pdf) == 0 or len(pdf) > MAX_PDF_PAGES:
            raise UploadError("pdf_page_count")
        page = pdf[0]
        width, height = page.get_size()
        if width <= 0 or height <= 0 or width * height > 10_000_000:
            raise UploadError("pdf_page_size")
        scale = MAX_SIDE / max(width, height)
        image = page.render(scale=scale).to_pil()
        return _encode(image)
    except UploadError:
        raise
    except Exception as exc:
        raise UploadError("unreadable_pdf") from exc
    finally:
        pdf.close()


@dataclass(frozen=True)
class StoredUpload:
    """A processed upload: the hash of the original bytes and the sanitised PNG."""

    doc_hash: str
    kind: str
    png: bytes
    path: Path


def process_upload(
    data: bytes, upload_dir: Path | None = None, user_id: str = "shared"
) -> StoredUpload:
    """Validate, sanitise and store an upload under a folder per user. Raises ``UploadError``."""
    if not data:
        raise UploadError("empty")
    if len(data) > MAX_UPLOAD_BYTES:
        raise UploadError("too_large")
    kind = detect_kind(data)
    png = rasterise_pdf(data) if kind == "pdf" else sanitize_image(data)
    doc_hash = hashlib.sha256(data).hexdigest()
    safe_user = "".join(c for c in user_id if c.isalnum() or c in "_-")[:64] or "shared"
    target = (upload_dir or UPLOAD_DIR) / safe_user / f"{doc_hash}.png"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(png)
    return StoredUpload(doc_hash, kind, png, target)
