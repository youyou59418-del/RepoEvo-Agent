"""Week 8 context budget, Artifact storage, and lightweight code retrieval."""

from __future__ import annotations

import ast
import hashlib
import json
import re
import time
from collections.abc import Iterable
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field

from .tool_layer import list_files, read_file


class ArtifactRef(BaseModel):
    artifact_id: str
    path: str
    sha256: str
    byte_size: int
    media_type: str = "text/plain"
    created_at: float


class ArtifactStore:
    """Store complete outputs outside Agent state and return stable references."""

    def __init__(self, root: Path) -> None:
        self.root = root.resolve()
        self.root.mkdir(parents=True, exist_ok=True)

    def put_bytes(self, name: str, data: bytes, *, media_type: str = "application/octet-stream") -> ArtifactRef:
        if not name or "/" in name or "\\" in name or name in {".", ".."}:
            raise ValueError("ARTIFACT_NAME_INVALID")
        digest = hashlib.sha256(data).hexdigest()
        artifact_id = f"artifact-{digest[:16]}"
        target = self.root / f"{artifact_id}-{name}"
        target.write_bytes(data)
        return ArtifactRef(
            artifact_id=artifact_id,
            path=str(target),
            sha256=digest,
            byte_size=len(data),
            media_type=media_type,
            created_at=time.time(),
        )

    def put_text(self, name: str, text: str) -> ArtifactRef:
        return self.put_bytes(name, text.encode("utf-8"), media_type="text/plain; charset=utf-8")

    def read(self, reference: ArtifactRef) -> bytes:
        target = Path(reference.path).resolve()
        try:
            target.relative_to(self.root)
        except ValueError as exc:
            raise ValueError("ARTIFACT_PATH_ESCAPE") from exc
        data = target.read_bytes()
        if hashlib.sha256(data).hexdigest() != reference.sha256:
            raise ValueError("ARTIFACT_HASH_MISMATCH")
        return data


ContextKind = Literal["system", "task", "evidence", "tool_output", "artifact"]


class ContextItem(BaseModel):
    kind: ContextKind
    content: str
    source: str = "unknown"
    priority: int = Field(default=1, ge=0, le=3)
    facts: list[str] = Field(default_factory=list)


class ContextResult(BaseModel):
    text: str
    items: list[ContextItem]
    artifact_refs: list[ArtifactRef] = Field(default_factory=list)
    compressed: bool
    dropped_items: int = 0
    char_count: int
    estimated_tokens: int


class ContextAssembler:
    """Keep context under a character budget while preserving high-priority facts."""

    def __init__(
        self,
        artifact_store: ArtifactStore,
        *,
        max_chars: int = 12000,
        trigger_ratio: float = 0.70,
    ) -> None:
        if max_chars <= 0 or not 0.5 <= trigger_ratio < 1:
            raise ValueError("CONTEXT_BUDGET_INVALID")
        self.artifact_store = artifact_store
        self.max_chars = max_chars
        self.trigger_ratio = trigger_ratio

    @staticmethod
    def _render(items: Iterable[ContextItem]) -> str:
        return "\n\n".join(f"[{item.kind}:{item.source}]\n{item.content}" for item in items)

    def assemble(self, items: list[ContextItem]) -> ContextResult:
        raw = self._render(items)
        if len(raw) <= int(self.max_chars * self.trigger_ratio):
            return ContextResult(
                text=raw,
                items=items,
                compressed=False,
                char_count=len(raw),
                estimated_tokens=max(1, len(raw) // 4),
            )

        ordered = sorted(enumerate(items), key=lambda pair: (-pair[1].priority, pair[0]))
        kept: list[ContextItem] = []
        refs: list[ArtifactRef] = []
        current = 0
        dropped = 0
        for _, item in ordered:
            content = item.content
            if item.kind == "tool_output" and len(content) > max(1024, self.max_chars // 5):
                reference = self.artifact_store.put_text(f"{item.source.replace('/', '_')}.log", content)
                refs.append(reference)
                preview = content[:350]
                if len(content) > 700:
                    preview += "\n... [middle stored in Artifact] ...\n" + content[-350:]
                content = (
                    f"Artifact {reference.artifact_id} ({reference.byte_size} bytes); "
                    f"full output: {reference.path}\n{preview}"
                )
                item = item.model_copy(update={"kind": "artifact", "content": content})
            rendered = self._render([item])
            if current + len(rendered) <= self.max_chars:
                kept.append(item)
                current += len(rendered) + 2
            elif item.priority >= 3:
                shortened = item.model_copy(
                    update={"content": item.content[: max(200, self.max_chars - current - 80)]}
                )
                kept.append(shortened)
                current += len(self._render([shortened])) + 2
            else:
                dropped += 1
        kept.sort(key=lambda item: items.index(next(original for original in items if original.source == item.source)))
        text = self._render(kept)
        return ContextResult(
            text=text,
            items=kept,
            artifact_refs=refs,
            compressed=True,
            dropped_items=dropped,
            char_count=len(text),
            estimated_tokens=max(1, len(text) // 4),
        )


class CodeHit(BaseModel):
    path: str
    line_start: int
    line_end: int
    snippet: str
    score: float
    symbols: list[str] = Field(default_factory=list)


class CodeRetriever:
    """Deterministic text plus Python-AST retrieval for the current repository."""

    def retrieve(self, repo_root: Path, query: str, *, limit: int = 8) -> list[CodeHit]:
        if not query.strip() or limit <= 0 or limit > 50:
            raise ValueError("RETRIEVAL_ARGUMENT_INVALID")
        tokens = [token.lower() for token in re.findall(r"[A-Za-z_][A-Za-z0-9_]+", query)]
        hits: list[CodeHit] = []
        files = list_files(repo_root, max_files=128)
        for path in files:
            if not path.endswith(".py"):
                continue
            content = read_file(repo_root, path)
            lines = content.splitlines()
            lower = content.lower()
            token_score = sum(lower.count(token) for token in tokens)
            symbols: list[str] = []
            try:
                tree = ast.parse(content, filename=path)
                for node in ast.walk(tree):
                    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) and any(
                        token in node.name.lower() for token in tokens
                    ):
                        symbols.append(node.name)
            except SyntaxError:
                pass
            if token_score == 0 and not symbols:
                continue
            score = float(token_score + len(symbols) * 3)
            matching_lines = [index for index, line in enumerate(lines) if any(token in line.lower() for token in tokens)]
            line_start = (matching_lines[0] if matching_lines else 0) + 1
            line_end = min(len(lines), line_start + 14)
            hits.append(
                CodeHit(
                    path=path,
                    line_start=line_start,
                    line_end=line_end,
                    snippet="\n".join(lines[line_start - 1 : line_end]),
                    score=score,
                    symbols=sorted(set(symbols)),
                )
            )
        hits.sort(key=lambda hit: (-hit.score, hit.path, hit.line_start))
        return hits[:limit]


def context_manifest(result: ContextResult) -> dict[str, Any]:
    return json.loads(result.model_dump_json())
