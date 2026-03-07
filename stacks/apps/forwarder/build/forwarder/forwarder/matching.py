"""Pattern matching and keyword filtering for email rules."""
import re
from fnmatch import fnmatch
from typing import Union

from .types import KeywordFilter


def match_sender_pattern(sender: str, pattern: str) -> bool:
    """Match sender against pattern with wildcard support.

    Pattern formats:
    - user@example.com: Exact match only
    - *@example.com: Any user at example.com
    - *@*.example.com: Any user at any subdomain
    - *.example.com: Shorthand for *@*.example.com
    """
    sender = sender.lower()
    pattern = pattern.lower()

    # If no wildcards, exact match
    if '*' not in pattern:
        return sender == pattern

    # Expand shorthand: *.domain.com -> *@*.domain.com
    if pattern.startswith('*.') and '@' not in pattern:
        pattern = '*@' + pattern

    return fnmatch(sender, pattern)


def normalize_keyword_filter(
    filter_value: Union[list[str], KeywordFilter, None]
) -> KeywordFilter:
    """Normalize include/exclude values for backward compatibility.

    - If list: treat as subject-only (array format)
    - If dict: use as-is (object format)
    - If None: return empty dict
    """
    if filter_value is None:
        return {}
    if isinstance(filter_value, list):
        return {'subject': filter_value} if filter_value else {}
    return filter_value


def check_keywords(
    subject: str,
    body: str,
    filter_config: KeywordFilter,
) -> bool:
    """Check if any keyword in filter matches subject or body.

    Returns True if ANY keyword matches in ANY specified source (OR logic).
    Returns False if filter is empty (no keywords to match).

    Supports both substring matching (subject/body) and word boundary
    matching (subject_words/body_words) using regex \\b anchors.
    """
    if not filter_config:
        return False

    subject_lower = subject.lower()
    body_lower = body.lower()

    # Check subject keywords (substring)
    subject_kws = filter_config.get('subject', [])
    if subject_kws and any(kw in subject_lower for kw in subject_kws):
        return True

    # Check body keywords (substring)
    body_kws = filter_config.get('body', [])
    if body_kws and any(kw in body_lower for kw in body_kws):
        return True

    # Check subject keywords (word boundary)
    subject_words = filter_config.get('subject_words', [])
    if subject_words:
        for word in subject_words:
            if re.search(rf'\b{re.escape(word)}\b', subject_lower):
                return True

    # Check body keywords (word boundary)
    body_words = filter_config.get('body_words', [])
    if body_words:
        for word in body_words:
            if re.search(rf'\b{re.escape(word)}\b', body_lower):
                return True

    return False


def _format_keyword_filter(
    filter_value: Union[list[str], KeywordFilter],
    prefix: str,
) -> str:
    """Format keyword filter for logging."""
    if not filter_value:
        return ''

    if isinstance(filter_value, list):
        return f"{prefix}: {', '.join(filter_value)}"

    # KeywordFilter dict format
    parts = []
    if 'subject' in filter_value and filter_value['subject']:
        parts.append(f"subj[{', '.join(filter_value['subject'])}]")
    if 'body' in filter_value and filter_value['body']:
        parts.append(f"body[{', '.join(filter_value['body'])}]")

    return f"{prefix}: {' | '.join(parts)}" if parts else ''


def _is_wildcard_pattern(pattern: str) -> bool:
    """Check if pattern contains wildcards."""
    return '*' in pattern
