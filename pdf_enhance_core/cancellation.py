"""Task-local cooperative cancellation; CLI calls remain unaffected."""

from contextlib import contextmanager
from contextvars import ContextVar


_cancel_event = ContextVar("pdf_enhance_cancel_event", default=None)


class ProcessingCancelled(BaseException):
    """Control flow, not a processing failure: must bypass OCR fallback handlers."""


def check_cancelled():
    event = _cancel_event.get()
    if event is not None and event.is_set():
        raise ProcessingCancelled()


@contextmanager
def cancellation_scope(event):
    token = _cancel_event.set(event)
    try:
        check_cancelled()
        yield
    finally:
        _cancel_event.reset(token)
