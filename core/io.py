from __future__ import annotations
from pathlib import Path
from typing import Any, Dict, List
from typing import Protocol, runtime_checkable
import yaml


@runtime_checkable
class IOAdapter(Protocol):
    async def read(self, path: str) -> Any: ...
    async def write(self, path: str, data: Any) -> None: ...
    async def list(self, path: str = "") -> List[str]: ...
    async def delete(self, path: str) -> None: ...


class LocalAdapter:
    def __init__(self, base_path: Path) -> None:
        self._base = base_path
        self._base.mkdir(parents=True, exist_ok=True)

    def _resolve(self, path: str) -> Path:
        resolved = (self._base / path).resolve()
        if not resolved.is_relative_to(self._base.resolve()):
            raise ValueError(f"Path '{path}' escapes the base directory")
        return resolved

    async def read(self, path: str) -> str:
        target = self._resolve(path)
        if not target.exists():
            raise FileNotFoundError(f"{path} not found in {self._base}")
        return target.read_text()

    async def write(self, path: str, data: str) -> None:
        target = self._resolve(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(data)

    async def list(self, path: str = "") -> List[str]:
        target = self._resolve(path) if path else self._base
        if not target.exists():
            return []
        return [str(p.relative_to(self._base)) for p in target.iterdir()]

    async def delete(self, path: str) -> None:
        self._resolve(path).unlink()


class ObsidianAdapter:
    def __init__(self, base_path: Path) -> None:
        self._local = LocalAdapter(base_path)

    async def read(self, path: str) -> Dict[str, Any]:
        content = await self._local.read(path)
        if content.startswith("---"):
            parts = content.split("---", 2)
            if len(parts) >= 3:
                frontmatter = yaml.safe_load(parts[1]) or {}
                body = parts[2].strip()
                return {"frontmatter": frontmatter, "body": body}
        return {"frontmatter": {}, "body": content}

    async def write(self, path: str, data: Dict[str, Any]) -> None:
        frontmatter = data.get("frontmatter", {})
        body = data.get("body", "")
        content = f"---\n{yaml.dump(frontmatter, default_flow_style=False)}---\n{body}"
        await self._local.write(path, content)

    async def list(self, path: str = "") -> List[str]:
        return await self._local.list(path)

    async def delete(self, path: str) -> None:
        await self._local.delete(path)
