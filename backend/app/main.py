from fastapi import FastAPI, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from slowapi.middleware import SlowAPIMiddleware
from .config import settings
from .limiter import limiter
from .routers import two_factor
from .routers import auth, oidc, notifications, event_types, people, data, calendar_feed, settings as settings_router

docs_url = "/docs" if settings.docs_enabled else None
redoc_url = "/redoc" if settings.docs_enabled else None
app = FastAPI(title="Candlr API", docs_url=docs_url, redoc_url=redoc_url)

app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)
app.add_middleware(SlowAPIMiddleware)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.middleware("http")
async def security_headers(request: Request, call_next) -> Response:
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    return response


app.include_router(auth.router)
app.include_router(two_factor.router)
app.include_router(oidc.router)
app.include_router(notifications.router)
app.include_router(event_types.router)
app.include_router(people.router)
app.include_router(data.router)
app.include_router(calendar_feed.router)
app.include_router(settings_router.router)


@app.get("/health")
def health():
    return {"status": "ok"}
