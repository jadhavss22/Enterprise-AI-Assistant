"""Private OCR child. Invoked in a new process group by ingestion.ocr."""

import csv
import ctypes
import io
import json
import math
import os
import signal
import subprocess
import sys
from pathlib import Path

MAX_TSV_BYTES = 4_000_000
MAX_TEXT_CHARACTERS = 50_000


def kill_group(*_args):
    if os.getpgrp() == os.getpid():
        os.killpg(os.getpid(), signal.SIGKILL)
    os._exit(1)


def readable_text(tsv):
    rows = csv.DictReader(io.StringIO(tsv), delimiter="\t")
    lines, confidences = {}, []
    for row in rows:
        text = row.get("text", "").strip()
        if row.get("level") != "5" or not text:
            continue
        confidence = float(row["conf"])
        if not math.isfinite(confidence) or confidence < 0:
            continue
        key = (row["block_num"], row["par_num"], row["line_num"])
        lines.setdefault(key, []).append(text)
        confidences.extend([confidence] * min(len(text), 100))
    text = "\n".join(" ".join(words) for words in lines.values())
    if (
        sum(character.isalnum() for character in text) < 3
        or not confidences
        or sum(confidences) / len(confidences) < 60
    ):
        return {"error": "OCR_UNREADABLE"}
    if len(text) > MAX_TEXT_CHARACTERS:
        return {"error": "OCR_RESOURCE_LIMIT"}
    return {"text": text, "blank": False}


def run():
    import resource

    pdf_path, number, max_pixels, memory_mb, cpu_seconds, parent_pid = sys.argv[1:]
    max_pixels, memory_mb, cpu_seconds = (
        int(max_pixels),
        int(memory_mb),
        int(cpu_seconds),
    )
    if os.getpgrp() != os.getpid():
        return {"error": "OCR_UNAVAILABLE"}
    signal.signal(signal.SIGTERM, kill_group)
    libc = ctypes.CDLL(None, use_errno=True)
    if libc.prctl(1, signal.SIGTERM) != 0:
        return {"error": "OCR_UNAVAILABLE"}
    if os.getppid() != int(parent_pid):
        kill_group()
    resource.setrlimit(resource.RLIMIT_AS, (memory_mb * 1024 * 1024,) * 2)
    resource.setrlimit(resource.RLIMIT_CPU, (cpu_seconds + 1,) * 2)
    resource.setrlimit(resource.RLIMIT_FSIZE, (16 * 1024 * 1024,) * 2)
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))

    import pymupdf

    with pymupdf.open(pdf_path) as pdf:
        page = pdf[int(number) - 1]
        width, height = page.rect.width, page.rect.height
        if not all(math.isfinite(value) and value > 0 for value in (width, height)):
            return {"error": "OCR_RESOURCE_LIMIT"}
        scale = (
            min(
                200 / 72,
                math.sqrt(max_pixels / (width * height)),
                6000 / max(width, height),
            )
            * 0.995
        )
        pixmap = page.get_pixmap(
            matrix=pymupdf.Matrix(scale, scale), colorspace=pymupdf.csGRAY, alpha=False
        )
        if pixmap.width * pixmap.height > max_pixels:
            return {"error": "OCR_RESOURCE_LIMIT"}
        if min(pixmap.samples) >= 245:
            return {"text": "", "blank": True}
        image_path = Path("page.png")
        pixmap.save(image_path)
    with open("recognition.tsv", "w+b") as output:
        subprocess.run(
            [
                "tesseract",
                str(image_path),
                "stdout",
                "-l",
                "eng",
                "--dpi",
                "200",
                "--psm",
                "1",
                "tsv",
            ],
            stdout=output,
            stderr=subprocess.DEVNULL,
            check=True,
        )
        output.seek(0)
        raw = output.read(MAX_TSV_BYTES + 1)
    if len(raw) > MAX_TSV_BYTES:
        return {"error": "OCR_RESOURCE_LIMIT"}
    return readable_text(raw.decode("utf-8"))


if __name__ == "__main__":
    try:
        result = run()
    except FileNotFoundError:
        result = {"error": "OCR_UNAVAILABLE"}
    except subprocess.CalledProcessError as error:
        result = {
            "error": "OCR_RESOURCE_LIMIT" if error.returncode < 0 else "OCR_FAILED"
        }
    except MemoryError:
        result = {"error": "OCR_RESOURCE_LIMIT"}
    except Exception:  # noqa: BLE001 - private child emits fixed error codes only
        result = {"error": "OCR_FAILED"}
    print(json.dumps(result, ensure_ascii=True))
