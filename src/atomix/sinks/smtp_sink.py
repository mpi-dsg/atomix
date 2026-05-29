"""SMTP receiver that appends every received message to an AppendOnlyLog.

Uses aiosmtpd. Run with `python -m atomix.sinks.smtp_sink --port 1025
--log /tmp/atomix-mail.log`.
"""

from __future__ import annotations

import argparse
import asyncio
from email import message_from_bytes
from pathlib import Path
from typing import Optional

from .append_only_log import AppendOnlyLog


class _LoggingHandler:
    """aiosmtpd handler that appends to an AppendOnlyLog."""

    def __init__(self, log: AppendOnlyLog) -> None:
        self._log = log
        self.received_count = 0

    async def handle_DATA(self, server, session, envelope) -> str:  # noqa: N802
        msg = message_from_bytes(envelope.content)
        payload = {
            "from": envelope.mail_from,
            "to": list(envelope.rcpt_tos),
            "subject": msg.get("Subject", ""),
            "body": _extract_body(msg),
            "raw_size": len(envelope.content),
        }
        self._log.append(payload)
        self.received_count += 1
        return "250 Message accepted"


def _extract_body(msg) -> str:
    if msg.is_multipart():
        parts = []
        for part in msg.walk():
            if part.get_content_type() == "text/plain":
                payload = part.get_payload(decode=True)
                if payload:
                    parts.append(payload.decode(errors="replace"))
        return "\n".join(parts)
    payload = msg.get_payload(decode=True)
    if payload is None:
        return ""
    return payload.decode(errors="replace")


class SMTPSink:
    """Async SMTP server that logs every received message.

    Usage:
        sink = SMTPSink(log_path=Path("/tmp/mail.log"), port=1025)
        await sink.start()
        # ... send mail to localhost:1025 ...
        await sink.stop()
    """

    def __init__(
        self,
        log_path: Path,
        host: str = "127.0.0.1",
        port: int = 1025,
    ) -> None:
        self._host = host
        self._port = port
        self._log = AppendOnlyLog(log_path)
        self._controller = None
        self._handler: Optional[_LoggingHandler] = None

    async def start(self) -> None:
        try:
            from aiosmtpd.controller import Controller
        except ImportError as e:
            raise ImportError(
                "aiosmtpd is required for SMTPSink. "
                "Install with `uv pip install aiosmtpd`."
            ) from e
        self._handler = _LoggingHandler(self._log)
        self._controller = Controller(self._handler, hostname=self._host, port=self._port)
        self._controller.start()

    async def stop(self) -> None:
        if self._controller is not None:
            self._controller.stop()
            self._controller = None
        self._log.close()

    @property
    def received_count(self) -> int:
        return self._handler.received_count if self._handler else 0

    @property
    def log(self) -> AppendOnlyLog:
        return self._log


async def _amain(args) -> None:
    sink = SMTPSink(log_path=Path(args.log), host=args.host, port=args.port)
    await sink.start()
    print(f"SMTP sink listening on {args.host}:{args.port}, log={args.log}")
    try:
        while True:
            await asyncio.sleep(3600)
    except KeyboardInterrupt:
        pass
    finally:
        await sink.stop()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=1025)
    parser.add_argument("--log", required=True)
    args = parser.parse_args()
    asyncio.run(_amain(args))


if __name__ == "__main__":
    main()
