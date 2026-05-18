from __future__ import annotations
import os
import pytest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock
from gen import belgrade_os_pb2
from worker import process_vault_op


@pytest.fixture
def vault_root(tmp_path):
    root = tmp_path / "vault"
    root.mkdir()
    return root


@pytest.fixture
def mock_redis():
    client = AsyncMock()
    client.acquire_lock.return_value = True
    return client


async def test_process_vault_op_write(vault_root, mock_redis):
    op = belgrade_os_pb2.VaultOperation()
    op.op = belgrade_os_pb2.VaultOperation.WRITE
    op.path = "notes/hello.md"
    op.content = b"# Hello"

    success = await process_vault_op(op, vault_root, mock_redis)
    
    assert success is True
    file_path = vault_root / "notes/hello.md"
    assert file_path.exists()
    assert file_path.read_text() == "# Hello"
    assert not (vault_root / "notes/hello.md.tmp").exists()
    mock_redis.acquire_lock.assert_awaited_once_with("notes/hello.md")
    mock_redis.release_lock.assert_awaited_once_with("notes/hello.md")


async def test_process_vault_op_delete(vault_root, mock_redis):
    file_path = vault_root / "notes/bye.md"
    file_path.parent.mkdir()
    file_path.write_text("bye")

    op = belgrade_os_pb2.VaultOperation()
    op.op = belgrade_os_pb2.VaultOperation.DELETE
    op.path = "notes/bye.md"

    success = await process_vault_op(op, vault_root, mock_redis)
    
    assert success is True
    assert not file_path.exists()


async def test_process_vault_op_traversal_blocked(vault_root, mock_redis):
    op = belgrade_os_pb2.VaultOperation()
    op.op = belgrade_os_pb2.VaultOperation.WRITE
    op.path = "../secret.txt"
    op.content = b"hacked"

    success = await process_vault_op(op, vault_root, mock_redis)
    
    assert success is False
    secret_file = vault_root.parent / "secret.txt"
    assert not secret_file.exists()


async def test_process_vault_op_waits_for_lock(vault_root, mock_redis):
    mock_redis.acquire_lock.return_value = False
    
    op = belgrade_os_pb2.VaultOperation()
    op.op = belgrade_os_pb2.VaultOperation.WRITE
    op.path = "locked.md"

    success = await process_vault_op(op, vault_root, mock_redis)
    
    assert success is False
    assert not (vault_root / "locked.md").exists()
