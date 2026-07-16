import logging

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.routes import router
from app.config import get_settings


def _configure_pipeline_logging() -> None:
    """Make the shadow pipeline's structured-JSON logs visible under uvicorn,
    which configures only its own loggers (root has no handler, so INFO
    records would otherwise be dropped). Scoped to this one logger — nothing
    else gets noisier."""
    pipeline_logger = logging.getLogger("app.agent.retrieval_pipeline")
    if not pipeline_logger.handlers:
        handler = logging.StreamHandler()
        handler.setFormatter(logging.Formatter("%(asctime)s [%(name)s] %(levelname)s %(message)s"))
        pipeline_logger.addHandler(handler)
    pipeline_logger.setLevel(logging.INFO)


def create_app() -> FastAPI:
    app = FastAPI(title="Dubizzle Car Assistant")
    _configure_pipeline_logging()

    frontend_origin = get_settings().frontend_origin
    app.add_middleware(
        CORSMiddleware,
        # Explicit origins only — the configured frontend origin plus its
        # 127.0.0.1 twin (browsers treat localhost/127.0.0.1 as distinct).
        allow_origins=[
            frontend_origin,
            frontend_origin.replace("localhost", "127.0.0.1"),
        ],
        allow_methods=["GET", "POST"],
        allow_headers=["Content-Type"],
        # No cookies involved: session_id travels in the request body.
        allow_credentials=False,
    )

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    app.include_router(router)
    return app


app = create_app()
