"""Bounded OCR processes for the Linux ingestion worker."""

import json
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from fastapi import HTTPException

from app.config import settings
from app.documents.validation import fail

MAX_PAGE_CHARACTERS = 50_000
MAX_RESULT_BYTES = MAX_PAGE_CHARACTERS * 6 + 1024
OCR_ERRORS = {
    "OCR_PAGE_LIMIT": (
        422,
        "This PDF has too many scanned pages. Split it into smaller files.",
    ),
    "OCR_UNAVAILABLE": (
        503,
        "The English OCR engine is unavailable. Restart the updated worker and retry.",
    ),
    "OCR_TIMEOUT": (
        503,
        "OCR exceeded the per-page time limit. Retry or upload a clearer scan.",
    ),
    "OCR_DOCUMENT_TIMEOUT": (
        422,
        "OCR exceeded the document time limit. Split the PDF into smaller files.",
    ),
    "OCR_RESOURCE_LIMIT": (422, "The scanned page exceeds the OCR processing limits."),
    "OCR_UNREADABLE": (
        422,
        "A scanned page has no reliably readable printed English text. Upload a clearer scan.",
    ),
    "OCR_FAILED": (503, "The OCR engine could not process this page. Retry the job."),
}


def ocr_error(code):
    status, message = OCR_ERRORS.get(code, OCR_ERRORS["OCR_FAILED"])
    return fail(status, code if code in OCR_ERRORS else "OCR_FAILED", message)


def recognize_page(pdf_path, number, timeout, directory):
    helper = Path(__file__).with_name("ocr_process.py")
    environment = {
        key: os.environ[key]
        for key in ("PATH", "LANG", "LC_ALL", "TESSDATA_PREFIX")
        if key in os.environ
    }
    environment.update(OMP_THREAD_LIMIT="1", PYTHONIOENCODING="utf-8")
    with tempfile.TemporaryFile() as output:
        with subprocess.Popen(
            [
                sys.executable,
                str(helper),
                str(pdf_path),
                str(number),
                str(settings.document_ocr_max_pixels),
                str(settings.document_ocr_memory_mb),
                str(settings.document_ocr_page_timeout_seconds),
                str(os.getpid()),
            ],
            stdout=output,
            stderr=subprocess.DEVNULL,
            env=environment,
            cwd=directory,
            start_new_session=True,
        ) as process:
            try:
                process.wait(timeout=timeout)
            except subprocess.TimeoutExpired:
                raise ocr_error("OCR_TIMEOUT") from None
            finally:
                # Reap descendants even if the helper exited or was killed first.
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                process.wait()
            if process.returncode != 0:
                raise ocr_error(
                    "OCR_RESOURCE_LIMIT" if process.returncode < 0 else "OCR_FAILED"
                )
        output.seek(0)
        raw = output.read(MAX_RESULT_BYTES + 1)
    if len(raw) > MAX_RESULT_BYTES:
        raise ocr_error("OCR_RESOURCE_LIMIT")
    try:
        result = json.loads(raw)
    except (ValueError, UnicodeDecodeError):
        raise ocr_error("OCR_FAILED") from None
    if not isinstance(result, dict):
        raise ocr_error("OCR_FAILED")
    if result.get("error"):
        raise ocr_error(result["error"])
    text, blank = result.get("text"), result.get("blank")
    if (
        not isinstance(text, str)
        or len(text) > MAX_PAGE_CHARACTERS
        or type(blank) is not bool
    ):
        raise ocr_error("OCR_FAILED")
    if not blank and not text.strip():
        raise ocr_error("OCR_UNREADABLE")
    return text


def recognize_pages(content, numbers, progress=None):
    if not settings.document_ocr_enabled:
        raise fail(
            422,
            "OCR_REQUIRED",
            "This PDF needs OCR. Enable OCR on the ingestion worker and reprocess it.",
        )
    if len(numbers) > settings.document_ocr_max_pages:
        raise ocr_error("OCR_PAGE_LIMIT")
    if sys.platform != "linux" or shutil.which("tesseract") is None:
        raise ocr_error("OCR_UNAVAILABLE")
    deadline = time.monotonic() + settings.document_ocr_timeout_seconds
    results = {}
    with tempfile.TemporaryDirectory(prefix="document-ocr-") as directory:
        pdf_path = Path(directory) / "input.pdf"
        pdf_path.write_bytes(content)
        for completed, number in enumerate(numbers):
            if progress:
                progress(number, completed, len(numbers))
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise ocr_error("OCR_DOCUMENT_TIMEOUT")
            page_timeout = settings.document_ocr_page_timeout_seconds
            try:
                results[number] = recognize_page(
                    pdf_path, number, min(remaining, page_timeout), directory
                )
            except HTTPException as error:
                if (
                    error.detail.get("code") == "OCR_TIMEOUT"
                    and remaining <= page_timeout
                ):
                    raise ocr_error("OCR_DOCUMENT_TIMEOUT") from None
                raise
            if time.monotonic() > deadline:
                raise ocr_error("OCR_DOCUMENT_TIMEOUT")
            if progress:
                progress(number, completed + 1, len(numbers))
    return results
