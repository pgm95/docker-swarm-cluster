"""Main email processing loop for Email Forwarder."""
import email
import logging
from email.message import Message
from typing import Optional

from .config import (
    IMAP_FLAG_DELETED,
    IMAP_FLAG_SEEN,
    IMAP_FOLDER,
    MARK_AS_READ,
    MAX_EMAILS_PER_CYCLE,
    MOVE_TO_FOLDER,
    OPERATION_TIMEOUT,
    RULES,
)
from .connection import ConnectionManager, OperationTimeout, run_with_timeout
from .extraction import extract_and_send
from .forwarding import forward_email
from .matching import check_keywords, match_sender_pattern, normalize_keyword_filter
from .parsing import get_email_body, get_sender_email
from .types import ActionConfig, Rule

logger = logging.getLogger(__name__)

# Graceful shutdown flag (set by runner.py signal handler)
_shutdown_requested = False


def set_shutdown_requested(value: bool) -> None:
    """Set the shutdown flag."""
    global _shutdown_requested
    _shutdown_requested = value


def is_shutdown_requested() -> bool:
    """Check if shutdown has been requested."""
    return _shutdown_requested


def get_matching_rules(msg: Message, body_cache: Optional[dict] = None) -> list[Rule]:
    """Find all rules that match this message.

    Matching logic:
    1. Check sender against patterns (wildcards supported)
    2. If include defined: at least one keyword must match in subject/body (OR)
    3. If exclude defined: NO keyword can match in subject/body
    4. If both include and exclude match: EXCLUDE wins
    5. Empty include = match all emails from sender

    Args:
        msg: Email message to match
        body_cache: Optional dict to cache body extraction (set 'body' key)
    """
    sender = get_sender_email(msg)
    subject = msg.get('Subject') or ''
    matching: list[Rule] = []

    # Lazy body extraction (only compute once if needed)
    body: Optional[str] = None
    if body_cache is not None and 'body' in body_cache:
        body = body_cache['body']

    def get_body() -> str:
        nonlocal body
        if body is None:
            body = get_email_body(msg)
            if body_cache is not None:
                body_cache['body'] = body
        return body

    for rule in RULES:
        # Skip disabled rules
        if not rule.get('enabled', True):
            continue

        match_config = rule['match']

        # Check sender against patterns (wildcards supported)
        sender_matched = any(
            match_sender_pattern(sender, pattern)
            for pattern in match_config['senders']
        )
        if not sender_matched:
            continue

        # Normalize include/exclude filters
        include_filter = normalize_keyword_filter(match_config.get('include'))
        exclude_filter = normalize_keyword_filter(match_config.get('exclude'))

        # Check include keywords (OR logic - any must match)
        if include_filter:
            # Only fetch body if needed for body keywords
            needs_body = bool(include_filter.get('body'))
            current_body = get_body() if needs_body else ''
            if not check_keywords(subject, current_body, include_filter):
                continue

        # Check exclude keywords (blocks if ANY match) - exclude wins over include
        if exclude_filter:
            needs_body = bool(exclude_filter.get('body'))
            current_body = get_body() if needs_body else ''
            if check_keywords(subject, current_body, exclude_filter):
                logger.debug(f"Rule '{rule['name']}' excluded by keyword match")
                continue

        matching.append(rule)

    return matching


def _check_action_filter(
    action: ActionConfig,
    subject: str,
    body: str,
    rule_name: str,
) -> bool:
    """Check if action passes its filter (if any).

    Returns True if action should execute, False if filtered out.
    """
    action_filter = action.get('filter')
    if not action_filter:
        return True

    include_filter = normalize_keyword_filter(action_filter.get('include'))
    exclude_filter = normalize_keyword_filter(action_filter.get('exclude'))

    # Check include: must match at least one keyword
    if include_filter and not check_keywords(subject, body, include_filter):
        logger.debug(f"Action in rule '{rule_name}' skipped: include filter not matched")
        return False

    # Check exclude: must not match any keyword
    if exclude_filter and check_keywords(subject, body, exclude_filter):
        logger.debug(f"Action in rule '{rule_name}' skipped: exclude filter matched")
        return False

    return True


