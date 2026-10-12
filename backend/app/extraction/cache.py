"""File cache for model extractions (DL-40).

The key is derived from the file content hash, the prompt version, the schema version and
the deployment name. Changing any of them yields a different key, so a stale answer is
never reused. Each entry stores the raw model response and the validated extraction.

The cache directory holds real invoice data and must stay out of git (data/cache/).
"""

import hashlib
import json
from pathlib import Path
from typing import Any


def cache_key(file_sha256: str, prompt_version: str, schema_version: str, deployment: str) -> str:
    material = "\n".join((file_sha256, prompt_version, schema_version, deployment))
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


class ExtractionCache:
    def __init__(self, directory: Path) -> None:
        self._directory = directory

    def _path(self, key: str) -> Path:
        return self._directory / f"{key}.json"

    def get(self, key: str) -> dict[str, Any] | None:
        path = self._path(key)
        if not path.is_file():
            return None
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except ValueError:
            return None  # a corrupt entry is treated as a miss

    def put(self, key: str, entry: dict[str, Any]) -> None:
        self._directory.mkdir(parents=True, exist_ok=True)
        self._path(key).write_text(
            json.dumps(entry, indent=2, ensure_ascii=False), encoding="utf-8"
        )
