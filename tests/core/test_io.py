import pytest
from pathlib import Path
from core.io import LocalAdapter


async def test_local_write_and_read(tmp_dir: Path) -> None:
    adapter = LocalAdapter(tmp_dir)
    await adapter.write("hello.txt", "world")
    result = await adapter.read("hello.txt")
    assert result == "world"


async def test_local_write_creates_subdirs(tmp_dir: Path) -> None:
    adapter = LocalAdapter(tmp_dir)
    await adapter.write("subdir/file.txt", "content")
    assert (tmp_dir / "subdir" / "file.txt").exists()


async def test_local_list(tmp_dir: Path) -> None:
    adapter = LocalAdapter(tmp_dir)
    await adapter.write("a.txt", "a")
    await adapter.write("b.txt", "b")
    files = await adapter.list()
    assert "a.txt" in files
    assert "b.txt" in files


async def test_local_delete(tmp_dir: Path) -> None:
    adapter = LocalAdapter(tmp_dir)
    await adapter.write("del.txt", "x")
    await adapter.delete("del.txt")
    assert not (tmp_dir / "del.txt").exists()


async def test_local_read_missing_file_raises(tmp_dir: Path) -> None:
    adapter = LocalAdapter(tmp_dir)
    with pytest.raises(FileNotFoundError):
        await adapter.read("missing.txt")


async def test_base_path_is_injected(tmp_dir: Path) -> None:
    adapter = LocalAdapter(tmp_dir)
    await adapter.write("log.md", "data")
    assert (tmp_dir / "log.md").read_text() == "data"
