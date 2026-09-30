"""Packaged entry point: `klipani-backend` (or `python backend/run.py` in dev).

Storage root defaults to <project>/storage, override with KLIPANI_DATA
(packaged apps point it at the OS user-data dir).
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import uvicorn  # noqa: E402

from app.config import get_settings  # noqa: E402
from app.main import app as asgi_app  # noqa: E402


def main() -> None:
    settings = get_settings()
    uvicorn.run(
        asgi_app,
        host="127.0.0.1",
        port=settings.backend_port,
        log_level="info",
    )


if __name__ == "__main__":
    main()
