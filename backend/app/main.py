from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.routes import router
from app.config import get_settings
from app.services.run_queue import RUN_QUEUE
from app.services.scheduler import create_scheduler


@asynccontextmanager
async def lifespan(_app: FastAPI):
    RUN_QUEUE.recover_stale_runs()
    scheduler = create_scheduler()
    if scheduler:
        scheduler.start()
    try:
        yield
    finally:
        if scheduler:
            scheduler.shutdown(wait=False)
        RUN_QUEUE.shutdown()


settings = get_settings()
app = FastAPI(
    title="Alpha Sage API",
    version="0.1.0",
    description="本地、证据优先、自我学习进化的A股模拟投资Agent",
    lifespan=lifespan,
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=[settings.web_origin],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.include_router(router)
