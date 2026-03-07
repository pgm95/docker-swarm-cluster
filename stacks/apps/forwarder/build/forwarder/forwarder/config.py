"""Configuration loading and validation for Email Forwarder."""
import json
import logging
import os
import re
from pathlib import Path
from typing import Union

from .types import ActionConfig, ActionFilter, KeywordFilter, MatchConfig, Rule
from .matching import _format_keyword_filter, _is_wildcard_pattern

logger = logging.getLogger(__name__)

# Email validation pattern (basic RFC 5322)
EMAIL_PATTERN = re.compile(r'^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$')

# Pattern for ${FWD_*} environment variable references in JSON config values
_ENV_VAR_PATTERN = re.compile(r'\$\{(FWD_[A-Za-z0-9_]+)\}')


def _resolve_var(name: str) -> str | None:
    """Resolve an environment variable, supporting Docker Swarm _FILE convention.

    If {name}_FILE is set, reads the secret from the referenced file path.
    Falls back to os.environ.get(name) if no _FILE variant exists.
    """
    file_key = f"{name}_FILE"
    file_path = os.environ.get(file_key)
    if file_path is not None:
        path = Path(file_path)
        if not path.is_file():
            raise ValueError(f"{file_key}={file_path} — file does not exist or is not readable")
        try:
            content = path.read_text(encoding='utf-8').rstrip()
        except OSError as e:
            raise ValueError(f"{file_key}={file_path} — cannot read file: {e}")
        if not content:
            raise ValueError(f"{file_key}={file_path} — file is empty after stripping whitespace")
        return content
    return os.environ.get(name)


def _safe_int(value: str, default: int, name: str) -> int:
    """Safely parse integer from string with error logging."""
    try:
        return int(value)
    except (ValueError, TypeError):
        logger.warning(f"Invalid value for {name}: '{value}', using default: {default}")
        return default


def _is_valid_email(email_addr: str) -> bool:
    """Validate email address format."""
    return bool(EMAIL_PATTERN.match(email_addr))


# Configuration from environment variables (supports _FILE suffix for Docker Swarm secrets)
IMAP_SERVER = _resolve_var('IMAP_SERVER')
IMAP_PORT = _safe_int(os.environ.get('IMAP_PORT', '993'), 993, 'IMAP_PORT')
SMTP_SERVER = _resolve_var('SMTP_SERVER')
SMTP_PORT = _safe_int(os.environ.get('SMTP_PORT', '465'), 465, 'SMTP_PORT')

EMAIL_ADDRESS = _resolve_var('EMAIL_ADDRESS')
EMAIL_PASSWORD = _resolve_var('EMAIL_PASSWORD')  # App Password recommended

# Config file path (JSON format)
CONFIG_FILE = Path(os.environ.get('CONFIG_FILE', '/config/config.json'))

# Loaded rules (populated by load_config)
RULES: list[Rule] = []

# How often to check for new emails (seconds)
POLL_INTERVAL = _safe_int(os.environ.get('POLL_INTERVAL', '60'), 60, 'POLL_INTERVAL')

# Folder to monitor
IMAP_FOLDER = os.environ.get('IMAP_FOLDER', 'INBOX')

# Mark forwarded emails as read
MARK_AS_READ = os.environ.get('MARK_AS_READ', 'true').lower() == 'true'

# Use a custom label/folder to track forwarded emails (move after forwarding)
MOVE_TO_FOLDER = os.environ.get('MOVE_TO_FOLDER', '')  # Empty = don't move

# Timeouts and resilience
CONNECTION_TIMEOUT = _safe_int(os.environ.get('CONNECTION_TIMEOUT', '30'), 30, 'CONNECTION_TIMEOUT')
OPERATION_TIMEOUT = _safe_int(os.environ.get('OPERATION_TIMEOUT', '60'), 60, 'OPERATION_TIMEOUT')
MAX_BACKOFF = 600  # 10 minutes max backoff
BACKOFF_MULTIPLIER = 2
HEARTBEAT_INTERVAL = _safe_int(os.environ.get('HEARTBEAT_INTERVAL', '300'), 300, 'HEARTBEAT_INTERVAL')
CONNECTION_MAX_AGE = _safe_int(os.environ.get('CONNECTION_MAX_AGE', '300'), 300, 'CONNECTION_MAX_AGE')

