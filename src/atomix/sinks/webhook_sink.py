"""HTTP webhook receiver that appends every POST body to an AppendOnlyLog."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Mapping

from .append_only_log import AppendOnlyLog

REDACTED_HEADER_VALUE = "<redacted>"
SENSITIVE_HEADER_NAMES = frozenset(
    {
        "authorization",
        "cookie",
        "proxy-authorization",
        "set-cookie",
        "x-api-key",
        "api-key",
        "x-auth-token",
        "x-csrf-token",
    }
)


def _redact_headers(headers: Mapping[str, str]) -> dict[str, str]:
    """Return headers with credential-bearing values removed."""
    redacted: dict[str, str] = {}
    for name, value in headers.items():
        normalized = name.lower()
        if normalized in SENSITIVE_HEADER_NAMES or "token" in normalized:
            redacted[name] = REDACTED_HEADER_VALUE
        else:
            redacted[name] = value
    return redacted


class WebhookSink:
    """Single-endpoint FastAPI app that logs every POST.

    Usage:
        sink = WebhookSink(log_path=Path("/tmp/web.log"))
        app = sink.app  # FastAPI app
        # serve with uvicorn:
        # uvicorn atomix.sinks.webhook_sink:app --host 127.0.0.1 --port 8001
    """

    def __init__(self, log_path: Path) -> None:
        self._log = AppendOnlyLog(log_path)
        self._app: Any = None
        self.received_count = 0

    @property
    def app(self) -> Any:
        if self._app is None:
            self._app = self._build_app()
        return self._app

    @property
    def log(self) -> AppendOnlyLog:
        return self._log

    def _build_app(self) -> Any:
        try:
            from fastapi import FastAPI, Request
        except ImportError as e:
            raise ImportError(
                "fastapi is required for WebhookSink. "
                "Install with `uv pip install fastapi uvicorn`."
            ) from e

        app = FastAPI()

        @app.post("/")
        async def receive(request: Request):
            body = await request.body()
            try:
                payload = json.loads(body.decode("utf-8")) if body else {}
                if not isinstance(payload, dict):
                    payload = {"value": payload}
            except json.JSONDecodeError:
                payload = {"raw": body.decode("utf-8", errors="replace")}
            payload["_headers"] = _redact_headers(dict(request.headers))
            self._log.append(payload)
            self.received_count += 1
            return {"status": "ok", "count": self.received_count}

        @app.get("/health")
        async def health():
            return {"status": "ok", "count": self.received_count}

        return app

    def close(self) -> None:
        self._log.close()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8001)
    parser.add_argument("--log", required=True)
    args = parser.parse_args()
    try:
        import uvicorn
    except ImportError as e:
        raise SystemExit(
            "uvicorn required: uv pip install fastapi uvicorn"
        ) from e
    sink = WebhookSink(log_path=Path(args.log))
    uvicorn.run(sink.app, host=args.host, port=args.port)


if __name__ == "__main__":
    main()
