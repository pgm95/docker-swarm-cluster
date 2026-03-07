"""Content extraction and formatted email creation for Email Forwarder."""
import html
import logging
import re
from email.message import Message
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path
from string import Template
from typing import Optional

from .config import EMAIL_ADDRESS
from .connection import connect_smtp
from .parsing import get_email_body

logger = logging.getLogger(__name__)

# Template cache
_TEMPLATE_DIR = Path(__file__).parent / 'templates'
_TEMPLATES: dict[str, Template] = {}


def _load_template(name: str) -> Template:
    """Load and cache HTML template."""
    if name not in _TEMPLATES:
        template_path = _TEMPLATE_DIR / f'{name}.html'
        _TEMPLATES[name] = Template(template_path.read_text())
    return _TEMPLATES[name]


def extract_matches(body: str, patterns: list[str]) -> list[str]:
    """Apply regex patterns to body and collect matches.

    Returns deduplicated list of matches. Uses capture group 1 if exists, else full match.
    """
    matches: list[str] = []
    seen: set[str] = set()

    for pattern in patterns:
        try:
            regex = re.compile(pattern)
            for match in regex.finditer(body):
                # Use capture group 1 if exists, else full match (group 0)
                if match.lastindex and match.lastindex >= 1:
                    value = match.group(1)
                else:
                    value = match.group(0)

                if value:
                    # Normalize HTML entities (e.g., &amp; -> &) for deduplication
                    normalized = html.unescape(value)
                    # Strip trailing chars commonly captured from HTML/text markup
                    normalized = normalized.rstrip('>"\')\u200c')
                    if normalized and normalized not in seen:
                        seen.add(normalized)
                        matches.append(normalized)
        except re.error as e:
            logger.warning(f"Invalid regex pattern '{pattern}': {e}")

    return matches


def create_extracted_message(
    original_msg: Message,
    recipient: str,
    matches: list[str],
    custom_subject: Optional[str] = None,
    display: str = 'link'
) -> MIMEMultipart:
    """Create an email with extracted content from the original message."""
    assert EMAIL_ADDRESS is not None, "EMAIL_ADDRESS not configured"

    extracted = MIMEMultipart('alternative')

    original_from = original_msg.get('From', 'Unknown')
    original_subject = original_msg.get('Subject', 'No Subject')
    original_date = original_msg.get('Date', 'Unknown')

    extracted['From'] = EMAIL_ADDRESS
    extracted['To'] = recipient
    email_subject = custom_subject or f"Extracted: {original_subject}"
    extracted['Subject'] = email_subject

    if display == 'code':
        # Plain text version for code
        plain_body = f"""{email_subject}

{matches[0]}

──────────────
From: {original_from}
Date: {original_date}
"""

        # HTML version for code display
        template = _load_template('extracted_code')
        html_body = template.substitute(
            subject=html.escape(email_subject),
            code=html.escape(matches[0]),
            **{'from': html.escape(original_from)},
            date=html.escape(original_date),
        )
    else:
        # Plain text version for link
        plain_body = f"""Click the link below to {email_subject}:

{matches[0]}

──────────────
From: {original_from}
Date: {original_date}
"""

        # HTML version with CTA button
        template = _load_template('extracted_link')
        html_body = template.substitute(
            subject=html.escape(email_subject),
            link=html.escape(matches[0]),
            **{'from': html.escape(original_from)},
            date=html.escape(original_date),
        )

    extracted.attach(MIMEText(plain_body, 'plain'))
    extracted.attach(MIMEText(html_body, 'html'))
    return extracted


def extract_and_send(
    original_msg: Message,
    recipient: str,
    patterns: list[str],
    custom_subject: Optional[str] = None,
    display: str = 'link'
) -> bool:
    """Extract content from email and send to recipient.

    Returns True if email was sent, False if no matches found.
    """
    assert EMAIL_ADDRESS is not None, "EMAIL_ADDRESS not configured"

    body = get_email_body(original_msg)
    matches = extract_matches(body, patterns)[:1]  # Only use first match

    if not matches:
        subject = original_msg.get('Subject', 'No Subject')
        logger.debug(f"No matches found for extraction, skipping send to {recipient}: {subject}")
        return False

    subject = original_msg.get('Subject', 'No Subject')
    smtp = None
    try:
        smtp = connect_smtp()
        extracted_msg = create_extracted_message(original_msg, recipient, matches, custom_subject, display)
        smtp.sendmail(EMAIL_ADDRESS, recipient, extracted_msg.as_string())
        logger.info(f"Extracted {len(matches)} match(es) and sent to {recipient}: {subject}")
        return True
    except Exception as e:
        logger.error(f"Failed to send extracted email to {recipient}: {e}", exc_info=True)
        return False
    finally:
        if smtp:
            try:
                smtp.quit()
            except Exception:
                pass