# Batch processing limit
MAX_EMAILS_PER_CYCLE = _safe_int(os.environ.get('MAX_EMAILS_PER_CYCLE', '50'), 50, 'MAX_EMAILS_PER_CYCLE')

# Health check file (touched after each successful cycle)
HEALTH_FILE = Path('/tmp/forwarder_healthy')

# IMAP flag constants
IMAP_FLAG_SEEN = '\\Seen'
IMAP_FLAG_DELETED = '\\Deleted'


def _validate_keyword_filter(
    value: Union[list, dict, None],
    context: str,
) -> Union[list[str], KeywordFilter]:
    """Validate and normalize keyword filter (include/exclude).

    Accepts:
    - list[str]: array format (subject-only)
    - dict with subject/body/subject_words/body_words keys: object format
    - None: treated as empty

    Returns normalized value (list or KeywordFilter dict).
    """
    if value is None:
        return []

    if isinstance(value, list):
        # Array format: list of keywords (subject-only)
        for kw in value:
            if not isinstance(kw, str):
                raise ValueError(f"{context} list items must be strings")
        return [k.strip().lower() for k in value if k.strip()]

    if isinstance(value, dict):
        # Object format: dict with subject/body/subject_words/body_words keys
        result: KeywordFilter = {}
        valid_keys = ('subject', 'body', 'subject_words', 'body_words')
        for key in valid_keys:
            if key in value:
                kws = value[key]
                if not isinstance(kws, list):
                    raise ValueError(f"{context}.{key} must be a list")
                for kw in kws:
                    if not isinstance(kw, str):
                        raise ValueError(f"{context}.{key} items must be strings")
                result[key] = [k.strip().lower() for k in kws if k.strip()]
        # Check for unknown keys
        unknown = set(value.keys()) - set(valid_keys)
        if unknown:
            raise ValueError(f"{context} has unknown keys: {unknown}")
        return result

    raise ValueError(f"{context} must be a list or object")


def _validate_action_filter(
    filter_data: dict,
    context: str,
) -> ActionFilter:
    """Validate action-level filter."""
    result: ActionFilter = {}

    if 'include' in filter_data:
        result['include'] = _validate_keyword_filter(
            filter_data['include'], f"{context}.include"
        )
    if 'exclude' in filter_data:
        result['exclude'] = _validate_keyword_filter(
            filter_data['exclude'], f"{context}.exclude"
        )

    unknown = set(filter_data.keys()) - {'include', 'exclude'}
    if unknown:
        raise ValueError(f"{context} has unknown keys: {unknown}")

    return result


def _find_env_refs(config) -> set[str]:
    """Recursively collect all ${FWD_*} variable names referenced in config."""
    refs: set[str] = set()
    if isinstance(config, str):
        refs.update(m.group(1) for m in _ENV_VAR_PATTERN.finditer(config))
    elif isinstance(config, list):
        for item in config:
            refs.update(_find_env_refs(item))
    elif isinstance(config, dict):
        for value in config.values():
            refs.update(_find_env_refs(value))
    return refs


def _substitute_string(value: str) -> str:
    """Replace all ${FWD_*} references in a single string value.

    Raises ValueError listing all missing variables.
    """
    missing = []

    def replacer(match):
        var_name = match.group(1)
        var_value = _resolve_var(var_name)
        if var_value is None:
            missing.append(var_name)
            return match.group(0)
        return var_value

    result = _ENV_VAR_PATTERN.sub(replacer, value)

    if missing:
        raise ValueError(
            f"Missing environment variable(s): {', '.join(missing)} "
            f"(referenced in config value: \"{value}\")"
        )

    return result


def _substitute_env_vars(config):
    """Recursively substitute ${FWD_*} env var references in config values."""
    if isinstance(config, str):
        return _substitute_string(config)
    if isinstance(config, list):
        return [_substitute_env_vars(item) for item in config]
    if isinstance(config, dict):
        return {key: _substitute_env_vars(value) for key, value in config.items()}
    return config


