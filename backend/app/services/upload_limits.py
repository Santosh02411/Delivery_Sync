"""
Refuses oversized request bodies BEFORE the framework parses them.

Why a middleware and not a check in the route: FastAPI/Starlette read and parse the entire multipart
body (spooling it into memory and a temp file) before the route function is even called, so a size check
inside the route can only run after the whole upload has already been received. This wraps the request
at the ASGI layer and enforces the limit in two ways:

  1. Content-Length larger than the limit  -> 413 immediately; the body is never read.
  2. No/lying Content-Length (chunked transfer, or a client that sends more than it announced) -> the
     bytes are counted as they arrive; the moment the limit is crossed the request is cut off with 413.

It is plain ASGI (no framework imports), so it works with any ASGI server and is unit-tested directly.
Optionally (require_authorization_header=True) a request with no Authorization header at all is refused with
401 before a single body byte is read: the framework authenticates only AFTER it has parsed the body, so
without this an anonymous client could make the server receive the full limit on every request.
Only POST/PUT/PATCH requests to the configured paths are affected.
"""

import json
from typing import Dict

_BODY_METHODS = {"POST", "PUT", "PATCH"}


class _BodyTooLarge(Exception):
    pass


class BodySizeLimitMiddleware:
    def __init__(self, app, limits: Dict[str, int], require_authorization_header: bool = False):
        """`limits` maps an exact URL path to the maximum body size in bytes for that path."""
        self.app = app
        self.limits = limits
        self.require_authorization_header = require_authorization_header

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or scope.get("method") not in _BODY_METHODS or scope.get("path") not in self.limits:
            await self.app(scope, receive, send)
            return

        limit = self.limits[scope["path"]]
        declared = None
        has_authorization = False
        for name, value in scope.get("headers", []):
            if name == b"content-length":
                try:
                    declared = int(value)
                except ValueError:
                    declared = None
            elif name == b"authorization" and value.strip():
                has_authorization = True
        if self.require_authorization_header and not has_authorization:
            await self._respond(send, 401, "Not authenticated.")
            return
        if declared is not None and declared > limit:
            await self._reject(send, limit)
            return

        state = {"seen": 0, "exceeded": False, "responded": False}

        async def limited_receive():
            message = await receive()
            if message["type"] == "http.request":
                state["seen"] += len(message.get("body", b""))
                if state["seen"] > limit:
                    state["exceeded"] = True
                    raise _BodyTooLarge()
            return message

        async def guarded_send(message):
            # FastAPI turns ANY error while parsing the body into its own 400 response. Once the limit has
            # been crossed, whatever the app tries to say is replaced by the one correct answer: 413.
            if state["exceeded"]:
                if not state["responded"]:
                    state["responded"] = True
                    await self._reject(send, limit)
                return
            await send(message)

        try:
            await self.app(scope, limited_receive, guarded_send)
        except _BodyTooLarge:
            pass
        except Exception:
            if not state["exceeded"]:
                raise
        if state["exceeded"] and not state["responded"]:
            state["responded"] = True
            await self._reject(send, limit)

    @classmethod
    async def _reject(cls, send, limit: int):
        await cls._respond(send, 413, f"Upload is too large — max {max(limit // (1024 * 1024), 1)} MB.")

    @staticmethod
    async def _respond(send, status: int, detail: str):
        body = json.dumps({"detail": detail}).encode()
        await send({
            "type": "http.response.start", "status": status,
            "headers": [(b"content-type", b"application/json"), (b"content-length", str(len(body)).encode()), (b"connection", b"close")],
        })
        await send({"type": "http.response.body", "body": body})
