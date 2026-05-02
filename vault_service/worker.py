from __future__ import annotations
import os
import logging
from pathlib import Path
from gen import belgrade_os_pb2

log = logging.getLogger(__name__)

async def process_vault_op(op: belgrade_os_pb2.VaultOperation, vault_root: Path, redis_client) -> bool:
    """Execute a single vault operation with locking and atomic write."""
    if not op.path:
        return False

    # Path traversal protection
    try:
        target_path = (vault_root / op.path).resolve()
        if not str(target_path).startswith(str(vault_root.resolve())):
            log.error("path traversal attempt: %s", op.path)
            return False
    except Exception:
        return False

    # Acquire lock
    if not await redis_client.acquire_lock(op.path):
        log.warning("failed to acquire lock for %s", op.path)
        return False

    try:
        if op.op == belgrade_os_pb2.VaultOperation.WRITE:
            return _write_file(target_path, op.content)
        elif op.op == belgrade_os_pb2.VaultOperation.DELETE:
            return _delete_file(target_path)
    finally:
        await redis_client.release_lock(op.path)
    return False

def _write_file(path: Path, content: bytes) -> bool:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = path.with_suffix(path.suffix + ".tmp")
        with open(tmp_path, "wb") as f:
            f.write(content)
        os.rename(tmp_path, path)
        log.info("wrote vault file: %s", path)
        return True
    except Exception as e:
        log.error("failed to write vault file %s: %s", path, e)
        return False

def _delete_file(path: Path) -> bool:
    try:
        if path.exists():
            os.remove(path)
            log.info("deleted vault file: %s", path)
        return True
    except Exception as e:
        log.error("failed to delete vault file %s: %s", path, e)
        return False