def _resolve_env_vars(config):
    """Resolve ${FWD_*} references in config with logging."""
    refs = _find_env_refs(config)
    if not refs:
        return config

    logger.info(f"Resolving {len(refs)} environment variable(s) in config: {', '.join(sorted(refs))}")
    return _substitute_env_vars(config)


def load_config() -> list[Rule]:
    """Load rules from JSON config file."""
    if not CONFIG_FILE.exists():
        raise FileNotFoundError(f"Config file not found: {CONFIG_FILE}")

    try:
        with open(CONFIG_FILE, 'r', encoding='utf-8') as f:
            config = json.load(f)
    except json.JSONDecodeError as e:
        raise ValueError(f"Invalid JSON in config file: {e}")

    # Resolve ${FWD_*} environment variable references before validation
    config = _resolve_env_vars(config)

    if not isinstance(config, dict):
        raise ValueError("Config must be a JSON object")

    rules = config.get('rules', [])
    if not isinstance(rules, list) or len(rules) == 0:
        raise ValueError("Config must have at least one rule in 'rules' array")

    loaded_rules: list[Rule] = []
    for i, rule_data in enumerate(rules):
        if not isinstance(rule_data, dict):
            raise ValueError(f"Rule {i} must be an object")

        name = rule_data.get('name')
        if not name:
            raise ValueError(f"Rule {i} must have a 'name' field")

        match_data = rule_data.get('match')
        if not isinstance(match_data, dict):
            raise ValueError(f"Rule '{name}' must have a 'match' object")

        senders = match_data.get('senders', [])
        if not isinstance(senders, list) or len(senders) == 0:
            raise ValueError(f"Rule '{name}' match.senders must have at least one sender")

        # Validate include/exclude (list or object format)
        include_filter = _validate_keyword_filter(
            match_data.get('include'), f"Rule '{name}' match.include"
        )
        exclude_filter = _validate_keyword_filter(
            match_data.get('exclude'), f"Rule '{name}' match.exclude"
        )

        actions = rule_data.get('actions', [])
        if not isinstance(actions, list) or len(actions) == 0:
            raise ValueError(f"Rule '{name}' must have at least one action")

        validated_actions: list[ActionConfig] = []
        for j, action_data in enumerate(actions):
            if not isinstance(action_data, dict):
                raise ValueError(f"Rule '{name}' action {j} must be an object")

            action_type = action_data.get('type')
            if action_type not in ('forward', 'extract'):
                raise ValueError(f"Rule '{name}' action {j} type must be 'forward' or 'extract'")

            recipients = action_data.get('recipients', [])
            if not isinstance(recipients, list) or len(recipients) == 0:
                raise ValueError(f"Rule '{name}' action {j} must have at least one recipient")

            action: ActionConfig = {
                'type': action_type,
                'recipients': [r.strip() for r in recipients],
            }

            if action_type == 'extract':
                patterns = action_data.get('patterns', [])
                if not isinstance(patterns, list) or len(patterns) == 0:
                    raise ValueError(f"Rule '{name}' extract action {j} must have at least one pattern")
                # Validate regex patterns compile
                for p_idx, pattern in enumerate(patterns):
                    try:
                        re.compile(pattern)
                    except re.error as e:
                        raise ValueError(f"Rule '{name}' action {j} pattern {p_idx} invalid regex: {e}")
                action['patterns'] = patterns
                # Optional custom subject
                if 'subject' in action_data:
                    action['subject'] = action_data['subject']
                # Optional display type: 'link' (default) or 'code'
                display = action_data.get('display', 'link')
                if display not in ('link', 'code'):
                    raise ValueError(f"Rule '{name}' action {j} display must be 'link' or 'code'")
                action['display'] = display

            # Validate action-level filter
            if 'filter' in action_data:
                filter_data = action_data['filter']
                if not isinstance(filter_data, dict):
                    raise ValueError(f"Rule '{name}' action {j} filter must be an object")
                action['filter'] = _validate_action_filter(
                    filter_data, f"Rule '{name}' action {j} filter"
                )

            validated_actions.append(action)

        loaded_rules.append(Rule(
            name=name,
            enabled=rule_data.get('enabled', True),
            match=MatchConfig(
                senders=[s.strip().lower() for s in senders],
                include=include_filter,
                exclude=exclude_filter,
            ),
            actions=validated_actions,
        ))

    return loaded_rules


