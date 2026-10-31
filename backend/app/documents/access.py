from bson import ObjectId

from app.auth.models import Role
from app.documents.models import DocumentVisibility
from app.documents.validation import fail


def visible_documents_query(actor: dict) -> dict:
    query = {"deleted_at": {"$exists": False}}
    if actor["role"] == Role.ADMIN.value:
        return query
    user_id = actor["_id"]
    department_id = actor["department_id"]
    query["$or"] = [
        {"visibility": DocumentVisibility.ORGANIZATION.value},
        {
            "visibility": DocumentVisibility.DEPARTMENT.value,
            "department_id": department_id,
        },
        {"visibility": DocumentVisibility.PRIVATE.value, "uploaded_by": user_id},
        {
            "visibility": DocumentVisibility.PRIVATE.value,
            "department_id": department_id,
            "allowed_user_ids": user_id,
        },
    ]
    return query


def can_manage_document(actor: dict, document: dict) -> bool:
    return actor["role"] == Role.ADMIN.value or (
        actor["role"] == Role.MANAGER.value
        and document.get("uploaded_by") == actor["_id"]
    )


def require_visible_document(actor: dict, document: dict | None) -> dict:
    if document is None or document.get("deleted_at") is not None:
        raise fail(404, "DOCUMENT_NOT_FOUND", "Document was not found")
    if actor["role"] == Role.ADMIN.value:
        return document
    if document["visibility"] == DocumentVisibility.ORGANIZATION.value:
        return document
    if document["visibility"] == DocumentVisibility.DEPARTMENT.value and document.get(
        "department_id"
    ) == actor.get("department_id"):
        return document
    if document["visibility"] == DocumentVisibility.PRIVATE.value:
        if document.get("uploaded_by") == actor["_id"]:
            return document
        if document.get("department_id") == actor.get("department_id") and actor[
            "_id"
        ] in document.get("allowed_user_ids", []):
            return document
    raise fail(404, "DOCUMENT_NOT_FOUND", "Document was not found")


def document_object_id(value: str) -> ObjectId:
    if not ObjectId.is_valid(value):
        raise fail(422, "DOCUMENT_ID_INVALID", "Invalid document ID")
    return ObjectId(value)
