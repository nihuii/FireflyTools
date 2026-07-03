"""把结构化视频诊断报告格式化为经过脱敏的用户文本。"""

from tools.video_crawler.logging_utils import redact_for_display
from tools.video_crawler.models import DiagnosticReport


def format_diagnostic_report(report: DiagnosticReport) -> str:
    """格式化并脱敏诊断报告的候选、警告和错误。"""
    return redact_for_display(report.to_user_summary())
