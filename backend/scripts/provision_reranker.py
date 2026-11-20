"""Explicit model download. Run with HF_HUB_OFFLINE=0, then restart the worker."""

from app.retrieval.reranker import MODEL, REVISION, load_model

if __name__ == "__main__":
    load_model(local_files_only=False)
    print({"provisioned": True, "model": MODEL, "revision": REVISION})
