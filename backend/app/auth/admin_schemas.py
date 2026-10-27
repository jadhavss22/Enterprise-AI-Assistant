from bson import ObjectId
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.auth.models import Role, UserStatus
from app.auth.schemas import UserCredentials, UserPublic


def object_id_string(value):
    if not ObjectId.is_valid(value):
        raise ValueError("A valid resource ID is required")
    return str(ObjectId(value))


class CreateUserRequest(UserCredentials):
    role: Role = Role.EMPLOYEE
    department_id: str

    @field_validator("department_id")
    @classmethod
    def valid_department(cls, value):
        return object_id_string(value)


class UpdateUserRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)
    display_name: str | None = None
    role: Role | None = None
    status: UserStatus | None = None
    department_id: str | None = None

    @field_validator("display_name")
    @classmethod
    def valid_name(cls, value):
        if value is None:
            raise ValueError("Name cannot be null")
        value = value.strip()
        if not 1 <= len(value) <= 120:
            raise ValueError("Name must contain 1-120 characters")
        return value

    @field_validator("department_id")
    @classmethod
    def valid_department(cls, value):
        return object_id_string(value)

    @model_validator(mode="after")
    def nonempty_update(self):
        if not self.model_fields_set or any(
            getattr(self, key) is None for key in self.model_fields_set
        ):
            raise ValueError("Provide at least one non-null supported field")
        return self


class UsersPage(BaseModel):
    users: list[UserPublic]
    next_cursor: str | None = None


class DepartmentPublic(BaseModel):
    department_id: str
    code: str
    name: str


class DepartmentsPage(BaseModel):
    departments: list[DepartmentPublic] = Field(default_factory=list)
