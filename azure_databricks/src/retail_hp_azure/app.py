"""Local-only app contract skeleton; no recommendations until later parity gates."""

from fastapi import FastAPI
from fastapi.responses import JSONResponse

from retail_hp_azure import __version__


def create_app() -> FastAPI:
    app = FastAPI(title="Retail HP Azure POC", version=__version__, docs_url=None, redoc_url=None)

    @app.get("/health/live")
    def live() -> dict[str, str]:
        return {"status": "alive", "phase": "1"}

    @app.get("/health/ready")
    def ready() -> JSONResponse:
        return JSONResponse(status_code=503, content={
            "status": "not_ready", "reason": "Model and agent are not deployed in Phase 1",
        })

    @app.get("/version")
    def version() -> dict[str, str]:
        return {"version": __version__, "release": "POC_ONLY"}

    return app
