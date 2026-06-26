import re


SENSITIVE_QUERY_KEYS = (
    "token",
    "access_token",
    "auth",
    "authorization",
    "signature",
    "sig",
    "key",
)

SENSITIVE_HEADER_NAMES = (
    "cookie",
    "authorization",
    "x-token",
    "x-auth-token",
)


def redact_for_display(text: object) -> str:
    value = "" if text is None else str(text)
    for header in SENSITIVE_HEADER_NAMES:
        value = re.sub(
            rf"(?i)\b{re.escape(header)}:\s*.*?(?=\s+[A-Za-z-]+:|\s+\w+=|\r?\n|$)",
            lambda match: match.group(0).split(":", 1)[0] + ": <redacted>",
            value,
        )
    for key in SENSITIVE_QUERY_KEYS:
        value = re.sub(
            rf"(?i)(^|[?&;\s])({re.escape(key)}=)[^\s&#;]+",
            lambda match: match.group(1) + match.group(2) + "<redacted>",
            value,
        )
    return value
