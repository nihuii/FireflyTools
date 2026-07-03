"""持久化分片下载状态，为 HLS 和 DASH 提供断点续传判断。"""

import json
import os


class SegmentManifest:
    """维护已完成分片的大小信息并将其原子化写入磁盘。"""

    def __init__(self, path: str):
        """创建绑定到指定 JSON 文件的空分片状态。"""
        self.path = path
        self.data = {"segments": {}}

    def load(self) -> None:
        """从磁盘读取断点清单；文件缺失或损坏时回退为空状态。"""
        if os.path.exists(self.path):
            with open(self.path, "r", encoding="utf-8") as manifest_file:
                self.data = json.load(manifest_file)

    def save(self) -> None:
        """通过临时文件替换方式写入当前分片状态，避免半写入 JSON。

        注意：当前固定使用 `<manifest>.tmp`。Windows 上若杀毒软件或其他
        进程短暂占用该文件，`os.replace` 可能抛出 PermissionError；调用方
        会保留已下载分片，后续加固应在此处实现唯一临时名与有限重试。
        """
        directory = os.path.dirname(self.path)
        if directory:
            os.makedirs(directory, exist_ok=True)
        temp_path = self.path + ".tmp"
        with open(temp_path, "w", encoding="utf-8") as manifest_file:
            json.dump(self.data, manifest_file, ensure_ascii=False, indent=2)
        # replace 在同一文件系统内提供“旧文件或完整新文件”的可见性，
        # 避免进程中断后留下只写了一半、无法解析的正式 manifest。
        os.replace(temp_path, self.path)

    def mark_downloaded(self, filename: str, *, url: str, size: int) -> None:
        """记录指定分片已经完成以及对应文件大小。"""
        self.data.setdefault("segments", {})[filename] = {
            "url": url,
            "size": size,
        }

    def is_downloaded(
        self,
        filename: str,
        expected_size: int | None = None,
    ) -> bool:
        """同时校验清单记录、文件存在性和大小，判断分片能否复用。"""
        item = self.data.get("segments", {}).get(filename)
        if not item:
            return False
        if expected_size is not None and item.get("size") != expected_size:
            return False
        return True
