"""Shared result references must stay inside the mounted storage boundary."""

import hashlib

import pytest

from dtest.infrastructure.file_storage.integrity import (
    read_verified_bytes,
    resolve_shared_path,
)


@pytest.mark.parametrize("relative", ["", ".", "/etc/passwd", "../outside"])
def test_rejects_absolute_parent_and_empty_references(tmp_path, relative):
    with pytest.raises(ValueError, match="unsafe shared-PV"):
        resolve_shared_path(tmp_path, relative)


def test_rejects_symlink_that_escapes_mounted_root(tmp_path):
    root = tmp_path / "mounted"
    root.mkdir()
    outside = tmp_path / "private.bin"
    outside.write_bytes(b"outside")
    (root / "result.bin").symlink_to(outside)
    with pytest.raises(ValueError, match="escapes configured root"):
        resolve_shared_path(root, "result.bin")


def test_reads_binary_result_only_when_size_and_digest_match(tmp_path):
    data = b"\x00\xff\x80verified binary output"
    path = tmp_path / "result.bin"
    path.write_bytes(data)
    resolved = resolve_shared_path(tmp_path, "result.bin")
    assert (
        read_verified_bytes(
            resolved,
            expected_size=len(data),
            expected_sha256=hashlib.sha256(data).hexdigest(),
        )
        == data
    )


@pytest.mark.parametrize("changed_size", [False, True])
def test_rejects_modified_result_even_if_reference_path_is_valid(
    tmp_path, changed_size
):
    original = b"registered output"
    path = tmp_path / "result.bin"
    path.write_bytes(b"different content" + (b"!" if changed_size else b""))
    with pytest.raises(ValueError, match="size mismatch|checksum mismatch"):
        read_verified_bytes(
            path,
            expected_size=len(original),
            expected_sha256=hashlib.sha256(original).hexdigest(),
        )
