from tools.video_crawler.logging_utils import redact_for_display
from tools.video_crawler.models import DiagnosticReport


def format_diagnostic_report(report: DiagnosticReport) -> str:
    return redact_for_display(report.to_user_summary())
