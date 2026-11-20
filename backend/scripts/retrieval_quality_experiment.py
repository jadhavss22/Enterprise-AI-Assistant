"""Compare configured quality stages against the existing synthetic baseline."""

import argparse
import asyncio
import io
import json
import logging
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

from app.config import settings
from app.retrieval import quality
from scripts.retrieval_live_check import main as live_check


class StageCounts(logging.Handler):
    def __init__(self):
        super().__init__()
        self.counts = {
            "rerank_completed": 0,
            "rerank_fallback": 0,
            "rewrite_completed": 0,
            "rewrite_accepted": 0,
            "rewrite_fallback": 0,
        }

    def emit(self, record):
        if record.msg.startswith("retrieval.rerank_completed"):
            self.counts["rerank_completed"] += 1
        elif record.msg.startswith("retrieval.rerank_fallback"):
            self.counts["rerank_fallback"] += 1
        elif record.msg.startswith("retrieval.rewrite_completed"):
            self.counts["rewrite_completed"] += 1
            self.counts["rewrite_accepted"] += int(record.args[0])
        elif record.msg.startswith("retrieval.rewrite_fallback"):
            self.counts["rewrite_fallback"] += 1


async def compare(dataset, output):
    results = {"dataset": Path(dataset).name, "profiles": {}}
    failed = False
    for name, rerank, rewrite in (
        ("baseline", False, False),
        ("reranking", True, False),
        ("rewriting", False, True),
        ("combined", True, True),
    ):
        buffer = io.StringIO()
        counts = StageCounts()
        previous_level = quality.logger.level
        quality.logger.addHandler(counts)
        quality.logger.setLevel(logging.INFO)
        error = None
        try:
            with (
                patch.object(settings, "retrieval_rerank_enabled", rerank),
                patch.object(settings, "retrieval_query_rewrite_enabled", rewrite),
                patch.object(settings, "retrieval_rate_limit", 120),
                redirect_stdout(buffer),
            ):
                await live_check(dataset)
        except Exception as exc:  # noqa: BLE001 - record no provider credentials/bodies
            error = type(exc).__name__
        finally:
            quality.logger.removeHandler(counts)
            quality.logger.setLevel(previous_level)
        try:
            result = json.loads(buffer.getvalue())
        except ValueError:
            result = {}
        result["stages"] = counts.counts
        if rerank and not counts.counts["rerank_completed"]:
            error = error or "RerankerNotExercised"
        if rewrite and not counts.counts["rewrite_completed"]:
            error = error or "RewriterNotExercised"
        if error:
            failed = True
            result["error"] = error
        results["profiles"][name] = result
    Path(output).write_text(json.dumps(results, indent=2) + "\n", encoding="utf-8")
    if failed:
        raise SystemExit(
            "Some quality stages failed or were not exercised; inspect the output."
        )


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--dataset", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    asyncio.run(compare(args.dataset, args.output))
