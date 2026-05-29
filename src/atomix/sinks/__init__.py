"""Real append-only sinks for irreversible-effect experiments (E3, E4).

These sinks are the "real world" that swallows messages and never lets them
go. The Atomix runtime must classify tools that write here as irreversible
so they are gated by the frontier and never run in losing speculative
branches.
"""

from .append_only_log import AppendOnlyLog
from .smtp_sink import SMTPSink
from .webhook_sink import WebhookSink

__all__ = ["AppendOnlyLog", "SMTPSink", "WebhookSink"]
