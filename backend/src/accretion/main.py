from __future__ import annotations

import uvicorn

from accretion.api import create_app
from accretion.config import get_settings


def run() -> None:
    settings = get_settings()
    uvicorn.run(
        create_app(settings),
        host=settings.host,
        port=settings.port,
        log_level="info",
    )


if __name__ == "__main__":
    run()