def validate_config() -> None:
    """Validate required configuration, load rules, and test connection."""
    global RULES
    errors = []
    warnings = []

    # Validate server/credential env vars
    if not IMAP_SERVER:
        errors.append("IMAP_SERVER is required (e.g., imap.gmail.com, imap.mail.yahoo.com)")
    if not SMTP_SERVER:
        errors.append("SMTP_SERVER is required (e.g., smtp.gmail.com, smtp.mail.yahoo.com)")

    if not EMAIL_ADDRESS:
        errors.append("EMAIL_ADDRESS is required")
    elif not _is_valid_email(EMAIL_ADDRESS):
        warnings.append(f"EMAIL_ADDRESS '{EMAIL_ADDRESS}' may not be a valid email format")

    if not EMAIL_PASSWORD:
        errors.append("EMAIL_PASSWORD is required (use App Password for Gmail/Yahoo)")

    # Load and validate rules from JSON config
    try:
        RULES = load_config()
    except (FileNotFoundError, ValueError) as e:
        errors.append(str(e))

    # Validate email formats in rules (skip wildcards for senders)
    if RULES:
        for rule in RULES:
            for addr in rule['match']['senders']:
                if not _is_wildcard_pattern(addr) and not _is_valid_email(addr):
                    warnings.append(f"Rule '{rule['name']}' sender '{addr}' may not be a valid email format")
            for action in rule['actions']:
                for addr in action['recipients']:
                    if not _is_valid_email(addr):
                        warnings.append(f"Rule '{rule['name']}' recipient '{addr}' may not be a valid email format")

    for warning in warnings:
        logger.warning(warning)

    if errors:
        for error in errors:
            logger.error(error)
        raise ValueError("Configuration errors found")

    # Log configuration summary
    logger.info(f"Monitoring: {EMAIL_ADDRESS}")
    logger.info(f"Config file: {CONFIG_FILE}")
    logger.info(f"Loaded {len(RULES)} rule(s):")
    for rule in RULES:
        enabled_str = "" if rule.get('enabled', True) else " [DISABLED]"
        action_types = [a['type'] for a in rule['actions']]
        sender_count = len(rule['match']['senders'])

        # Format include/exclude filters
        include_filter = rule['match'].get('include', [])
        exclude_filter = rule['match'].get('exclude', [])
        kw_parts = []
        inc_str = _format_keyword_filter(include_filter, 'include')
        exc_str = _format_keyword_filter(exclude_filter, 'exclude')
        if inc_str:
            kw_parts.append(inc_str)
        if exc_str:
            kw_parts.append(exc_str)
        kw_str = '; '.join(kw_parts) if kw_parts else "(all subjects)"

        # Check for action-level filters
        actions_with_filters = sum(1 for a in rule['actions'] if 'filter' in a)
        filter_note = f" ({actions_with_filters} action filter(s))" if actions_with_filters else ""

        logger.info(f"  - {rule['name']}{enabled_str}: {sender_count} sender(s), actions: {', '.join(action_types)}, {kw_str}{filter_note}")
    logger.info(f"Poll interval: {POLL_INTERVAL}s, batch limit: {MAX_EMAILS_PER_CYCLE}")

    # Test IMAP connection at startup (fail fast on bad credentials)
    # Note: connect_imap is imported at module level in built version
    from .connection import connect_imap  # noqa: local import to avoid circular dependency
    try:
        logger.info("Testing IMAP connection...")
        imap = connect_imap()
        imap.logout()
        logger.info("IMAP connection test: OK")
    except Exception as e:
        raise ValueError(f"Cannot connect to IMAP server: {e}")
