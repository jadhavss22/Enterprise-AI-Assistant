import pytest

from app.ingestion import providers
from app.ingestion.chunking import chunk_pages


class FakeTokenizer:
    model_max_length = 512

    def encode(self, text, add_special_tokens=False, truncation=False, verbose=True):
        del add_special_tokens, truncation, verbose
        return text.split()

    def decode(self, tokens, **kwargs):
        del kwargs
        return " ".join(tokens)


class FakeVectors:
    def tolist(self):
        return [[0.0] * 384 for _ in range(2)]


class FakeModel:
    max_seq_length = 256
    tokenizer = FakeTokenizer()

    def get_sentence_embedding_dimension(self):
        return 384

    def get_embedding_dimension(self):
        return 384

    def encode(self, texts, **kwargs):
        assert kwargs["batch_size"] == 8
        return FakeVectors()


def test_chunking_rejects_chunks_above_provider_limit():
    with pytest.raises(ValueError, match="CHUNK_SIZE_EXCEEDS_EMBEDDING_LIMIT"):
        chunk_pages(
            [{"text": "one two three", "page": 1}],
            chunk_size=255,
            overlap=20,
            tokenizer=FakeTokenizer(),
            max_input_tokens=254,
        )


def test_local_provider_uses_model_limit_and_384_dimensions(monkeypatch):
    monkeypatch.setattr(providers, "_load_local_model", lambda: FakeModel())
    provider = providers.LocalSentenceTransformerProvider()

    assert provider.max_input_tokens == 254
    assert provider.dimension == 384
    assert len(provider.embed(["first", "second"])[0]) == 384
