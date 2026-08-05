import asyncio
import contextlib
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.cache import close_redis
from app.config import get_settings
from app.database import engine
from app.errors import ApiError
from app.routers import auth, grounding, problems, tutor
from app.worker import run_worker


@asynccontextmanager
async def lifespan(app: FastAPI):
    # In dev, run the ingestion worker inside the API process so `uvicorn` alone processes
    # the queue. The compose stack sets RUN_INLINE_WORKER=false and runs a dedicated worker.
    worker_task: asyncio.Task | None = None
    stop_event = asyncio.Event()
    if get_settings().run_inline_worker:
        worker_task = asyncio.create_task(run_worker(stop_event=stop_event))

    yield

    if worker_task is not None:
        stop_event.set()
        worker_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await worker_task
    await close_redis()
    await engine.dispose()


app = FastAPI(
    title="Spotr",
    description="Graduated editorial hints, stress-test-verified pitfall checking, "
    "and practice recommendations for competitive programmers.",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=get_settings().cors_origin_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(auth.router)
app.include_router(problems.router)
app.include_router(tutor.router)
app.include_router(grounding.router)


@app.exception_handler(ApiError)
async def api_error_handler(request: Request, exc: ApiError) -> JSONResponse:
    return JSONResponse(
        status_code=exc.status_code,
        content={"detail": exc.detail, "code": exc.code},
    )


@app.get("/health")
async def health():
    return {"status": "ok"}
