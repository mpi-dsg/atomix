"""
Logging configuration for Atomix.

Provides structured logging for the Atomix runtime and demos.
"""

from __future__ import annotations

import logging
import sys
from typing import Optional, TextIO


def setup_logging(
    level: int = logging.INFO,
    format_string: Optional[str] = None,
    stream: Optional[TextIO] = None,
) -> logging.Logger:
    """Configure logging for Atomix.

    Args:
        level: Logging level (default: INFO)
        format_string: Custom format string (default: timestamp + level + name + message)
        stream: Output stream (default: sys.stdout)

    Returns:
        The configured root logger for atomix.
    """
    if format_string is None:
        format_string = "%(asctime)s [%(levelname)s] %(name)s: %(message)s"

    if stream is None:
        stream = sys.stdout

    # Configure the atomix logger
    logger = logging.getLogger("atomix")

    # Only add handler if not already configured
    if not logger.handlers:
        handler = logging.StreamHandler(stream)
        handler.setFormatter(logging.Formatter(format_string))
        logger.addHandler(handler)

    logger.setLevel(level)

    return logger


def get_logger(name: str) -> logging.Logger:
    """Get a logger for a specific module.

    Args:
        name: Module name (will be prefixed with 'atomix.')

    Returns:
        A logger instance.
    """
    if not name.startswith("atomix."):
        name = f"atomix.{name}"
    return logging.getLogger(name)


# Default logger for quick access
logger = get_logger("core")
