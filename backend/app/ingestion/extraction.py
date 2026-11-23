from io import BytesIO
from zipfile import BadZipFile

import pymupdf
from docx import Document as WordDocument
from fastapi import HTTPException

from app.documents.validation import fail
from app.ingestion.ocr import recognize_pages

MAX_PDF_PAGES = 2_000
MAX_EXTRACTED_CHARACTERS = 10_000_000


def extract_pages(filename: str, content: bytes, *, ocr_progress=None) -> list[dict]:
    suffix = filename.rsplit(".", 1)[-1].lower()
    try:
        if suffix == "pdf":
            with pymupdf.open(stream=content, filetype="pdf") as pdf:
                if pdf.is_encrypted:
                    raise fail(
                        422,
                        "PDF_ENCRYPTED",
                        "Password-protected PDFs are not supported",
                    )
                if pdf.page_count > MAX_PDF_PAGES:
                    raise fail(
                        422, "PDF_PAGE_LIMIT", "The PDF has too many pages to process"
                    )
                pages, scanned = [], []
                for number, page in enumerate(pdf, start=1):
                    text = page.get_text("text", sort=True).strip()
                    pages.append({"page": number, "text": text})
                    area = page.rect.width * page.rect.height
                    image_area = sum(
                        max(0, info["bbox"][2] - info["bbox"][0])
                        * max(0, info["bbox"][3] - info["bbox"][1])
                        for info in page.get_image_info()
                    )
                    substantial_image = area > 0 and image_area >= area * 0.25
                    if (
                        substantial_image
                        and sum(character.isalnum() for character in text) < 200
                    ) or (not text and page.get_contents()):
                        scanned.append(number)
                if scanned:
                    recognized = recognize_pages(content, scanned, ocr_progress)
                    for item in pages:
                        if item["page"] in recognized:
                            # Preserve native text on mixed pages and avoid exact repeated lines.
                            native = {
                                " ".join(line.casefold().split())
                                for line in item["text"].splitlines()
                            }
                            extra = [
                                line
                                for line in recognized[item["page"]].splitlines()
                                if " ".join(line.casefold().split()) not in native
                            ]
                            item["text"] = "\n".join(
                                filter(None, [item["text"], *extra])
                            )
        elif suffix == "docx":
            word = WordDocument(BytesIO(content))
            text = [
                paragraph.text.strip()
                for paragraph in word.paragraphs
                if paragraph.text.strip()
            ]
            for table in word.tables:
                for row in table.rows:
                    cells = [
                        cell.text.strip() for cell in row.cells if cell.text.strip()
                    ]
                    if cells:
                        text.append(" | ".join(cells))
            pages = [{"page": None, "text": "\n".join(text)}]
        elif suffix == "txt":
            pages = [
                {
                    "page": None,
                    "text": content.decode("utf-8-sig", errors="strict").strip(),
                }
            ]
        else:
            raise fail(
                415,
                "FILE_TYPE_UNSUPPORTED",
                "Only PDF, DOCX, and TXT files are supported",
            )
    except HTTPException:
        raise
    except (
        pymupdf.FileDataError,
        pymupdf.EmptyFileError,
        BadZipFile,
        ValueError,
        RuntimeError,
    ) as error:
        if isinstance(error, RuntimeError) and str(error) in {
            "INGESTION_LEASE_LOST",
            "DOCUMENT_STATE_CHANGED",
        }:
            raise
        raise fail(
            422, "DOCUMENT_PARSE_FAILED", "The document could not be safely parsed"
        ) from None
    total = sum(len(item["text"]) for item in pages)
    if total > MAX_EXTRACTED_CHARACTERS:
        raise fail(
            422,
            "EXTRACTED_TEXT_LIMIT",
            "The extracted document text exceeds the processing limit",
        )
    if not any(item["text"] for item in pages):
        raise fail(
            422, "DOCUMENT_TEXT_EMPTY", "No readable text was found in the document"
        )
    return pages
