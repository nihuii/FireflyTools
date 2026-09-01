"""Provide safe confirmation UI for Edge media candidates."""

from datetime import timezone
from urllib.parse import urlsplit

from PyQt6.QtWidgets import QDialog, QDialogButtonBox, QLabel, QVBoxLayout, QWidget

from tools.video_crawler.models import EdgeCaptureCandidate


class EdgeCandidateDialog(QDialog):
    """Confirm an Edge candidate without displaying URLs or header secrets."""

    def __init__(
        self,
        candidate: EdgeCaptureCandidate,
        parent: QWidget | None = None,
    ) -> None:
        """Build a metadata-only confirmation dialog for ``candidate``."""
        super().__init__(parent)
        self.setWindowTitle("确认 Edge 候选")

        captured_at = candidate.captured_at.astimezone(timezone.utc)
        headers_present = "是" if candidate.headers else "否"
        details = (
            f"页面来源: {urlsplit(candidate.page_url).hostname or ''}",
            f"媒体主机: {urlsplit(candidate.media_url).hostname or ''}",
            f"媒体类型: {candidate.kind.value}",
            f"捕获时间 (UTC): {captured_at:%Y-%m-%d %H:%M:%S}",
            f"包含临时请求头: {headers_present}",
        )

        layout = QVBoxLayout(self)
        layout.addWidget(QLabel("确认使用以下 Edge 捕获候选？"))
        for detail in details:
            layout.addWidget(QLabel(detail))

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok
            | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