def process_emails(conn_manager: ConnectionManager) -> int:
    """Check for new emails and forward matching ones.

    Returns the number of emails processed.
    """
    processed = 0
    try:
        imap = conn_manager.get_imap()

        def select_folder():
            return imap.select(IMAP_FOLDER)
        run_with_timeout(select_folder, OPERATION_TIMEOUT, "IMAP select")

        def search_unseen():
            return imap.search(None, 'UNSEEN')
        status, messages = run_with_timeout(search_unseen, OPERATION_TIMEOUT, "IMAP search")

        if status != 'OK':
            logger.error("Failed to search emails")
            return 0

        email_ids = messages[0].split()

        if not email_ids:
            logger.debug("No new emails")
            return 0

        total_emails = len(email_ids)
        if total_emails > MAX_EMAILS_PER_CYCLE:
            logger.debug(f"Found {total_emails} unread email(s), processing first {MAX_EMAILS_PER_CYCLE}")
            email_ids = email_ids[:MAX_EMAILS_PER_CYCLE]
        else:
            logger.debug(f"Found {total_emails} unread email(s)")

        for email_id in email_ids:
            if _shutdown_requested:
                logger.info("Shutdown requested, stopping email processing")
                break

            try:
                def fetch_email():
                    return imap.fetch(email_id, '(BODY.PEEK[])')
                status, msg_data = run_with_timeout(fetch_email, OPERATION_TIMEOUT, "IMAP fetch")

                if status != 'OK' or not msg_data or not msg_data[0]:
                    continue

                raw_email = msg_data[0][1]
                if not isinstance(raw_email, bytes):
                    logger.warning(f"Unexpected email data type: {type(raw_email)}")
                    continue
                msg = email.message_from_bytes(raw_email)

                sender = get_sender_email(msg)
                subject = msg.get('Subject', 'No Subject')

                # Use body cache for efficiency (body extracted once)
                body_cache: dict = {}
                matching_rules = get_matching_rules(msg, body_cache)

                if matching_rules:
                    # Get body for action filters (may already be cached)
                    body = body_cache.get('body') or get_email_body(msg)

                    # Collect actions from all matching rules
                    forward_recipients: set[str] = set()
                    extract_tasks: list[tuple[str, list[str], Optional[str], str]] = []  # (recipient, patterns, subject, display)
                    rule_names: list[str] = []

                    for rule in matching_rules:
                        rule_names.append(rule['name'])
                        for action in rule['actions']:
                            # Check action-level filter
                            if not _check_action_filter(action, subject, body, rule['name']):
                                continue

                            if action['type'] == 'forward':
                                forward_recipients.update(action['recipients'])
                            elif action['type'] == 'extract':
                                custom_subject = action.get('subject')
                                patterns = action.get('patterns', [])
                                display = action.get('display', 'link')
                                for recipient in action['recipients']:
                                    extract_tasks.append((recipient, patterns, custom_subject, display))

                    logger.info(f"Processing email from {sender}: {subject} (rules: {', '.join(rule_names)})")

                    # Execute forward actions
                    if forward_recipients:
                        forward_email(msg, list(forward_recipients))

                    # Execute extract actions
                    for recipient, patterns, custom_subject, display in extract_tasks:
                        extract_and_send(msg, recipient, patterns, custom_subject, display)

                    if MARK_AS_READ:
                        imap.store(email_id, '+FLAGS', IMAP_FLAG_SEEN)

                    if MOVE_TO_FOLDER:
                        try:
                            imap.copy(email_id, MOVE_TO_FOLDER)
                            imap.store(email_id, '+FLAGS', IMAP_FLAG_DELETED)
                            imap.expunge()
                        except Exception as e:
                            logger.warning(f"Could not move email to {MOVE_TO_FOLDER}: {e}")

                    processed += 1
                else:
                    logger.debug(f"Skipping email from {sender} (no matching rules)")

            except OperationTimeout:
                logger.error(f"Timeout processing email {email_id}, skipping")
                conn_manager.invalidate()
            except Exception as e:
                logger.error(f"Error processing email {email_id}: {e}", exc_info=True)

    except OperationTimeout:
        logger.error("IMAP operation timed out")
        conn_manager.invalidate()
        raise
    except Exception as e:
        logger.error(f"Error in process_emails: {e}", exc_info=True)
        raise

    return processed
