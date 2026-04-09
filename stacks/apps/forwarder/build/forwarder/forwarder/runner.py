"""Main entry point for Email Forwarder."""
import logging
import signal
import sys
import time
from types import FrameType
from typing import Optional

from .config import (
    BACKOFF_MULTIPLIER,
    HEALTH_FILE,
    HEARTBEAT_INTERVAL,
    MAX_BACKOFF,
    POLL_INTERVAL,
    validate_config,
)
from .connection import ConnectionManager
from .processing import process_emails, set_shutdown_requested, is_shutdown_requested

logger = logging.getLogger(__name__)


def _shutdown_handler(signum: int, _frame: Optional[FrameType]) -> None:
    """Handle SIGTERM/SIGINT for graceful shutdown."""
    sig_name = signal.Signals(signum).name
    logger.info(f"Received {sig_name}, initiating graceful shutdown...")
    set_shutdown_requested(True)


def main() -> None:
    """Main loop with connection management and heartbeat logging."""
    # Configure logging
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(levelname)s - %(message)s'
    )

    # Register signal handlers for graceful shutdown
    signal.signal(signal.SIGTERM, _shutdown_handler)
    signal.signal(signal.SIGINT, _shutdown_handler)

    logger.info("Email Forwarder starting...")
    validate_config()

    conn_manager = ConnectionManager()
    consecutive_failures = 0
    last_heartbeat = time.time()

    logger.info(f"Starting email polling (every {POLL_INTERVAL}s)")

    while not is_shutdown_requested():
        # Heartbeat logging
        now = time.time()
        if now - last_heartbeat >= HEARTBEAT_INTERVAL:
            logger.debug(f"HEARTBEAT: consecutive_failures={consecutive_failures}")
            last_heartbeat = now

        try:
            processed = process_emails(conn_manager)
            consecutive_failures = 0
            sleep_time = POLL_INTERVAL
            HEALTH_FILE.touch()
            if processed > 0:
                logger.debug(f"Processed {processed} email(s) this cycle")
        except Exception as e:
            consecutive_failures += 1
            sleep_time = min(POLL_INTERVAL * (BACKOFF_MULTIPLIER ** consecutive_failures), MAX_BACKOFF)
            logger.error(f"Error in main loop: {e} (failure #{consecutive_failures}, retry in {sleep_time}s)", exc_info=True)
            conn_manager.invalidate()

        # Interruptible sleep for graceful shutdown
        sleep_end = time.time() + sleep_time
        while time.time() < sleep_end and not is_shutdown_requested():
            time.sleep(min(1, sleep_end - time.time()))

    # Graceful shutdown
    logger.info("Shutting down...")
    conn_manager.invalidate()
    logger.info("Email Forwarder stopped.")
    sys.exit(0)


if __name__ == '__main__':
    main()
