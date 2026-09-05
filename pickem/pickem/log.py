import logging
from contextvars import ContextVar
from logging.handlers import RotatingFileHandler
from typing import Optional
from uuid import uuid4

# Per-request context for log lines (set by RequestContextMiddleware).
_request_id: ContextVar[str] = ContextVar("request_id", default="-")
_user_label: ContextVar[str] = ContextVar("user_label", default="-")


def get_request_id() -> str:
    return _request_id.get()


def set_request_context(
    *, request_id: Optional[str] = None, user_label: str = "-"
) -> None:
    _request_id.set(request_id or uuid4().hex[:8])
    _user_label.set(user_label or "-")


def clear_request_context() -> None:
    _request_id.set("-")
    _user_label.set("-")


def user_label_for(user) -> str:
    if user is None or not getattr(user, "is_authenticated", False):
        return "-"
    return f"{user.pk}:{user.get_username()}"


class RequestContextFilter(logging.Filter):
    """Inject request_id / user into every LogRecord for formatters."""

    def filter(self, record: logging.LogRecord) -> bool:
        record.request_id = _request_id.get()
        record.user = _user_label.get()
        return True


class SafeRotatingFileHandler(RotatingFileHandler):
    """Rotate logs, but skip rename when another process has the file open.

    Windows cannot rename a file that Django runserver, Celery beat, or another
    worker already has open. Python then dumps a PermissionError traceback on
    every rollover even though logging itself still works.
    """

    def doRollover(self):
        try:
            super().doRollover()
        except PermissionError:
            pass
