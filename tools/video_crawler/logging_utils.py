"""清理日志中的认证信息和敏感 URL 查询参数。"""

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
    """隐藏 Header 和 URL 中的认证令牌后返回可展示文本。

    该函数在日志信号和报告格式化的最后边界调用，允许上游继续使用
    原始认证值发起请求，同时确保 UI、终端和错误弹窗只看到替代标记。
    """
    value = "" if text is None else str(text)
    for header in SENSITIVE_HEADER_NAMES:
        # Header 值可能与其他 Header 或查询参数出现在同一行，前瞻用于
        # 在下一个字段边界停止，避免把整条诊断信息一起吞掉。
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
