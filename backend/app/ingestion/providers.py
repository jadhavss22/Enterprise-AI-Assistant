from __future__ import annotations

from threading import Lock
from typing import Protocol

import tiktoken
from openai import OpenAI
from pinecone import Pinecone

from app.config import settings


class ProviderConfigurationError(RuntimeError):
    pass


class EmbeddingProvider(Protocol):
    dimension: int
    max_input_tokens: int
    batch_size: int

    def tokenize(self, text: str) -> list[int]: ...

    def decode(self, tokens: list[int]) -> str: ...

    def embed(self, texts: list[str]) -> list[list[float]]: ...


class AzureOpenAIEmbeddingProvider:
    def __init__(self) -> None:
        if not (
            settings.azure_openai_endpoint
            and settings.azure_openai_embedding_deployment
            and settings.azure_openai_api_key
        ):
            raise ProviderConfigurationError("INDEX_PROVIDER_UNCONFIGURED")
        base_url = settings.azure_openai_endpoint.rstrip("/")
        if not base_url.endswith("/openai/v1"):
            base_url += "/openai/v1"
        self.client = OpenAI(
            api_key=settings.azure_openai_api_key,
            base_url=base_url + "/",
            timeout=60.0,
            max_retries=2,
        )
        self.encoding = tiktoken.get_encoding("cl100k_base")
        self.dimension = settings.azure_openai_embedding_dimensions
        self.max_input_tokens = settings.azure_openai_embedding_max_input_tokens
        self.batch_size = settings.local_embedding_batch_size

    def tokenize(self, text: str) -> list[int]:
        return self.encoding.encode(text, disallowed_special=())

    def decode(self, tokens: list[int]) -> str:
        return self.encoding.decode(tokens)

    def embed(self, texts: list[str]) -> list[list[float]]:
        response = self.client.embeddings.create(
            model=settings.azure_openai_embedding_deployment,
            input=texts,
            dimensions=self.dimension,
        )
        return [item.embedding for item in response.data]


_local_model = None
_local_model_lock = Lock()


def _load_local_model():
    global _local_model
    if _local_model is None:
        with _local_model_lock:
            if _local_model is None:
                from sentence_transformers import SentenceTransformer

                _local_model = SentenceTransformer(
                    settings.local_embedding_model,
                    device="cpu",
                )
                dimension_reader = getattr(
                    _local_model,
                    "get_embedding_dimension",
                    _local_model.get_sentence_embedding_dimension,
                )
                actual_dimension = dimension_reader()
                if actual_dimension != settings.local_embedding_dimensions:
                    raise ProviderConfigurationError(
                        "LOCAL_EMBEDDING_DIMENSION_MISMATCH"
                    )
    return _local_model


class LocalSentenceTransformerProvider:
    def __init__(self) -> None:
        self.model = _load_local_model()
        self.tokenizer = self.model.tokenizer
        model_limit = int(self.model.max_seq_length)
        tokenizer_limit = int(getattr(self.tokenizer, "model_max_length", model_limit))
        if tokenizer_limit < 1_000_000:
            model_limit = min(model_limit, tokenizer_limit)
        # Sentence-transformers adds special tokens around content tokens.
        self.max_input_tokens = max(1, model_limit - 2)
        self.dimension = settings.local_embedding_dimensions
        self.batch_size = settings.local_embedding_batch_size

    def tokenize(self, text: str) -> list[int]:
        return self.tokenizer.encode(
            text,
            add_special_tokens=False,
            truncation=False,
            verbose=False,
        )

    def decode(self, tokens: list[int]) -> str:
        return self.tokenizer.decode(
            tokens,
            skip_special_tokens=False,
            clean_up_tokenization_spaces=False,
        )

    def embed(self, texts: list[str]) -> list[list[float]]:
        vectors = self.model.encode(
            texts,
            batch_size=self.batch_size,
            convert_to_numpy=True,
            normalize_embeddings=False,
            show_progress_bar=False,
        )
        return vectors.tolist()


def embedding_provider() -> EmbeddingProvider:
    if settings.embedding_provider == "local":
        return LocalSentenceTransformerProvider()
    if settings.embedding_provider == "azure_openai":
        return AzureOpenAIEmbeddingProvider()
    raise ProviderConfigurationError("UNKNOWN_EMBEDDING_PROVIDER")


def pinecone_index():
    if not settings.pinecone_api_key or not settings.pinecone_index_name:
        raise ProviderConfigurationError("INDEX_PROVIDER_UNCONFIGURED")
    client = Pinecone(api_key=settings.pinecone_api_key)
    return client.index(settings.pinecone_index_name)
