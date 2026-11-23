"""Centralized logging: console + rotating file handler."""
import logging
import re
import sys
from logging.handlers import RotatingFileHandler

from config.settings import Settings

LOG_FORMAT = "%(asctime)s | %(levelname)-8s | %(name)s | %(message)s"
DATE_FORMAT = "%Y-%m-%d %H:%M:%S"
_HANDLER_TAG = "_jobpilot_handler"

# Anything that looks like a credential is masked in the FINAL text (message + traceback), whatever code logged it.
_REDACTIONS = [
    (re.compile(r"(mongodb(?:\+srv)?://)[^\s/@]*@", re.I), r"\1***@"),
    (re.compile(r"\bsk-[A-Za-z0-9_\-]{8,}"), "sk-***"),
    (re.compile(r"(?i)\b(bearer|basic)\s+[A-Za-z0-9._~+/=\-]{8,}"), r"\1 ***"),
    (re.compile(r"(?i)\b(x-admin-token|authorization|password|passwd|pwd|secret|api[_-]?key|access[_-]?token|refresh[_-]?token|client[_-]?secret)\b([\"']?\s*[:=]\s*[\"']?)[^\s\"',;)}\]]{4,}"), r"\1\2***"),
    (re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+"), "[email]"),
]


def redact(text: str) -> str:
    for rx, repl in _REDACTIONS:
        text = rx.sub(repl, text)
    return text


class RedactingFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        return redact(super().format(record))


def setup_logging(settings: Settings) -> None:
    """Configure the root logger. Safe to call more than once."""
    root = logging.getLogger()
    root.setLevel(settings.log_level)

    for handler in list(root.handlers):
        if getattr(handler, _HANDLER_TAG, False):
            root.removeHandler(handler)
            handler.close()

    formatter = RedactingFormatter(LOG_FORMAT, DATE_FORMAT)

    console = logging.StreamHandler(sys.stderr)
    console.setFormatter(formatter)
    setattr(console, _HANDLER_TAG, True)
    root.addHandler(console)

    log_path = settings.log_path
    log_path.parent.mkdir(parents=True, exist_ok=True)
    file_handler = RotatingFileHandler(
        log_path, maxBytes=5 * 1024 * 1024, backupCount=3, encoding="utf-8"
    )
    file_handler.setFormatter(formatter)
    setattr(file_handler, _HANDLER_TAG, True)
    root.addHandler(file_handler)

    # Avoid noisy third-party debug output (e.g. pymongo heartbeats).
    # pymongo's DEBUG loggers print whole commands (documents = personal data) and httpx prints URLs: keep ALL of them quiet whatever LOG_LEVEL is.
    for noisy in ("pymongo", "pymongo.command", "pymongo.connection", "pymongo.serverSelection", "pymongo.topology", "httpx", "httpcore", "PIL", "pdfminer"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
