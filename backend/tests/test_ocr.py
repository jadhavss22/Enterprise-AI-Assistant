import os
import shutil
import subprocess
import sys

import pymupdf
import pytest
from fastapi import HTTPException
from test_document_review_fixes import FakeProvider

from app.chat.grounding import build_grounded_messages, validate_answer_references
from app.config import settings
from app.ingestion import extraction, ocr, ocr_process
from app.ingestion.chunking import chunk_pages
from app.retrieval.service import checked_candidate


def scanned_pdf(
    *, rotated=0, native_first=False, native_header=False, unreadable=False, blank=False
):
    with pymupdf.open() as source:
        page = source.new_page(width=595, height=842)
        if unreadable:
            page.draw_rect(page.rect, color=(0, 0, 0), fill=(0, 0, 0))
        elif not blank:
            lines = [
                "CEDAR DEMO EMPLOYEE POLICY",
                "Policy reference HR-42",
                "Employees receive 20 annual leave days.",
                "Leave requests need manager approval.",
                "The IT help desk opens Monday to Friday.",
                "Support hours are 9 AM until 6 PM.",
            ] * 3
            page.insert_textbox(
                pymupdf.Rect(40, 30, 555, 812), "\n".join(lines), fontsize=17
            )
        image = page.get_pixmap(matrix=pymupdf.Matrix(2, 2), alpha=False).tobytes("png")
    with pymupdf.open() as document:
        if native_first:
            page = document.new_page()
            page.insert_text(
                (40, 60), "Native digital policy: training limit is 1200 credits."
            )
        page = document.new_page(width=595, height=842)
        if native_header:
            page.insert_text((40, 35), "Native header reference HR-99")
        page.insert_image(
            pymupdf.Rect(0, 55 if native_header else 0, 595, 842), stream=image
        )
        page.set_rotation(rotated)
        return document.tobytes()


def require_engine():
    available = sys.platform == "linux" and shutil.which("tesseract") is not None
    if not available and os.environ.get("RUN_OCR_TESTS") == "1":
        pytest.fail(
            "RUN_OCR_TESTS requires Linux and the installed English Tesseract engine"
        )
    if not available:
        pytest.skip("Real OCR fixtures run in the Linux OCR worker/CI environment")


def test_native_text_never_starts_ocr(monkeypatch):
    def unexpected(*args, **kwargs):
        pytest.fail("Native text must use the existing extraction path")

    monkeypatch.setattr(extraction, "recognize_pages", unexpected)
    with pymupdf.open() as pdf:
        pdf.new_page().insert_text((40, 60), "Native readable text remains searchable.")
        pages = extraction.extract_pages("native.pdf", pdf.tobytes())
    assert pages[0]["page"] == 1
    assert "Native readable" in pages[0]["text"]
    assert extraction.extract_pages("text.txt", b"Plain UTF-8 text") == [
        {"page": None, "text": "Plain UTF-8 text"}
    ]


@pytest.mark.parametrize("rotation", [0, 90, 180, 270])
def test_real_scanned_and_rotated_pdf(rotation):
    require_engine()
    progress = []
    pages = extraction.extract_pages(
        "scan.pdf",
        scanned_pdf(rotated=rotation),
        ocr_progress=lambda *args: progress.append(args),
    )
    assert pages[0]["page"] == 1
    assert "20 annual leave days" in pages[0]["text"], pages
    assert "HR-42" in pages[0]["text"], pages
    assert progress == [(1, 0, 1), (1, 1, 1)]


def test_real_mixed_pdf_and_page_citations():
    require_engine()
    progress = []
    pages = extraction.extract_pages(
        "mixed.pdf",
        scanned_pdf(native_first=True, native_header=True),
        ocr_progress=lambda *args: progress.append(args),
    )
    assert "1200 credits" in pages[0]["text"]
    assert "Native header reference HR-99" in pages[1]["text"]
    assert "20 annual leave days" in pages[1]["text"]
    assert progress == [(2, 0, 1), (2, 1, 1)]
    chunks = chunk_pages(pages, 80, 10, tokenizer=FakeProvider(), max_input_tokens=254)
    import hashlib

    from bson import ObjectId

    doc = {"_id": ObjectId(), "active_index_version": 1, "chunk_count": len(chunks)}
    selected = next(
        chunk
        for chunk in chunks
        if chunk.page == 2 and "20 annual leave days" in chunk.text
    )
    key = hashlib.sha256(f"{doc['_id']}:1:{selected.chunk_index}".encode()).hexdigest()
    candidate = checked_candidate(
        doc,
        {
            "id": key,
            "score": 0.9,
            "metadata": {
                "document_id": str(doc["_id"]),
                "index_version": 1,
                "chunk_index": selected.chunk_index,
                "page": selected.page,
                "text": selected.text,
            },
        },
    )
    assert candidate is not None
    passage = candidate | {
        "filename": "mixed.pdf",
        "source_path": f"/documents/{doc['_id']}/source",
    }
    _, references = build_grounded_messages("What is the leave allowance?", [passage])
    citations = validate_answer_references(
        "Employees receive 20 annual leave days [S1].", references
    )
    assert citations[0]["page"] == 2


def test_real_unreadable_scan_is_an_explicit_failure():
    require_engine()
    with pytest.raises(HTTPException) as error:
        extraction.extract_pages("unreadable.pdf", scanned_pdf(unreadable=True))
    assert error.value.detail["code"] == "OCR_UNREADABLE"


