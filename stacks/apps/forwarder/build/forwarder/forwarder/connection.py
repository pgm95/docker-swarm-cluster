"""IMAP/SMTP connection management for Email Forwarder."""
import imaplib
import logging
import signal
import smtplib
import time
from types import FrameType
from typing import Optional, Union

from .config import (
    CONNECTION_MAX_AGE,
    CONNECTION_TIMEOUT,
    EMAIL_ADDRESS,
    EMAIL_PASSWORD,
    IMAP_PORT,
    IMAP_SERVER,
    SMTP_PORT,
    SMTP_SERVER,
)

logger = logging.getLogger(__name__)


class OperationTimeout(Exception):
    """Raised when an operation times out."""
    pass


def _timeout_handler(_signum: int, _frame: Optional[FrameType]) -> None:
    raise OperationTimeout("Operation timed out")


def run_with_timeout(func, timeout: int, name: str = "operation"):
    """Execute function with signal-based timeout."""
    old_handler = signal.signal(signal.SIGALRM, _timeout_handler)
    signal.alarm(timeout)
    try:
        return func()
    except OperationTimeout:
        logger.error(f"{name} timed out after {timeout}s")
        raise
    finally:
        signal.alarm(0)
        signal.signal(signal.SIGALRM, old_handler)


def connect_imap() -> imaplib.IMAP4_SSL:
    """Connect to IMAP server with configured timeout."""
    assert IMAP_SERVER is not None, "IMAP_SERVER not configured"
    assert EMAIL_ADDRESS is not None, "EMAIL_ADDRESS not configured"
    assert EMAIL_PASSWORD is not None, "EMAIL_PASSWORD not configured"
    logger.debug(f"Connecting to IMAP {IMAP_SERVER}:{IMAP_PORT}")
    imap = imaplib.IMAP4_SSL(IMAP_SERVER, IMAP_PORT, timeout=CONNECTION_TIMEOUT)
    imap.login(EMAIL_ADDRESS, EMAIL_PASSWORD)
    return imap


def connect_smtp() -> Union[smtplib.SMTP, smtplib.SMTP_SSL]:
    """Connect to SMTP server with configured timeout."""
    assert SMTP_SERVER is not None, "SMTP_SERVER not configured"
    assert EMAIL_ADDRESS is not None, "EMAIL_ADDRESS not configured"
    assert EMAIL_PASSWORD is not None, "EMAIL_PASSWORD not configured"
    logger.debug(f"Connecting to SMTP {SMTP_SERVER}:{SMTP_PORT}")
    if SMTP_PORT == 465:
        smtp = smtplib.SMTP_SSL(SMTP_SERVER, SMTP_PORT, timeout=CONNECTION_TIMEOUT)
    else:
        smtp = smtplib.SMTP(SMTP_SERVER, SMTP_PORT, timeout=CONNECTION_TIMEOUT)
        smtp.starttls()
    smtp.login(EMAIL_ADDRESS, EMAIL_PASSWORD)
    return smtp


class ConnectionManager:
    """Reuses IMAP/SMTP connections with health checks."""

    def __init__(self, max_age: int = CONNECTION_MAX_AGE) -> None:
        self._imap: Optional[imaplib.IMAP4_SSL] = None
        self._smtp: Optional[Union[smtplib.SMTP, smtplib.SMTP_SSL]] = None
        self._imap_time: Optional[float] = None
        self._smtp_time: Optional[float] = None
        self._max_age = max_age

    def get_imap(self) -> imaplib.IMAP4_SSL:
        if self._should_refresh(self._imap_time):
            self._close_imap()
        if self._imap is None:
            logger.debug("Creating new IMAP connection")
            self._imap = connect_imap()
            self._imap_time = time.time()
        return self._imap

    def get_smtp(self) -> Union[smtplib.SMTP, smtplib.SMTP_SSL]:
        if self._should_refresh(self._smtp_time):
            self._close_smtp()
        if self._smtp is None:
            logger.debug("Creating new SMTP connection")
            self._smtp = connect_smtp()
            self._smtp_time = time.time()
        return self._smtp

    def _should_refresh(self, created: Optional[float]) -> bool:
        return created is None or (time.time() - created) > self._max_age

    def _close_imap(self) -> None:
        if self._imap:
            try:
                self._imap.logout()
            except Exception as e:
                logger.debug(f"Error closing IMAP connection: {e}")
            self._imap = None
            self._imap_time = None

    def _close_smtp(self) -> None:
        if self._smtp:
            try:
                self._smtp.quit()
            except Exception as e:
                logger.debug(f"Error closing SMTP connection: {e}")
            self._smtp = None
            self._smtp_time = None

    def invalidate(self) -> None:
        """Force close all connections (call after errors)."""
        logger.debug("Invalidating all connections")
        self._close_imap()
        self._close_smtp()
