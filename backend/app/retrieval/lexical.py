"""Exact phrase and identifier retrieval for small authorized document sets."""

import asyncio
import hashlib
import re

from app.config import settings
from app.retrieval.pool import ProviderPool

MAX_CHUNKS = 64
# Optional fetches can outlive cancellation. Keep their permits off the required
# provider pool used by semantic search and query embedding.
keyword_pool = ProviderPool(settings.retrieval_search_concurrency)
STOP_WORDS = frozenset(
    [
        "a",
        "an",
        "and",
        "are",
        "as",
        "at",
        "be",
        "by",
        "can",
        "do",
        "does",
        "for",
        "from",
        "how",
        "i",
        "in",
        "is",
        "it",
        "me",
        "my",
        "of",
        "on",
        "or",
        "our",
        "please",
        "so",
        "that",
        "the",
        "their",
        "this",
        "to",
        "was",
        "were",
        "what",
        "when",
        "where",
        "which",
        "who",
        "why",
        "will",
        "with",
        "would",
        "you",
        "your",
    ]
)
TOKEN = re.compile(r"[^\W_]+(?:[-_][^\W_]+)*", re.UNICODE)


def terms(text):
    return tuple(
        word for word in TOKEN.findall(text.casefold()) if word not in STOP_WORDS
    )


def phrases(query):
    words = terms(query)
    return {
        words[start : start + length]
        for length in range(2, min(5, len(words)) + 1)
        for start in range(len(words) - length + 1)
        if len(set(words[start : start + length])) > 1
    } | {
        (word,)
        for word in words
        if any(ch.isdigit() for ch in word) and any(ch.isalpha() for ch in word)
    }


def lexical_score(query_phrases, text):
    words = terms(text)
    sizes = {len(phrase) for phrase in query_phrases}
    return max(
        (
            size
            for size in sizes
            for start in range(len(words) - size + 1)
            if words[start : start + size] in query_phrases
        ),
        default=0,
    )


def fuse(semantic, lexical):
    candidates = {}
    weights = {}
    for ranking in (semantic, lexical):
        for rank, candidate in enumerate(ranking, 1):
            key = candidate["chunk_id"]
            candidates.setdefault(key, candidate)
            weights[key] = weights.get(key, 0) + 1 / (60 + rank)
    return [
        candidates[key] for key in sorted(weights, key=lambda key: (-weights[key], key))
    ]


async def fetch_active_chunks(documents, index):
    """Bound the transferred vectors and skip the entire lexical pass over budget."""
    counts = [doc.get("chunk_count") for doc in documents]
    if (
        not counts
        or any(type(count) is not int or count < 1 for count in counts)
        or sum(counts) > MAX_CHUNKS
    ):
        return []
    loop = asyncio.get_running_loop()
    deadline = loop.time() + min(4, settings.retrieval_search_timeout_seconds)
    async with asyncio.timeout_at(deadline):
        budget = asyncio.Semaphore(settings.retrieval_search_concurrency)

        async def fetch(doc, count):
            version = doc["active_index_version"]
            namespace = f"{doc['_id']}-v{version}"
            ids = [
                hashlib.sha256(f"{doc['_id']}:{version}:{chunk}".encode()).hexdigest()
                for chunk in range(count)
            ]
            async with budget:
                remaining = deadline - loop.time()
                if remaining <= 0:
                    raise TimeoutError
                result = await keyword_pool.call(
                    index.fetch,
                    ids=ids,
                    namespace=namespace,
                    timeout=min(
                        remaining, settings.retrieval_namespace_timeout_seconds
                    ),
                )
            vectors = result["vectors"] if isinstance(result, dict) else result.vectors
            return [(doc, vectors[key]) for key in ids if key in vectors]

        tasks = [
            asyncio.create_task(fetch(doc, count))
            for doc, count in zip(documents, counts, strict=True)
        ]
        try:
            return [item for batch in await asyncio.gather(*tasks) for item in batch]
        finally:
            for task in tasks:
                if not task.done():
                    task.cancel()
