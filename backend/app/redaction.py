"""Best-effort removal of recognizable secrets from log text.

GitHub already masks secrets it knows about (as ***), but logs can still contain tokens that
were never registered as secrets — printed env vars, credentials in URLs, private keys.
This is defense in depth, not a guarantee: unknown secret formats will get through.
"""

import re

REDACTED = "[REDACTED]"

# (pattern, replacement). Order matters: multi-line and specific formats first.
_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    # PEM private keys (whole block).
    (
        re.compile(
            r"-----BEGIN [A-Z0-9 ]*PRIVATE KEY-----.*?-----END [A-Z0-9 ]*PRIVATE KEY-----",
            re.DOTALL,
        ),
        REDACTED,
    ),
    # GitHub tokens: classic (ghp_, gho_, ghu_, ghs_, ghr_) and fine-grained.
    (re.compile(r"\bgh[pousr]_[A-Za-z0-9]{36,255}\b"), REDACTED),
    (re.compile(r"\bgithub_pat_[A-Za-z0-9_]{22,255}\b"), REDACTED),
    # AWS access key ids.
    (re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b"), REDACTED),
    # Slack tokens.
    (re.compile(r"\bxox[abprs]-[A-Za-z0-9-]{10,}\b"), REDACTED),
    # "sk-..." style API keys (OpenAI, Anthropic, Stripe secret keys, ...).
    (re.compile(r"\bsk-[A-Za-z0-9_-]{20,}"), REDACTED),
    # JSON Web Tokens.
    (re.compile(r"\beyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}"), REDACTED),
    # Credentials embedded in URLs: https://user:password@host
    (
        re.compile(r"(?P<scheme>[a-zA-Z][a-zA-Z0-9+.-]*://)[^/\s:@]+:[^/\s@]+@"),
        r"\g<scheme>" + REDACTED + "@",
    ),
    # Authorization headers.
    (re.compile(r"(?i)(authorization:\s*(?:bearer|basic|token)\s+)\S+"), r"\1" + REDACTED),
    # key=value / key: value where the key name suggests a secret.
    (
        re.compile(
            r"(?i)\b((?:[A-Z0-9_]*_)?(?:password|passwd|secret|token|api[_-]?key|access[_-]?key)"
            r"[A-Z0-9_]*)(\s*[:=]\s*)([\"']?)[^\s\"']{6,}\3"
        ),
        r"\1\2\3" + REDACTED + r"\3",
    ),
]


def redact_secrets(text: str) -> str:
    for pattern, replacement in _PATTERNS:
        text = pattern.sub(replacement, text)
    return text
