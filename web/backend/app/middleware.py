"""Security headers + body-size guard."""
from __future__ import annotations

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response


SECURE_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "no-referrer",
    "Permissions-Policy": "geolocation=(), microphone=(), camera=()",
    # CSP. The SPA is fully self-hosted; no inline scripts (Vite/React
    # don't need any after build). 'unsafe-inline' for style is needed
    # by some React patterns and inline style attributes. Tighten if
    # you can prove it isn't necessary in your bundle.
    "Content-Security-Policy": (
        "default-src 'self'; "
        # Allow inline data: images plus the Google Fonts stylesheet host
        # for fonts.googleapis.com / fonts.gstatic.com.
        "img-src 'self' data: blob:; "
        "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; "
        "font-src 'self' data: https://fonts.gstatic.com; "
        "script-src 'self'; "
        "connect-src 'self' ws: wss:; "
        "frame-ancestors 'none'; "
        "base-uri 'self'; "
        "object-src 'none'"
    ),
    # HSTS — operators behind HTTPS should keep this; harmless on http.
    "Strict-Transport-Security": "max-age=31536000; includeSubDomains",
}


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        response: Response = await call_next(request)
        for k, v in SECURE_HEADERS.items():
            response.headers.setdefault(k, v)
        return response


class MaxBodySizeMiddleware(BaseHTTPMiddleware):
    """Refuse oversized requests before any route reads the body.

    The per-file caps inside the jobs route still apply for finer
    granularity; this is the outer dam in case a client streams a
    multi-GB body to a non-upload endpoint.
    """

    def __init__(self, app, max_bytes: int) -> None:
        super().__init__(app)
        self.max_bytes = max_bytes

    async def dispatch(self, request: Request, call_next):
        cl = request.headers.get("content-length")
        if cl and cl.isdigit() and int(cl) > self.max_bytes:
            from starlette.responses import PlainTextResponse
            return PlainTextResponse("Request too large", status_code=413)
        return await call_next(request)
