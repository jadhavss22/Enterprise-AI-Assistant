"""Warm and attest the model in each Celery execution child, not its parent."""

import hashlib
import json
import logging
import math
import os
import socket
import threading

from celery.signals import worker_process_init, worker_process_shutdown
from redis import Redis

from app.config import settings
from app.ingestion.providers import ProviderConfigurationError, embedding_provider

logger = logging.getLogger(__name__)
_state = "warming"
_error = None
_provider = None
_stop = threading.Event()
_status_lock = threading.Lock()


def model_prefix():
    profile = [
        settings.embedding_provider,
        settings.local_embedding_model,
        settings.local_embedding_dimensions,
        settings.local_embedding_cache_only,
        settings.azure_openai_endpoint,
        settings.azure_openai_embedding_deployment,
        settings.azure_openai_embedding_dimensions,
    ]
    digest = hashlib.sha256(json.dumps(profile).encode()).hexdigest()[:24]
    return "retrieval:model:" + digest + ":"


def model_key():
    return model_prefix() + socket.gethostname() + ":" + str(os.getpid())


def runtime_state():
    with _status_lock:
        return {"state": _state, "error": _error}


def ready_provider():
    with _status_lock:
        if _state != "ready" or _provider is None:
            raise ProviderConfigurationError(_error or "MODEL_WARMING")
        return _provider


def warm_model():
    global _state, _error, _provider
    try:
        provider = embedding_provider()
        vectors = provider.embed(["model initialization check"])
        if (
            len(vectors) != 1
            or len(vectors[0]) != provider.dimension
            or not all(math.isfinite(v) for v in vectors[0])
        ):
            raise ProviderConfigurationError("EMBEDDING_INVALID")
        with _status_lock:
            _provider, _state, _error = provider, "ready", None
        logger.info("retrieval.model_ready dimension=%d", provider.dimension)
    except Exception as exc:  # noqa: BLE001 - model initialization must not log private provider details
        safe_codes = {
            "MODEL_CACHE_UNAVAILABLE",
            "INDEX_PROVIDER_UNCONFIGURED",
            "LOCAL_EMBEDDING_DIMENSION_MISMATCH",
            "EMBEDDING_INVALID",
            "UNKNOWN_EMBEDDING_PROVIDER",
        }
        code = (
            str(exc)
            if isinstance(exc, ProviderConfigurationError) and str(exc) in safe_codes
            else "MODEL_INIT_FAILED"
        )
        with _status_lock:
            _state, _error = "failed", code
        logger.warning("retrieval.model_initialization_failed code=%s", code)


def heartbeat():
    client = Redis.from_url(
        settings.redis_url, socket_connect_timeout=1, socket_timeout=1
    )
    try:
        while not _stop.is_set():
            try:
                client.set(model_key(), json.dumps(runtime_state()), ex=15)
            except Exception:  # noqa: BLE001 - expiration makes unavailable Redis fail closed
                logger.warning("retrieval.model_heartbeat_unavailable")
            _stop.wait(3)
    finally:
        try:
            client.delete(model_key())
        except Exception:  # noqa: BLE001 - key also expires after a killed execution process
            logger.warning("retrieval.model_heartbeat_cleanup_unavailable")
        client.close()


@worker_process_init.connect(weak=False)
def start_child_model(**kwargs):
    global _stop, _state, _error, _provider
    _stop = threading.Event()
    _state, _error, _provider = "warming", None, None
    # Celery's init signal must return quickly; model initialization runs in the
    # execution child on a background thread, while query tasks are gated.
    threading.Thread(target=heartbeat, name="model-heartbeat", daemon=True).start()
    threading.Thread(target=warm_model, name="model-warmup", daemon=True).start()


@worker_process_shutdown.connect(weak=False)
def stop_child_model(**kwargs):
    _stop.set()
