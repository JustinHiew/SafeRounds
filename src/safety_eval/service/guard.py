"""Request guards: body size limits, a per-key rate limit, and one place that turns any crash into
a bare 500 so that no stack trace (which could quote a conversation) reaches the client or the logs."""

import json
import logging
import threading
import time
from collections import defaultdict, deque

log = logging.getLogger("safety_eval.service")


class RateLimiter:
    """Sliding one-minute window per (key, bucket), in memory. One process only: behind several
    workers each would allow the full rate, which is fine for the first deployment."""

    def __init__(self, clock=time.monotonic):
        self.clock = clock
        self.hits = defaultdict(deque)
        self.lock = threading.Lock()

    def allow(self, key, limit, window_s=60.0):
        now = self.clock()
        with self.lock:
            q = self.hits[key]
            while q and now - q[0] >= window_s:
                q.popleft()
            if len(q) >= limit:
                return False
            q.append(now)
            return True


class TooLarge(BaseException):
    # Not an Exception: FastAPI turns an Exception raised while reading the body into a 400.
    pass


class Guard:
    """Pure ASGI middleware. `limits` is a list of ((method, path), max_bytes); anything else gets
    `default_limit`. A declared Content-Length is checked up front; a body with none (chunked) is
    counted as it streams, so the limit holds either way."""

    def __init__(self, app, limits, default_limit):
        self.app, self.limits, self.default_limit = app, dict(limits), default_limit

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        limit = self.limits.get((scope["method"], scope["path"]), self.default_limit)
        declared = dict(scope["headers"]).get(b"content-length")
        started = False

        async def counting_receive():
            message = await receive()
            if message["type"] == "http.request":
                counting_receive.seen += len(message.get("body", b""))
                if counting_receive.seen > limit:
                    raise TooLarge
            return message
        counting_receive.seen = 0

        async def tracking_send(message):
            nonlocal started
            started = started or message["type"] == "http.response.start"
            await send(message)

        try:
            if declared and declared.isdigit() and int(declared) > limit:
                raise TooLarge
            await self.app(scope, counting_receive, tracking_send)
        except TooLarge:
            if not started:
                await _reply(send, 413, "request body too large")
        except Exception as exc:
            log.error("unhandled error (%s)", type(exc).__name__)    # type only: no message, no trace
            if not started:
                await _reply(send, 500, "internal error")


async def _reply(send, status, detail):
    body = json.dumps({"detail": detail}).encode()
    await send({"type": "http.response.start", "status": status,
                "headers": [(b"content-type", b"application/json"),
                            (b"content-length", str(len(body)).encode())]})
    await send({"type": "http.response.body", "body": body})