def test_real_blank_scan_does_not_discard_native_pages():
    require_engine()
    pages = extraction.extract_pages(
        "blank-back.pdf", scanned_pdf(native_first=True, blank=True)
    )
    assert "1200 credits" in pages[0]["text"]
    assert pages[1] == {"page": 2, "text": ""}


def test_real_ocr_respects_a_smaller_render_budget(monkeypatch):
    require_engine()
    monkeypatch.setattr(settings, "document_ocr_max_pixels", 1_000_000)
    pages = extraction.extract_pages("bounded.pdf", scanned_pdf())
    assert "20 annual leave days" in pages[0]["text"]


def test_low_confidence_output_is_rejected():
    tsv = "level\tblock_num\tpar_num\tline_num\tconf\ttext\n5\t1\t1\t1\t12\tgarbled\n"
    assert ocr_process.readable_text(tsv) == {"error": "OCR_UNREADABLE"}


def test_page_budget_and_disabled_mode_prevent_any_process(monkeypatch):
    monkeypatch.setattr(settings, "document_ocr_enabled", False)
    with pytest.raises(HTTPException) as error:
        ocr.recognize_pages(b"synthetic", [1])
    assert error.value.detail["code"] == "OCR_REQUIRED"
    monkeypatch.setattr(settings, "document_ocr_enabled", True)
    monkeypatch.setattr(settings, "document_ocr_max_pages", 1)
    with pytest.raises(HTTPException) as error:
        ocr.recognize_pages(b"synthetic", [1, 2])
    assert error.value.detail["code"] == "OCR_PAGE_LIMIT"


def test_document_deadline_is_enforced(monkeypatch):
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setattr(ocr.shutil, "which", lambda name: "/usr/bin/tesseract")
    moments = iter([0, 0, 121])
    monkeypatch.setattr(ocr.time, "monotonic", lambda: next(moments))
    monkeypatch.setattr(ocr, "recognize_page", lambda *args: "readable text")
    with pytest.raises(HTTPException) as error:
        ocr.recognize_pages(b"synthetic", [1])
    assert error.value.detail["code"] == "OCR_DOCUMENT_TIMEOUT"


def test_timeout_kills_the_entire_ocr_process_group(monkeypatch, tmp_path):
    waits, kills = [], []

    class Process:
        pid = 123
        returncode = 0

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def wait(self, timeout=None):
            waits.append(timeout)
            if timeout is not None:
                raise subprocess.TimeoutExpired("ocr", timeout)

    def start(*args, **kwargs):
        assert kwargs["start_new_session"] is True
        assert kwargs["stderr"] == subprocess.DEVNULL
        assert kwargs["env"]["OMP_THREAD_LIMIT"] == "1"
        assert "GEMINI_API_KEY" not in kwargs["env"]
        return Process()

    monkeypatch.setattr(ocr.subprocess, "Popen", start)
    monkeypatch.setattr(
        ocr.os, "killpg", lambda *args: kills.append(args), raising=False
    )
    monkeypatch.setattr(ocr.signal, "SIGKILL", 9, raising=False)
    with pytest.raises(HTTPException) as error:
        ocr.recognize_page(tmp_path / "input.pdf", 1, 0.1, tmp_path)
    assert error.value.detail["code"] == "OCR_TIMEOUT"
    assert kills == [(123, 9)] and waits == [0.1, None]


@pytest.mark.parametrize(
    "remaining, expected", [(5, "OCR_DOCUMENT_TIMEOUT"), (25, "OCR_TIMEOUT")]
)
def test_page_timeout_uses_the_limiting_budget(monkeypatch, remaining, expected):
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setattr(ocr.shutil, "which", lambda name: "/usr/bin/tesseract")
    monkeypatch.setattr(settings, "document_ocr_timeout_seconds", remaining)
    monkeypatch.setattr(settings, "document_ocr_page_timeout_seconds", 20)
    monkeypatch.setattr(ocr.time, "monotonic", lambda: 0)

    def timeout(*args):
        assert args[2] == min(remaining, 20)
        raise ocr.ocr_error("OCR_TIMEOUT")

    monkeypatch.setattr(ocr, "recognize_page", timeout)
    with pytest.raises(HTTPException) as error:
        ocr.recognize_pages(b"synthetic", [1])
    assert error.value.detail["code"] == expected


def test_abnormal_helper_exit_kills_remaining_process_group(monkeypatch, tmp_path):
    kills, waits = [], []

    class Process:
        pid = 124
        returncode = -9

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def wait(self, timeout=None):
            waits.append(timeout)

    monkeypatch.setattr(ocr.subprocess, "Popen", lambda *args, **kwargs: Process())
    monkeypatch.setattr(
        ocr.os, "killpg", lambda *args: kills.append(args), raising=False
    )
    monkeypatch.setattr(ocr.signal, "SIGKILL", 9, raising=False)
    with pytest.raises(HTTPException) as error:
        ocr.recognize_page(tmp_path / "input.pdf", 1, 0.1, tmp_path)
    assert error.value.detail["code"] == "OCR_RESOURCE_LIMIT"
    assert kills == [(124, 9)]
    assert waits == [0.1, None]


@pytest.mark.parametrize("code", ["INGESTION_LEASE_LOST", "DOCUMENT_STATE_CHANGED"])
def test_extraction_preserves_progress_cancellation(monkeypatch, code):
    def recognize(content, numbers, progress):
        progress(numbers[0], 0, len(numbers))

    def cancelled(*args):
        raise RuntimeError(code)

    monkeypatch.setattr(extraction, "recognize_pages", recognize)
    with pytest.raises(RuntimeError, match=code):
        extraction.extract_pages("scan.pdf", scanned_pdf(), ocr_progress=cancelled)
