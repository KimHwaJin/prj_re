"""Resolve shared storage references and verify immutable file contents."""

import hashlib
from pathlib import Path, PurePosixPath


def resolve_shared_path(root: Path, relative_path: str) -> Path:
    relative = PurePosixPath(relative_path)
    if relative.is_absolute() or not relative.parts or ".." in relative.parts:
        raise ValueError(f"unsafe shared-PV relative path: {relative_path!r}")
    resolved_root = root.resolve()
    resolved = (resolved_root / Path(*relative.parts)).resolve()
    try:
        resolved.relative_to(resolved_root)
    except ValueError as exc:
        raise ValueError(
            f"shared-PV path escapes configured root: {relative_path!r}"
        ) from exc
    return resolved


def read_verified_bytes(
    path: Path,
    *,
    expected_size: int,
    expected_sha256: str,
) -> bytes:
    data = path.read_bytes()
    if len(data) != expected_size:
        raise ValueError(
            f"result file size mismatch for {path}: "
            f"expected {expected_size}, got {len(data)}"
        )
    actual_sha256 = hashlib.sha256(data).hexdigest()
    if actual_sha256 != expected_sha256:
        raise ValueError(f"result file checksum mismatch for {path}")
    return data
