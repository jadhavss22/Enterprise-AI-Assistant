from dataclasses import dataclass
from typing import Protocol

import tiktoken


class Tokenizer(Protocol):
    def tokenize(self, text: str) -> list[int]: ...

    def decode(self, tokens: list[int]) -> str: ...


@dataclass(frozen=True)
class TextChunk:
    chunk_index: int
    text: str
    page: int | None
    token_count: int


class Cl100kTokenizer:
    def __init__(self) -> None:
        self.encoding = tiktoken.get_encoding("cl100k_base")

    def tokenize(self, text: str) -> list[int]:
        return self.encoding.encode(text, disallowed_special=())

    def decode(self, tokens: list[int]) -> str:
        return self.encoding.decode(tokens)


def chunk_pages(
    pages: list[dict],
    chunk_size: int = 600,
    overlap: int = 100,
    tokenizer: Tokenizer | None = None,
    max_input_tokens: int | None = None,
) -> list[TextChunk]:
    if chunk_size < 1 or overlap < 0 or overlap >= chunk_size:
        raise ValueError("Chunk overlap must be smaller than chunk size")
    if max_input_tokens is not None and chunk_size > max_input_tokens:
        raise ValueError("CHUNK_SIZE_EXCEEDS_EMBEDDING_LIMIT")
    tokenizer = tokenizer or Cl100kTokenizer()
    chunks: list[TextChunk] = []
    step = chunk_size - overlap
    for page in pages:
        text = str(page.get("text", "")).strip()
        if not text:
            continue
        tokens = tokenizer.tokenize(text)
        for start in range(0, len(tokens), step):
            segment = tokens[start : start + chunk_size]
            if not segment:
                break
            chunk = tokenizer.decode(segment)
            chunks.append(
                TextChunk(
                    chunk_index=len(chunks),
                    text=chunk,
                    page=page.get("page"),
                    token_count=len(segment),
                )
            )
            if start + chunk_size >= len(tokens):
                break
    return chunks
