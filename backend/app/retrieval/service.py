import asyncio
import hashlib
import logging
import math
import time

from fastapi import HTTPException

from app.auth.models import Role
from app.auth.service import session_matches_user, unauthorized
from app.config import settings
from app.documents.access import visible_documents_query
from app.documents.service import audit_admin_private_access
from app.documents.validation import fail
from app.retrieval import lexical, quality
from app.retrieval.providers import query_embedding, search_namespaces

logger = logging.getLogger(__name__)
_search_slots = asyncio.Semaphore(2)


async def revalidate_actor(db, actor):
    user = await db.users.find_one({"_id": actor["_id"]})
    session = await db.auth_sessions.find_one({"_id": actor["_auth_session"]["_id"]})
    if (
        not session_matches_user(session, user)
        or user["token_version"] != actor["token_version"]
        or user["role"] != actor["role"]
    ):
        raise unauthorized()
    user["_auth_session"] = session
    return user


def searchable_query(actor):
    query = visible_documents_query(actor)
    query.update({"active_index_version": {"$gt": 0}, "status": {"$ne": "DELETED"}})
    return query


def value(obj, key, default=None):
    return (
        obj.get(key, default) if isinstance(obj, dict) else getattr(obj, key, default)
    )


def metadata_integer(value):
    # Pinecone metadata numbers may round-trip as JSON/protobuf doubles.
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not math.isfinite(value)
        or value != int(value)
    ):
        return None
    return int(value)


def checked_candidate(doc, match, *, require_semantic_score=True):
    metadata = value(match, "metadata", {}) or {}
    if not isinstance(metadata, dict):
        return None
    version = doc["active_index_version"]
    chunk = metadata_integer(metadata.get("chunk_index"))
    score = value(match, "score")
    text = metadata.get("text")
    if (
        metadata.get("document_id") != str(doc["_id"])
        or metadata.get("index_version") != version
    ):
        return None
    if chunk is None or chunk < 0 or chunk >= doc.get("chunk_count", 0):
        return None
    expected = hashlib.sha256(f"{doc['_id']}:{version}:{chunk}".encode()).hexdigest()
    if value(match, "id") != expected:
        return None
    if require_semantic_score and (
        not isinstance(score, (int, float))
        or not math.isfinite(score)
        or score < settings.retrieval_min_score
    ):
        return None
    if not isinstance(text, str) or not text.strip() or len(text) > 32000:
        return None
    page = metadata_integer(metadata.get("page"))
    if metadata.get("page") is not None and (page is None or page < 1):
        return None
    return {
        "document_id": str(doc["_id"]),
        "index_version": version,
        "chunk_id": expected,
        "chunk_index": chunk,
        "page": page,
        "text": text,
        "score": score,
    }


async def add_keyword_candidates(documents, index, query, semantic, request_id):
    query_phrases = lexical.phrases(query)
    if not query_phrases:
        return semantic
    try:
        matches = await lexical.fetch_active_chunks(documents, index)
    except Exception as exc:  # noqa: BLE001 - optional provider leg, log no source data
        logger.warning(
            "retrieval.keyword_unavailable type=%s request_id=%s",
            type(exc).__name__,
            request_id,
        )
        return semantic
    scored = {}
    for doc, match in matches:
        candidate = checked_candidate(doc, match, require_semantic_score=False)
        if candidate and (
            score := lexical.lexical_score(query_phrases, candidate["text"])
        ):
            scored[candidate["chunk_id"]] = (score, candidate)
    ranked = [
        item[1]
        for _, item in sorted(scored.items(), key=lambda pair: (-pair[1][0], pair[0]))
    ]
    return lexical.fuse(semantic, ranked)[: settings.retrieval_candidate_limit]


async def retrieve(db, actor, query, request_id, *, rerank=None):
    # Refuse queued API work rather than accumulating unlimited namespace searches.
    try:
        await asyncio.wait_for(_search_slots.acquire(), timeout=0.1)
    except TimeoutError:
        raise fail(503, "SEARCH_BUSY", "Search is busy; retry shortly") from None
    started = time.monotonic()
    try:
        original = await _retrieve(db, actor, query, request_id, started, rerank=rerank)
        if (
            not settings.retrieval_query_rewrite_enabled
            or rerank is not None
            or original["status"] != "NO_RELEVANT_MATCHES"
        ):
            return original
        rewritten = await quality.rewrite_search_query(query, request_id)
        if rewritten is None:
            await revalidate_actor(db, actor)
            return original
        current = await revalidate_actor(db, actor)
        try:
            async with asyncio.timeout(settings.retrieval_query_retry_timeout_seconds):
                return await _retrieve(
                    db, current, rewritten, request_id, started, rerank=rerank
                )
        except TimeoutError:
            logger.info("retrieval.query_retry_timeout request_id=%s", request_id)
        except HTTPException as exc:
            code = exc.detail.get("code") if isinstance(exc.detail, dict) else None
            if exc.status_code < 500 and code != "QUERY_TOO_LONG":
                raise
            logger.info(
                "retrieval.query_retry_fallback code=%s request_id=%s", code, request_id
            )
        await revalidate_actor(db, actor)
        return original
    finally:
        _search_slots.release()


