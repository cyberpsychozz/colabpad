"""Local launcher; configuration is loaded from the same .env as the application."""

import uvicorn

from app.config import Settings

if __name__ == "__main__":
    settings = Settings()
    uvicorn.run(
        "app.main:asgi",
        host="127.0.0.1",
        port=settings.port,
        workers=1,
        access_log=False,
    )
