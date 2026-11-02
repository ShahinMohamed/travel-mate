# TripMate AI

FastAPI travel planner using Gemini, Tavily, and AviationStack.

## Local development

Use Python 3.12+, install `requirements.txt`, and copy `.env.example` to `.env`.
Set `GEMINI_API_KEY`, `GEMINI_MODEL`, `TAVILY_API_KEY`, and
`AVIATIONSTACK_API_KEY`. Generate a random `SESSION_SECRET` for stable sessions.
Run `uvicorn app:app --host 127.0.0.1 --port 8000`.

## Vercel

The FastAPI entrypoint is `app.py`. Set API keys and `SESSION_SECRET` as Vercel
Secrets, scoped to Production. Never put credentials in browser JavaScript,
HTML, public environment variables, or Git. `.vercelignore` excludes local
credentials, environments, and Git metadata from uploads. Keep LangSmith
tracing disabled unless you explicitly intend to export travel requests.

The current deployment uses `TRAVEL_STATELESS=1`: requests are not saved to
PostgreSQL. For durable checkpoints, provide a reachable PostgreSQL
`DATABASE_URL` as a server Secret and disable stateless mode. Session cookies
bind threads to the originating browser; supplied thread IDs cannot select
another session's saved history.

Security controls include signed HttpOnly/SameSite session cookies (Secure on
Vercel), same-origin POST checks, bounded inputs, generic provider errors,
secret redaction, sanitized Markdown, a restrictive Content Security Policy,
and pinned local browser libraries. The request limiter allows five plans per
IP per ten minutes **per function instance**, with two concurrent plans per
instance. It does not provide a distributed quota or a spending cap. Keep
Vercel Authentication enabled for private use. Before opening public access,
configure durable edge rate limits and provider spending limits.

Production source dependencies are listed in `requirements.in`; the resolved
Python 3.12 versions are pinned in `requirements.txt`. Browser dependencies in
`static/vendor` are DOMPurify 3.4.17, marked 18.1.2, and html2pdf.js 0.14.0.
Keep licenses bundled with vendored assets.

## Checks

Install pytest as a development tool, then run `python -m pytest -q`.
Security regression tests stub external providers and do not consume API quota.