async def _retrieve(db, actor, query, request_id, started, *, rerank=None):
    documents = (
        await db.documents.find(searchable_query(actor))
        .limit(settings.retrieval_max_documents + 1)
        .to_list(length=settings.retrieval_max_documents + 1)
    )
    if not documents:
        await revalidate_actor(db, actor)
        return {
            "status": "NO_ACCESSIBLE_DOCUMENTS",
            "passages": [],
            "request_id": request_id,
        }
    if len(documents) > settings.retrieval_max_documents:
        raise fail(
            422,
            "SEARCH_SCOPE_TOO_LARGE",
            "Too many searchable documents for this search configuration; contact your administrator",
        )
    try:
        vector = await query_embedding(query)
        matches, search_ms, index = await search_namespaces(documents, vector)
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001 - provider failures must not expose query/credentials
        logger.warning(
            "retrieval.provider_failure type=%s request_id=%s",
            type(exc).__name__,
            request_id,
        )
        raise fail(
            503,
            "SEARCH_UNAVAILABLE",
            "Document search is temporarily unavailable; retry shortly",
        ) from None
    candidates = []
    seen = set()
    for doc, match in matches:
        candidate = checked_candidate(doc, match)
        if candidate and candidate["chunk_id"] not in seen:
            seen.add(candidate["chunk_id"])
            candidates.append(candidate)
    candidates.sort(key=lambda c: (-c["score"], c["chunk_id"]))
    candidates = candidates[: settings.retrieval_candidate_limit]
    if settings.retrieval_hybrid_enabled and rerank is None:
        candidates = await add_keyword_candidates(
            documents, index, query, candidates, request_id
        )
    if rerank is not None and candidates:
        filenames = {str(doc["_id"]): doc["filename"] for doc in documents}
        candidates = await rerank(query, candidates, filenames)
    elif settings.retrieval_rerank_enabled and len(candidates) > 1:
        filenames = {str(doc["_id"]): doc["filename"] for doc in documents}
        candidates = await quality.rerank_candidates(
            query, candidates, filenames, request_id
        )
    current = await revalidate_actor(db, actor)
    latest_query = searchable_query(current)
    latest_query["_id"] = {"$in": [d["_id"] for d in documents]}
    rows = await db.documents.find(latest_query).to_list(length=len(documents))
    current_docs = {str(d["_id"]): d for d in rows}
    passages = []
    audited = set()
    for candidate in candidates:
        doc = current_docs.get(candidate["document_id"])
        if not doc or doc["active_index_version"] != candidate["index_version"]:
            continue
        if current["role"] == Role.ADMIN.value and doc["_id"] not in audited:
            await audit_admin_private_access(db, current, doc, request_id)
            audited.add(doc["_id"])
        passages.append(
            {k: v for k, v in candidate.items() if k != "score"}
            | {
                "filename": doc["filename"],
                "source_path": f"/documents/{doc['_id']}/source",
            }
        )
        if len(passages) == settings.retrieval_result_limit:
            break
    final_actor = await revalidate_actor(db, actor)
    final_query = searchable_query(final_actor)
    final_query["_id"] = {"$in": [d["_id"] for d in documents]}
    final_rows = await db.documents.find(final_query).to_list(length=len(documents))
    final_docs = {str(d["_id"]): d for d in final_rows}
    # No further awaits after this visibility/version read: audit writes must not
    # widen the window in which a concurrent revocation could pass unnoticed.
    passages = [
        p | {"filename": final_docs[p["document_id"]]["filename"]}
        for p in passages
        if p["document_id"] in final_docs
        and final_docs[p["document_id"]]["active_index_version"] == p["index_version"]
    ]
    logger.info(
        "retrieval.completed request_id=%s documents=%d candidates=%d results=%d search_ms=%d total_ms=%d",
        request_id,
        len(documents),
        len(candidates),
        len(passages),
        search_ms,
        round((time.monotonic() - started) * 1000),
    )
    return {
        "status": "MATCHES" if passages else "NO_RELEVANT_MATCHES",
        "passages": passages,
        "request_id": request_id,
    }
