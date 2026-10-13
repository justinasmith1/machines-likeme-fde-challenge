"""Storage for uploaded documents.

Services depend on the FileStore interface only, so the local folder used in development
can be replaced by Azure Blob Storage in deployment without touching them.

Files are named by content hash: <sha256>.<ext>. Uploading the same file twice stores it
once. The folder holds real invoices and must stay out of git (data/uploads/).
"""

from pathlib import Path
from typing import Protocol


class FileStore(Protocol):
    def save(self, sha256: str, extension: str, data: bytes) -> str:
        """Store the bytes and return the path or key to keep on the invoice."""
        ...

    def load(self, stored_path: str) -> bytes: ...


class LocalFileStore:
    def __init__(self, directory: Path) -> None:
        self._directory = directory

    def save(self, sha256: str, extension: str, data: bytes) -> str:
        self._directory.mkdir(parents=True, exist_ok=True)
        name = f"{sha256}{extension.lower()}"
        path = self._directory / name
        if not path.is_file():
            path.write_bytes(data)
        return name  # relative to the store, so the folder can move

    def load(self, stored_path: str) -> bytes:
        return (self._directory / stored_path).read_bytes()
