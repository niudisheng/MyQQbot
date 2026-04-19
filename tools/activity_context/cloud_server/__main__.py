"""python -m tools.activity_context.cloud_server"""

from __future__ import annotations

import os
import sys
from pathlib import Path

if __package__ in (None, ""):
    _ROOT = Path(__file__).resolve().parents[3]
    if str(_ROOT) not in sys.path:
        sys.path.insert(0, str(_ROOT))

try:
    import uvicorn
except ImportError as exc:
    raise SystemExit(
        "需要安装 uvicorn：pip install uvicorn[standard]\n"
        "或：pip install -r tools/activity_context/cloud_server/requirements.txt"
    ) from exc

from tools.activity_context.cloud_server import config
from tools.activity_context.cloud_server.app import app


def main() -> None:
    host = config.host()
    port = config.port()
    reload = os.getenv("ACTIVITY_CONTEXT_SERVER_RELOAD", "").lower() in (
        "1",
        "true",
        "yes",
    )
    if reload:
        uvicorn.run(
            "tools.activity_context.cloud_server.app:app",
            host=host,
            port=port,
            reload=True,
        )
    else:
        uvicorn.run(app, host=host, port=port)


if __name__ == "__main__":
    main()
