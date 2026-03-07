"""Email parsing utilities for Email Forwarder."""
import logging
from email.message import Message
from email.utils import parseaddr

logger = logging.getLogger(__name__)


def get_sender_email(msg: Message) -> str:
    """Extract sender email address from message."""
    from_header = msg.get('From', '')
    _, email_addr = parseaddr(from_header)
    return email_addr.lower() if email_addr else from_header.lower()


def _decode_payload(part: Message) -> str:
    """Safely decode email payload to string."""
    try:
        payload = part.get_payload(decode=True)
        if payload is None:
            return str(part.get_payload())
        if isinstance(payload, bytes):
            return payload.decode('utf-8', errors='replace')
        return str(payload)
    except Exception as e:
        logger.debug(f"Error decoding payload: {e}")
        return str(part.get_payload())


def get_email_body(msg: Message) -> str:
    """Extract full body content from email (plain text + HTML concatenated)."""
    parts: list[str] = []

    if msg.is_multipart():
        for part in msg.walk():
            content_type = part.get_content_type()
            content_disposition = str(part.get("Content-Disposition", ""))

            # Skip attachments
            if "attachment" in content_disposition:
                continue

            if content_type in ("text/plain", "text/html"):
                decoded = _decode_payload(part)
                if decoded:
                    parts.append(decoded)
    else:
        parts.append(_decode_payload(msg))

    return '\n'.join(parts)
