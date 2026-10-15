"""Content-addressed evidence store. Files are named by their SHA-256; every read re-hashes
the bytes and refuses to return content whose hash does not match the database."""

from __future__ import annotations

import hashlib
import os
import tempfile
from pathlib import Path


class EvidenceIntegrityError(Exception):
    pass


class EvidenceStore:
    def __init__(self, root: str | Path) -> None:
        self.root = Path(root)

    def _path(self, sha256: str) -> Path:
        if len(sha256) != 64 or any(c not in "0123456789abcdef" for c in sha256):
            raise ValueError("invalid sha256")
        return self.root / sha256[:2] / sha256

    def put(self, data: bytes) -> tuple[str, int]:
        digest = hashlib.sha256(data).hexdigest()
        path = self._path(digest)
        if not path.exists():
            path.parent.mkdir(parents=True, exist_ok=True)
            fd, tmp = tempfile.mkstemp(dir=path.parent)
            with os.fdopen(fd, "wb") as f:
                f.write(data)
            os.replace(tmp, path)
        return digest, len(data)

    def get(self, sha256: str) -> bytes:
        path = self._path(sha256)
        if not path.exists():
            raise EvidenceIntegrityError(f"artifact {sha256} missing from store")
        data = path.read_bytes()
        if hashlib.sha256(data).hexdigest() != sha256:
            raise EvidenceIntegrityError(f"artifact {sha256} failed hash verification")
        return data
