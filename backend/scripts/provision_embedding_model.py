"""Explicit one-time provisioning, outside a user query or readiness request.

Inside worker: HF_HUB_OFFLINE=0 LOCAL_EMBEDDING_CACHE_ONLY=false python -m scripts.provision_embedding_model
Then restart the worker with cache-only loading enabled.
"""

from app.config import settings
from app.ingestion.providers import embedding_provider

if __name__ == "__main__":
    if settings.embedding_provider != "local":
        raise SystemExit("This provisioning command is for the local embedding provider only")
    if settings.local_embedding_cache_only:
        raise SystemExit(
            "Set LOCAL_EMBEDDING_CACHE_ONLY=false and HF_HUB_OFFLINE=0 explicitly for provisioning"
        )
    provider = embedding_provider()
    print({"provisioned": True, "dimensions": provider.dimension})
