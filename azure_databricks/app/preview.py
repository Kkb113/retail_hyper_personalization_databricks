"""Loopback-only disconnected preview. Never discovers Azure credentials."""
from pathlib import Path

from retail_hp_azure.phase10_app import create_app

app = create_app(static_dir=Path(__file__).parent / "frontend/dist")

if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="127.0.0.1", port=8010, access_log=False)
