import logging
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.database import Base, engine
from app.routes import patients, vapi

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger("app")


@asynccontextmanager
async def lifespan(app: FastAPI):
    Base.metadata.create_all(bind=engine)
    if os.environ.get("SEED_ON_STARTUP", "").lower() in ("1", "true", "yes"):
        from app.seed import main as seed

        seed()
    yield


app = FastAPI(
    title="Voice AI Patient Registration API",
    version="1.0.0",
    lifespan=lifespan,
)
app.include_router(patients.router)
app.include_router(vapi.router)


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
