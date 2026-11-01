"""Bound upload bodies before FastAPI's multipart parser can spool them."""

from uuid import uuid4

from fastapi import HTTPException
from starlette.responses import JSONResponse

from app.documents.validation import MAX_UPLOAD_BYTES

# Includes the file plus multipart headers and small form fields. The separate
# file validator still enforces the exact 20 MiB file limit.
MAX_UPLOAD_BODY_BYTES = MAX_UPLOAD_BYTES + 64 * 1024


class UploadBodyLimitMiddleware:
    def __init__(
        self,
        app,
        max_body_bytes=MAX_UPLOAD_BODY_BYTES,
        path="/api/v1/documents",
        error_code="FILE_TOO_LARGE",
        error_message="Files must be 20 MB or smaller",
    ):
        self.app = app
        self.max_body_bytes = max_body_bytes
        self.path = path
        self.error_code = error_code
        self.error_message = error_message

    async def __call__(self, scope, receive, send):
        if (
            scope["type"] != "http"
            or scope["method"] != "POST"
            or scope["path"].rstrip("/") != self.path
        ):
            return await self.app(scope, receive, send)

        lengths = [
            value
            for name, value in scope.get("headers", [])
            if name.lower() == b"content-length"
        ]
        if lengths:
            try:
                if len(lengths) != 1 or not lengths[0].isdigit():
                    raise ValueError
                length = int(lengths[0])
            except ValueError:
                return await self.reject(
                    scope,
                    receive,
                    send,
                    400,
                    "UPLOAD_LENGTH_INVALID",
                    "Invalid request length",
                )
            if length > self.max_body_bytes:
                return await self.reject(
                    scope,
                    receive,
                    send,
                    413,
                    self.error_code,
                    self.error_message,
                )

        consumed = 0
        exceeded = False
        rejected = False

        async def bounded_receive():
            nonlocal consumed, exceeded
            message = await receive()
            if message["type"] == "http.request":
                consumed += len(message.get("body", b""))
                if consumed > self.max_body_bytes:
                    exceeded = True
                    # Raised inside request parsing: FastAPI's exception handler
                    # formats it and Starlette closes partially spooled files.
                    raise HTTPException(
                        413,
                        detail={
                            "code": self.error_code,
                            "message": self.error_message,
                        },
                    )
            return message

        async def bounded_send(message):
            nonlocal rejected
            if exceeded:
                # Some multipart/framework versions translate stream errors to
                # 400. Preserve the 413 contract at the outer ASGI boundary.
                if not rejected:
                    rejected = True
                    await self.reject(
                        scope,
                        receive,
                        send,
                        413,
                        self.error_code,
                        self.error_message,
                    )
                return
            await send(message)

        await self.app(scope, bounded_receive, bounded_send)

    @staticmethod
    async def reject(scope, receive, send, status, code, message):
        request_id = str(uuid4())
        response = JSONResponse(
            status_code=status,
            content={
                "error": {"code": code, "message": message, "request_id": request_id}
            },
            headers={"X-Request-ID": request_id, "Cache-Control": "no-store"},
        )
        await response(scope, receive, send)
