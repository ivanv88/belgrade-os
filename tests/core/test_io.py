import pytest
from pathlib import Path
from core.io import LocalAdapter
from core.io import ObsidianAdapter


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


async def test_path_traversal_raises(tmp_dir: Path) -> None:
    adapter = LocalAdapter(tmp_dir)
    with pytest.raises(ValueError, match="escapes the base directory"):
        await adapter.read("../../etc/passwd")


async def test_obsidian_write_creates_frontmatter(tmp_dir: Path) -> None:
    adapter = ObsidianAdapter(tmp_dir)
    await adapter.write("log.md", {
        "frontmatter": {"weight_kg": 104, "date": "2026-04-23"},
        "body": "Feeling good today.",
    })
    content = (tmp_dir / "log.md").read_text()
    assert "weight_kg: 104" in content
    assert "Feeling good today." in content
    assert content.startswith("---")


async def test_obsidian_read_parses_frontmatter(tmp_dir: Path) -> None:
    adapter = ObsidianAdapter(tmp_dir)
    (tmp_dir / "note.md").write_text(
        "---\nweight_kg: 104\ndate: '2026-04-23'\n---\nBody text here."
    )
    result = await adapter.read("note.md")
    assert result["frontmatter"]["weight_kg"] == 104
    assert result["body"] == "Body text here."


async def test_obsidian_read_file_without_frontmatter(tmp_dir: Path) -> None:
    adapter = ObsidianAdapter(tmp_dir)
    (tmp_dir / "plain.md").write_text("Just plain text.")
    result = await adapter.read("plain.md")
    assert result["frontmatter"] == {}
    assert result["body"] == "Just plain text."


async def test_obsidian_roundtrip(tmp_dir: Path) -> None:
    adapter = ObsidianAdapter(tmp_dir)
    original = {"frontmatter": {"calories": 2000}, "body": "Good deficit day."}
    await adapter.write("entry.md", original)
    result = await adapter.read("entry.md")
    assert result["frontmatter"]["calories"] == 2000
    assert "Good deficit day." in result["body"]
