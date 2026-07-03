"""配置 FireflyTools 运行时依赖的本地可执行工具。"""

import os
from pathlib import Path


def configure_bundled_ffmpeg(tools_dir=None):
    """在 Windows 下把仓库内 FFmpeg 目录置于 PATH 首位并返回可执行文件。"""
    tools_path = Path(tools_dir or Path(__file__).resolve().parent).resolve()
    executable = tools_path / "ffmpeg.exe"
    if os.name != "nt" or not executable.is_file():
        return None

    path_entries = os.environ.get("PATH", "").split(os.pathsep)
    normalized_tools = os.path.normcase(str(tools_path))
    remaining = [
        entry
        for entry in path_entries
        if entry and os.path.normcase(os.path.abspath(entry)) != normalized_tools
    ]
    os.environ["PATH"] = os.pathsep.join([str(tools_path), *remaining])
    return executable
