import json
import os


class SegmentManifest:
    def __init__(self, path: str):
        self.path = path
        self.data = {"segments": {}}

    def load(self) -> None:
        if os.path.exists(self.path):
            with open(self.path, "r", encoding="utf-8") as manifest_file:
                self.data = json.load(manifest_file)

    def save(self) -> None:
        directory = os.path.dirname(self.path)
        if directory:
            os.makedirs(directory, exist_ok=True)
        temp_path = self.path + ".tmp"
        with open(temp_path, "w", encoding="utf-8") as manifest_file:
            json.dump(self.data, manifest_file, ensure_ascii=False, indent=2)
        os.replace(temp_path, self.path)

    def mark_downloaded(self, filename: str, *, url: str, size: int) -> None:
        self.data.setdefault("segments", {})[filename] = {
            "url": url,
            "size": size,
        }

    def is_downloaded(
        self,
        filename: str,
        expected_size: int | None = None,
    ) -> bool:
        item = self.data.get("segments", {}).get(filename)
        if not item:
            return False
        if expected_size is not None and item.get("size") != expected_size:
            return False
        return True
