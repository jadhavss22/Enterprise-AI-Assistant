from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


class SearchRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    query: str = Field(min_length=1, max_length=2000, repr=False)

    @field_validator("query")
    @classmethod
    def nonempty(cls, value):
        value = value.strip()
        if not value:
            raise ValueError("Enter a question")
        return value


class Passage(BaseModel):
    document_id: str
    filename: str
    index_version: int
    chunk_id: str
    chunk_index: int
    page: int | None = None
    text: str
    source_path: str


class SearchResponse(BaseModel):
    status: Literal["MATCHES", "NO_ACCESSIBLE_DOCUMENTS", "NO_RELEVANT_MATCHES"]
    passages: list[Passage]
    request_id: str
