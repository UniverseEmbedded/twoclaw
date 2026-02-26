from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .api import mem0_router, mem1_router
from .config import Mem1Settings
from .service import Mem1Service


def create_app(*, settings: Mem1Settings | None = None) -> FastAPI:
    s = Mem1Settings.from_env() if settings is None else settings
    app = FastAPI(title="mem1", version="0.1.0")

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    app.state.mem1_service = Mem1Service(settings=s)
    app.include_router(mem0_router, tags=["mem0-compat"])
    app.include_router(mem1_router, tags=["mem1-enhanced"])
    return app


app = create_app()
