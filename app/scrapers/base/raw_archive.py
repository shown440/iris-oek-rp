import hashlib
import json
from datetime import UTC
from datetime import datetime
from pathlib import Path


class RawArchive:
    """
    Stores every raw service response of a run with its SHA-256, so each
    loaded record can be traced back to the exact bytes it came from.

        data/runs/<run_id>/raw/capabilities.xml
        data/runs/<run_id>/raw/page_0001.json
        data/runs/<run_id>/raw/manifest.json
    """

    def __init__(self, root: Path):
        self.root = root
        self.root.mkdir(parents=True, exist_ok=True)
        self.entries: list[dict[str, object]] = []

    def save(self, name: str, url: str, content: bytes, content_type: str) -> Path:
        path = self.root / name
        path.write_bytes(content)

        self.entries.append(
            {
                "file": name,
                "url": url,
                "content_type": content_type,
                "bytes": len(content),
                "sha256": hashlib.sha256(content).hexdigest(),
                "fetched_at": datetime.now(UTC).isoformat(),
            }
        )

        return path

    def write_manifest(self) -> Path:
        path = self.root / "manifest.json"
        path.write_text(json.dumps(self.entries, indent=2), encoding="utf-8")
        return path
