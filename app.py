from collections import defaultdict, deque
from pathlib import Path
import logging
import os
import secrets
from threading import Lock, BoundedSemaphore
from time import monotonic
import uuid

from dotenv import load_dotenv
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, ConfigDict, Field
from starlette.middleware.sessions import SessionMiddleware

load_dotenv()
BASE_DIR = Path(__file__).resolve().parent
logger = logging.getLogger(__name__)
SESSION_SECRET = os.getenv("SESSION_SECRET")
if not SESSION_SECRET:
    if os.getenv("VERCEL"):
        raise RuntimeError("Server session configuration is missing")
    SESSION_SECRET = secrets.token_urlsafe(48)

app = FastAPI(title="TripMate AI", version="1.0.0", docs_url=None,
              redoc_url=None, openapi_url=None)
app.add_middleware(SessionMiddleware, secret_key=SESSION_SECRET,
                   session_cookie="tripmate_session", same_site="strict",
                   https_only=bool(os.getenv("VERCEL")), max_age=86400)
app.mount("/static", StaticFiles(directory=str(BASE_DIR / "static")), name="static")
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))

# Best-effort instance limits. Distributed abuse control must run at the edge.
_attempts = defaultdict(deque)
_rate_lock = Lock()
_capacity = BoundedSemaphore(2)


def allowed_request(ip: str) -> bool:
    now = monotonic()
    with _rate_lock:
        for key in list(_attempts):
            while _attempts[key] and _attempts[key][0] <= now - 600:
                _attempts[key].popleft()
            if not _attempts[key]:
                del _attempts[key]
        if len(_attempts) >= 10000 and ip not in _attempts:
            return False
        if len(_attempts[ip]) >= 5:
            return False
        _attempts[ip].append(now)
        return True


def redact_secrets(value):
    if isinstance(value, str):
        for key, secret in os.environ.items():
            if any(part in key for part in ("API_KEY", "SECRET", "DATABASE_URL", "TOKEN")) and len(secret) >= 8:
                value = value.replace(secret, "[redacted]")
        return value
    if isinstance(value, dict):
        return {key: redact_secrets(item) for key, item in value.items()}
    if isinstance(value, list):
        return [redact_secrets(item) for item in value]
    return value


@app.middleware("http")
async def secure_requests(request: Request, call_next):
    response = None
    if request.method == "POST":
        # Check the streamed body too: Content-Length alone can be forged/absent.
        body = bytearray()
        async for chunk in request.stream():
            body.extend(chunk)
            if len(body) > 8192:
                response = JSONResponse(status_code=413, content={"success": False, "error": "Request is too large."})
                break
        if response is None:
            request._body = bytes(body)
    if response is None:
        response = await call_next(request)
    response.headers.update({
        "Content-Security-Policy": "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data: blob:; font-src 'self' data:; connect-src 'self'; frame-src 'self' blob:; object-src 'none'; base-uri 'none'; frame-ancestors 'none'; form-action 'self'",
        "X-Content-Type-Options": "nosniff",
        "X-Frame-Options": "DENY",
        "Referrer-Policy": "no-referrer",
        "Permissions-Policy": "camera=(), microphone=(), geolocation=()",
    })
    if os.getenv("VERCEL"):
        response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
    if not request.url.path.startswith("/static/"):
        response.headers["Cache-Control"] = "no-store"
    return response


class TravelRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    message: str = Field(min_length=1, max_length=2000)
    thread_id: str | None = Field(default=None, max_length=64)


@app.exception_handler(RequestValidationError)
async def invalid_request(request, exc):
    return JSONResponse(status_code=422, content={"success": False, "error": "Enter a travel request between 1 and 2,000 characters."})


@app.get("/", response_class=HTMLResponse)
async def home(request: Request):
    if not request.session.get("thread_id"):
        request.session["thread_id"] = f"user_{uuid.uuid4().hex}"
    return templates.TemplateResponse(request=request, name="index.html", context={})


@app.post("/api/travel")
def travel_planner(request: Request, request_data: TravelRequest):
    # Cookies bind persisted threads to a browser; caller-supplied IDs cannot
    # select another visitor's history. Same-origin checks prevent cross-site use.
    origin = request.headers.get("origin")
    if origin != str(request.base_url).rstrip("/") or not request.session.get("thread_id"):
        return JSONResponse(status_code=403, content={"success": False, "error": "Reload this page and try again."})
    thread_id = request.session["thread_id"]
    if request_data.thread_id and request_data.thread_id != thread_id:
        return JSONResponse(status_code=403, content={"success": False, "error": "Invalid session."})
    # Vercel overwrites this header; other deployments use the actual peer IP.
    ip = request.headers.get("x-vercel-forwarded-for", "").split(",")[0].strip() if os.getenv("VERCEL") else ""
    ip = ip or (request.client.host if request.client else "unknown")
    if not allowed_request(ip):
        return JSONResponse(status_code=429, headers={"Retry-After": "600"}, content={"success": False, "error": "Too many requests. Please try again in 10 minutes."})
    if not _capacity.acquire(blocking=False):
        return JSONResponse(status_code=503, content={"success": False, "error": "The planner is busy. Please try again shortly."})
    try:
        from backend import run_travel_agent
        result = run_travel_agent(user_input=request_data.message, thread_id=thread_id)
        return JSONResponse(content=redact_secrets({"success": True, **result}))
    except Exception as exc:
        # Exception messages/tracebacks may contain authenticated URLs or tokens.
        logger.warning("Travel planning failed (%s)", type(exc).__name__)
        return JSONResponse(status_code=503, content={"success": False, "error": "Travel planning is temporarily unavailable. Please try again later."})
    finally:
        _capacity.release()


@app.get("/health")
async def health_check():
    return {"status": "ok"}


@app.get("/favicon.ico")
async def favicon():
    return JSONResponse(content={})


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("app:app", host="127.0.0.1", port=8000)
