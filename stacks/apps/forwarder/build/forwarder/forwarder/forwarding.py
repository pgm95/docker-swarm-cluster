"""Email forwarding for Email Forwarder."""
import logging
from email.message import Message
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

from .config import EMAIL_ADDRESS
from .connection import connect_smtp
from .parsing import _decode_payload

logger = logging.getLogger(__name__)


def create_forwarded_message(original_msg: Message, recipient: str) -> MIMEMultipart:
    """Create a forwarded version of the message."""
    assert EMAIL_ADDRESS is not None, "EMAIL_ADDRESS not configured"
    fwd = MIMEMultipart()

    original_from = original_msg.get('From', 'Unknown')
    original_subject = original_msg.get('Subject', 'No Subject')
    original_date = original_msg.get('Date', 'Unknown')

    fwd['From'] = EMAIL_ADDRESS
    fwd['To'] = recipient
    fwd['Subject'] = f"Fwd: {original_subject}"

    forward_header = f"""
---------- Forwarded message ----------
From: {original_from}
Date: {original_date}
Subject: {original_subject}

"""

    body = ""
    attachments = []

    if original_msg.is_multipart():
        for part in original_msg.walk():
            content_type = part.get_content_type()
            content_disposition = str(part.get("Content-Disposition", ""))

            if "attachment" in content_disposition:
                attachments.append(part)
            elif content_type == "text/plain":
                body = _decode_payload(part)
            elif content_type == "text/html" and not body:
                body = _decode_payload(part)
    else:
        body = _decode_payload(original_msg)

    fwd.attach(MIMEText(forward_header + body, 'plain'))

    for attachment in attachments:
        fwd.attach(attachment)

    return fwd


def forward_email(original_msg: Message, recipients: list[str]) -> int:
    """Forward an email to specified recipients sequentially.

    Uses a single SMTP connection for all recipients to avoid rate limiting.
    Returns the number of successful sends.
    """
    assert EMAIL_ADDRESS is not None, "EMAIL_ADDRESS not configured"
    from_addr: str = EMAIL_ADDRESS
    subject = original_msg.get('Subject', 'No Subject')
    successful = 0
    smtp = None

    try:
        smtp = connect_smtp()
        for recipient in recipients:
            try:
                fwd_msg = create_forwarded_message(original_msg, recipient)
                smtp.sendmail(from_addr, recipient, fwd_msg.as_string())
                logger.info(f"Forwarded to {recipient}: {subject}")
                successful += 1
            except Exception as e:
                logger.error(f"Failed to forward to {recipient}: {e}", exc_info=True)
    except Exception as e:
        logger.error(f"SMTP connection failed: {e}", exc_info=True)
    finally:
        if smtp:
            try:
                smtp.quit()
            except Exception:
                pass

    return successful
