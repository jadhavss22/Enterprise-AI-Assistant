from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator


class ConversationWrite(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    title: str = Field(default="New conversation", min_length=1, max_length=100)


class ChatRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    question: str = Field(min_length=1, max_length=2000)
    client_request_id: UUID

    @field_validator("question")
    @classmethod
    def no_blank(cls, value):
        if not value.strip():
            raise ValueError("Question must not be blank")
        return value
