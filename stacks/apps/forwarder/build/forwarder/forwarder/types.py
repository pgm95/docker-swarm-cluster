"""Type definitions for Email Forwarder configuration."""
from typing import NotRequired, Required, Union, TypedDict


class KeywordFilter(TypedDict, total=False):
    """Keyword filter for matching in subject/body."""
    subject: list[str]        # Keywords to match in subject (substring)
    body: list[str]           # Keywords to match in body (substring)
    subject_words: list[str]  # Keywords to match in subject (word boundary)
    body_words: list[str]     # Keywords to match in body (word boundary)


class MatchConfig(TypedDict):
    """Rule matching configuration."""
    senders: Required[list[str]]                              # Required, supports wildcards
    include: NotRequired[Union[list[str], KeywordFilter]]     # Array or object format
    exclude: NotRequired[Union[list[str], KeywordFilter]]     # Array or object format


class ActionFilter(TypedDict, total=False):
    """Action-level filter configuration."""
    include: Union[list[str], KeywordFilter]        # Action-level include filter
    exclude: Union[list[str], KeywordFilter]        # Action-level exclude filter


class ActionConfig(TypedDict):
    """Action configuration for a rule."""
    type: Required[str]                    # Required: 'forward' | 'extract'
    recipients: Required[list[str]]        # Required
    patterns: NotRequired[list[str]]       # Required for extract action only
    subject: NotRequired[str]              # Optional custom subject for extract action
    display: NotRequired[str]              # Optional: 'link' (default) or 'code' for extract
    filter: NotRequired[ActionFilter]      # Optional action-level filter


class Rule(TypedDict):
    """Complete rule configuration."""
    name: Required[str]                        # Required
    enabled: NotRequired[bool]                 # Optional, default True
    match: Required[MatchConfig]               # Required
    actions: Required[list[ActionConfig]]      # Required
