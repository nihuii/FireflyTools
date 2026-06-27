import os
from pathlib import Path
from unittest import TestCase
from unittest.mock import patch

from tools.runtime_setup import configure_bundled_ffmpeg


class RuntimeSetupTests(TestCase):
    def test_configure_bundled_ffmpeg_prepends_tools_directory(self):
        tools_dir = Path(__file__).resolve().parents[1] / "tools"

        with patch.dict(os.environ, {"PATH": r"C:\Windows\System32"}):
            executable = configure_bundled_ffmpeg(tools_dir)

            self.assertEqual(executable, tools_dir / "ffmpeg.exe")
            self.assertEqual(
                Path(os.environ["PATH"].split(os.pathsep)[0]),
                tools_dir,
            )
