"""E13 Workflow JSON 파일 저장소. DB에는 root 기준 상대 경로만 노출합니다."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from uuid import UUID, uuid4

from config import settings


class WorkflowFileStore:
    @staticmethod
    def _root() -> Path:
        root = settings.workflow_storage_root.expanduser()
        return (Path.cwd() / root).resolve() if not root.is_absolute() else root.resolve()

    @staticmethod
    def _resolve(relative_path: str) -> Path:
        root = WorkflowFileStore._root()
        resolved = (root / relative_path).resolve()
        if resolved.parent != root:
            raise ValueError("workflow file path escapes workflow_storage_root")
        return resolved

    @staticmethod
    def write(workflow_id: UUID, document: dict) -> tuple[str, str]:
        if document.get('workflow_version') == '2.0':
            path, checksum, _ = WorkflowFileStore.write_revision(workflow_id, document)
            return path, checksum
        body = json.dumps(document, ensure_ascii=False, sort_keys=True, indent=2).encode("utf-8")
        checksum = hashlib.sha256(body).hexdigest()
        relative_path = f"{workflow_id}.json"
        target = WorkflowFileStore._resolve(relative_path)
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary = target.with_suffix(".json.tmp")
        temporary.write_bytes(body)
        os.replace(temporary, target)
        return relative_path, checksum

    @staticmethod
    def write_revision(workflow_id: UUID, document: dict) -> tuple[str, str, bool]:
        """Content-addressed immutable revision; report whether this call created it.

        Historical SHA references resolve without scanning unrelated files.
        Reusing an old revision must not delete that file on a DB rollback.
        """
        body = json.dumps(document, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False).encode('utf-8')
        checksum = hashlib.sha256(body).hexdigest()
        path = f'{workflow_id}.{checksum}.json'
        target = WorkflowFileStore._resolve(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists():
            if target.read_bytes() != body:
                raise ValueError('Stored Workflow revision content does not match its SHA')
            return path, checksum, False
        temporary = target.with_suffix('.' + uuid4().hex + '.tmp')
        try:
            temporary.write_bytes(body)
            os.replace(temporary, target)
        finally:
            temporary.unlink(missing_ok=True)
        return path, checksum, True

    @staticmethod
    def read(relative_path: str) -> dict:
        return json.loads(WorkflowFileStore._resolve(relative_path).read_text(encoding="utf-8"))

    @staticmethod
    def remove_if_exists(relative_path: str) -> None:
        WorkflowFileStore._resolve(relative_path).unlink(missing_ok=True)
