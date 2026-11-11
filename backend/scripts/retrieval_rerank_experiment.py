"""Compare live semantic retrieval with a local cross-encoder on synthetic data.

Run in the API container with the synthetic dataset path as the argument.
The model may download weights on first use. Inference runs locally.
"""

import argparse
import asyncio
import math
from functools import partial
from unittest.mock import patch

from app.config import settings
from app.retrieval import routes, service
from scripts.retrieval_live_check import main

MODEL = "cross-encoder/ms-marco-MiniLM-L6-v2"
REVISION = "ce0834f22110de6d9222af7a7a03628121708969"


async def compare(dataset, min_logit, *, fallback=False):
    from sentence_transformers import CrossEncoder
    from torch import nn

    model = CrossEncoder(
        MODEL,
        revision=REVISION,
        device="cpu",
        max_length=384,
        trust_remote_code=False,
        model_kwargs={"use_safetensors": True},
    )

    async def rerank(query, candidates, filenames):
        if fallback:
            # Both cutoffs use the same vector query and ordered candidate list.
            # Keep its original order when any candidate meets the baseline cutoff.
            baseline = [item for item in candidates if item["score"] >= 0.25]
            if baseline:
                return baseline
        pairs = [
            (query, f"{filenames[item['document_id']]}: {item['text']}")
            for item in candidates
        ]
        scores = await asyncio.to_thread(
            model.predict,
            pairs,
            batch_size=8,
            activation_fn=nn.Identity(),
            show_progress_bar=False,
        )
        ranked = sorted(
            zip(scores, candidates, strict=True),
            key=lambda pair: (-float(pair[0]), pair[1]["chunk_id"]),
        )
        return [
            item for score, item in ranked
            if math.isfinite(float(score)) and score >= min_logit
        ]

    print(f"Model: {MODEL}@{REVISION}; minimum relevance logit: {min_logit}", flush=True)
    with patch.object(settings, "retrieval_rate_limit", 30):
        print("Semantic baseline", flush=True)
        with patch.object(settings, "retrieval_min_score", 0.25):
            await main(dataset)
        print(
            "Semantic retrieval with reranking fallback" if fallback
            else "Broader retrieval with reranking",
            flush=True,
        )
        with (
            patch.object(settings, "retrieval_min_score", 0.20),
            patch.object(routes, "retrieve", partial(service.retrieve, rerank=rerank)),
        ):
            await main(dataset)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--dataset", required=True)
    parser.add_argument("--min-logit", type=float, default=0.0)
    parser.add_argument("--fallback", action="store_true")
    args = parser.parse_args()
    if not math.isfinite(args.min_logit):
        parser.error("--min-logit must be finite")
    asyncio.run(compare(args.dataset, args.min_logit, fallback=args.fallback))
