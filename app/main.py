import logging
import os
import time
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from sqlalchemy.exc import OperationalError
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.database import Base, engine
from app.routes import dashboard, patients, vapi

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger("app")


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Managed Postgres (and Railway's private DNS) can take a few seconds to
    # accept connections after the container starts. Retry rather than crash-loop.
    last_error: Exception | None = None
    for attempt in range(1, 11):
        try:
            Base.metadata.create_all(bind=engine)
            last_error = None
            break
        except OperationalError as exc:
            last_error = exc
            logger.warning("Database not ready (attempt %s/10): %s", attempt, exc)
            time.sleep(min(2 * attempt, 10))
    if last_error is not None:
        logger.error("Could not reach the database after 10 attempts.")
        raise last_error

    if os.environ.get("SEED_ON_STARTUP", "").lower() in ("1", "true", "yes"):
        from app.seed import main as seed

        try:
            seed()
        except Exception:
            # Seeding is a demo convenience; never let it take down the API.
            logger.exception("Seeding failed; continuing without seed data.")
    yield


app = FastAPI(
    title="Voice AI Patient Registration API",
    version="1.0.0",
    lifespan=lifespan,
)
app.include_router(patients.router)
app.include_router(vapi.router)
app.include_router(dashboard.router)


def envelope(data=None, error=None, status_code: int = 200) -> JSONResponse:
    return JSONResponse(status_code=status_code, content={"data": data, "error": error})


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
    details = [
        {
            "field": ".".join(str(p) for p in err["loc"] if p != "body"),
            "message": err["msg"].removeprefix("Value error, "),
        }
        for err in exc.errors()
    ]
    return envelope(
        error={"code": "validation_error", "message": "Invalid input.", "details": details},
        status_code=422,
    )


@app.exception_handler(StarletteHTTPException)
async def http_exception_handler(request: Request, exc: StarletteHTTPException):
    detail = exc.detail if isinstance(exc.detail, dict) else {
        "code": "http_error", "message": str(exc.detail),
    }
    return envelope(error=detail, status_code=exc.status_code)


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception):
    logger.exception("Unhandled error on %s %s", request.method, request.url.path)
    return envelope(
        error={"code": "internal_error", "message": "An unexpected error occurred."},
        status_code=500,
    )


@app.get("/")
async def health():
    return {"data": {"status": "ok", "service": "voice-patient-registration"}, "error": None}
