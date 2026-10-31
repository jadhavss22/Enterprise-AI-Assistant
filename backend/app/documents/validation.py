import ntpath
import re
import unicodedata
import zipfile
from io import BytesIO

from fastapi import HTTPException

MAX_UPLOAD_BYTES = 20 * 1024 * 1024
MAX_DOCX_UNCOMPRESSED_BYTES = 100 * 1024 * 1024
MAX_DOCX_ENTRIES = 10_000
MIME_BY_EXTENSION = {
    ".pdf": "application/pdf",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".txt": "text/plain",
}


def fail(status: int, code: str, message: str) -> HTTPException:
    return HTTPException(status, detail={"code": code, "message": message})


def sanitize_filename(filename: str | None) -> str:
    raw = unicodedata.normalize(
        "NFKC", ntpath.basename((filename or "").replace("/", "\\"))
    ).strip()
    raw = "".join(ch for ch in raw if ch.isprintable() and ch not in "\r\n\t")
    raw = re.sub(r"[^\w.\- ()]", "_", raw, flags=re.UNICODE).strip(" .")
    if not raw or raw in {".", ".."}:
        raise fail(422, "FILENAME_INVALID", "Choose a valid PDF, DOCX, or TXT filename")
    if len(raw) > 255:
        suffix = ntpath.splitext(raw)[1]
        raw = raw[: 255 - len(suffix)] + suffix
    if ntpath.splitext(raw)[1].lower() not in MIME_BY_EXTENSION:
        raise fail(
            415, "FILE_TYPE_UNSUPPORTED", "Only PDF, DOCX, and TXT files are supported"
        )
    return raw


def validate_upload(
    filename: str, content_type: str | None, content: bytes
) -> tuple[str, str]:
    safe_name = sanitize_filename(filename)
    extension = ntpath.splitext(safe_name)[1].lower()
    expected = MIME_BY_EXTENSION[extension]
    if not content:
        raise fail(422, "FILE_EMPTY", "The uploaded file is empty")
    if len(content) > MAX_UPLOAD_BYTES:
        raise fail(413, "FILE_TOO_LARGE", "Files must be 20 MB or smaller")
    supplied = (content_type or "").split(";", 1)[0].strip().lower()
    if supplied not in {expected, "application/octet-stream", ""}:
        raise fail(
            415,
            "FILE_TYPE_MISMATCH",
            "The file content type does not match its extension",
        )
    if extension == ".pdf":
        if not content.lstrip(b"\xef\xbb\xbf \t\r\n")[:1024].startswith(b"%PDF-"):
            raise fail(415, "FILE_CONTENT_INVALID", "The file is not a valid PDF")
    elif extension == ".docx":
        try:
            with zipfile.ZipFile(BytesIO(content)) as archive:
                entries = archive.infolist()
                if len(entries) > MAX_DOCX_ENTRIES:
                    raise ValueError("too many entries")
                if (
                    sum(item.file_size for item in entries)
                    > MAX_DOCX_UNCOMPRESSED_BYTES
                ):
                    raise ValueError("expanded file exceeds limit")
                if any(
                    item.file_size > 1_000_000 and item.compress_size == 0
                    for item in entries
                ):
                    raise ValueError("invalid compression ratio")
                if any(
                    item.compress_size and item.file_size / item.compress_size > 200
                    for item in entries
                ):
                    raise ValueError("compression ratio exceeds limit")
                names = set(archive.namelist())
                if (
                    "[Content_Types].xml" not in names
                    or "word/document.xml" not in names
                ):
                    raise ValueError("required Word document parts are missing")
        except (OSError, zipfile.BadZipFile, RuntimeError, ValueError):
            raise fail(
                415, "FILE_CONTENT_INVALID", "The file is not a valid DOCX document"
            ) from None
    else:
        try:
            decoded = content.decode("utf-8-sig", errors="strict")
        except UnicodeDecodeError:
            raise fail(
                415, "TEXT_ENCODING_UNSUPPORTED", "TXT files must use UTF-8 encoding"
            ) from None
        if "\x00" in decoded:
            raise fail(
                415,
                "TEXT_CONTENT_INVALID",
                "The TXT file contains unsupported binary data",
            )
    return safe_name, expected
