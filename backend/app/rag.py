import hashlib
import json
import os
import sqlite3
import zipfile
from datetime import UTC, datetime
from functools import lru_cache
from pathlib import Path

os.environ["ANONYMIZED_TELEMETRY"] = "False"

import chromadb
import httpx
from chromadb.api.models.Collection import Collection
from chromadb.config import Settings as ChromaSettings
from chromadb.errors import ChromaError
from docx import Document
from docx.opc.exceptions import PackageNotFoundError
from pypdf import PdfReader
from pypdf.errors import PdfReadError

from .config import get_settings
from .db import delete_source_record, get_source, list_sources, replace_folder_sources
from .schemas import IndexedSource, IngestResponse, Scope
from .tools import ToolError, resolve_allowed_path


CHUNK_SIZE_CHARS = 2000
CHUNK_OVERLAP_CHARS = 200
EMBEDDING_BATCH_SIZE = 64
SUPPORTED_EXTENSIONS = {".txt", ".md", ".pdf", ".docx"}


class RAGError(Exception):
    """Raised when local document ingestion or search cannot complete."""


@lru_cache
def _collection() -> Collection:
    settings = get_settings()
    try:
        client = chromadb.PersistentClient(
            path=str(settings.resolved_chroma_path),
            settings=ChromaSettings(anonymized_telemetry=False),
        )
        return client.get_or_create_collection(
            name="javis_knowledge",
            metadata={"hnsw:space": "cosine"},
        )
    except (ChromaError, sqlite3.Error) as exc:
        raise RAGError(f"Could not open the local Chroma index: {exc}") from exc


def chunk_text(text: str) -> list[str]:
    normalized = " ".join(text.split())
    chunks: list[str] = []
    start = 0
    while start < len(normalized):
        end = min(start + CHUNK_SIZE_CHARS, len(normalized))
        if end < len(normalized):
            boundary = normalized.rfind(" ", start, end)
            if boundary > start + CHUNK_SIZE_CHARS // 2:
                end = boundary
        chunk = normalized[start:end].strip()
        if chunk:
            chunks.append(chunk)
        if end >= len(normalized):
            break
        start = max(start + 1, end - CHUNK_OVERLAP_CHARS)
    return chunks


def _extract_text(path: Path) -> str:
    try:
        if path.suffix.lower() in {".txt", ".md"}:
            return path.read_text(encoding="utf-8")
        if path.suffix.lower() == ".pdf":
            reader = PdfReader(str(path))
            return "\n".join(page.extract_text() or "" for page in reader.pages)
        if path.suffix.lower() == ".docx":
            document = Document(path)
            text = [paragraph.text for paragraph in document.paragraphs]
            text.extend(
                cell.text
                for table in document.tables
                for row in table.rows
                for cell in row.cells
            )
            return "\n".join(text)
    except (
        OSError,
        UnicodeError,
        ValueError,
        KeyError,
        zipfile.BadZipFile,
        PackageNotFoundError,
        PdfReadError,
    ) as exc:
        raise RAGError(f"Could not read {path.name}: {exc}") from exc
    return ""


def _embed(texts: list[str]) -> list[list[float]]:
    settings = get_settings()
    vectors: list[list[float]] = []
    try:
        with httpx.Client(timeout=httpx.Timeout(120.0, connect=10.0)) as client:
            for offset in range(0, len(texts), EMBEDDING_BATCH_SIZE):
                response = client.post(
                    f"{settings.ollama_url}/api/embed",
                    json={
                        "model": settings.embedding_model,
                        "input": texts[offset : offset + EMBEDDING_BATCH_SIZE],
                    },
                )
                response.raise_for_status()
                result: object = response.json()
                if not isinstance(result, dict) or not isinstance(result.get("embeddings"), list):
                    raise RAGError("Ollama returned an invalid embedding response")
                embeddings = result["embeddings"]
                if len(embeddings) != len(texts[offset : offset + EMBEDDING_BATCH_SIZE]):
                    raise RAGError("Ollama returned an incomplete embedding batch")
                for embedding in embeddings:
                    if not isinstance(embedding, list) or not all(
                        isinstance(value, (int, float)) for value in embedding
                    ):
                        raise RAGError("Ollama returned an invalid document embedding")
                    vectors.append([float(value) for value in embedding])
    except httpx.HTTPError as exc:
        raise RAGError(
            f"Could not create embeddings with local Ollama model "
            f"{settings.embedding_model}: {exc}"
        ) from exc
    except ValueError as exc:
        raise RAGError(f"Ollama returned invalid embedding JSON: {exc}") from exc
    return vectors


def _source_id(path: str, scope: Scope) -> str:
    return hashlib.sha256(f"{scope}:{path}".encode("utf-8")).hexdigest()


def ingest_folder(folder_path: str, scope: Scope) -> IngestResponse:
    try:
        root = resolve_allowed_path(folder_path)
    except ToolError as exc:
        raise RAGError(str(exc)) from exc
    if not root.is_dir():
        raise RAGError("The selected path is not a directory inside ALLOWED_DIRS.")

    documents: list[tuple[Path, list[str]]] = []
    try:
        for path in sorted(root.rglob("*")):
            if path.suffix.lower() not in SUPPORTED_EXTENSIONS or not path.is_file():
                continue
            resolved_path = path.resolve()
            if resolved_path != root and root not in resolved_path.parents:
                continue
            documents.append((resolved_path, chunk_text(_extract_text(resolved_path))))
    except OSError as exc:
        raise RAGError(f"Could not scan the selected folder: {exc}") from exc

    folder = str(root)
    indexed_at = datetime.now(UTC)
    indexed_sources = [
        IndexedSource(
            id=_source_id(str(path), scope),
            path=str(path),
            folder_path=folder,
            source=path.name,
            scope=scope,
            indexed_at=indexed_at,
            chunk_count=len(chunks),
        )
        for path, chunks in documents
    ]

    chunk_texts: list[str] = []
    chunk_metadata: list[dict[str, str | int]] = []
    chunk_ids: list[str] = []
    for source, (_, chunks) in zip(indexed_sources, documents, strict=True):
        for chunk_index, chunk in enumerate(chunks):
            chunk_texts.append(chunk)
            chunk_ids.append(f"{source.id}:{chunk_index}")
            chunk_metadata.append(
                {
                    "source_id": source.id,
                    "source": source.source,
                    "scope": source.scope,
                    "path": source.path,
                    "folder_path": source.folder_path,
                    "chunk_index": chunk_index,
                }
            )

    embeddings = _embed(chunk_texts) if chunk_texts else []
    collection = _collection()
    try:
        for offset in range(0, len(chunk_texts), EMBEDDING_BATCH_SIZE):
            collection.upsert(
                ids=chunk_ids[offset : offset + EMBEDDING_BATCH_SIZE],
                documents=chunk_texts[offset : offset + EMBEDDING_BATCH_SIZE],
                embeddings=embeddings[offset : offset + EMBEDDING_BATCH_SIZE],
                metadatas=chunk_metadata[offset : offset + EMBEDDING_BATCH_SIZE],
            )

        old_sources = list_sources(folder_path=folder, scope=scope)
        new_ids = {source.id for source in indexed_sources}
        stale_ids = [source.id for source in old_sources if source.id not in new_ids]
        if stale_ids:
            collection.delete(where={"source_id": {"$in": stale_ids}})
        stale_database_ids = replace_folder_sources(folder, scope, indexed_sources)
        if set(stale_database_ids) != set(stale_ids):
            raise RAGError("The source index changed during ingestion; re-index the folder.")
    except (ChromaError, sqlite3.Error) as exc:
        raise RAGError(f"Could not update the local Chroma index: {exc}") from exc

    return IngestResponse(
        indexed_files=len(indexed_sources),
        indexed_chunks=len(chunk_texts),
        sources=indexed_sources,
    )


def list_indexed_sources() -> list[IndexedSource]:
    return list_sources()


def remove_indexed_source(source_id: str) -> IndexedSource | None:
    source = get_source(source_id)
    if source is None:
        return None
    try:
        _collection().delete(where={"source_id": source_id})
        return delete_source_record(source_id)
    except (ChromaError, sqlite3.Error) as exc:
        raise RAGError(f"Could not remove the indexed source: {exc}") from exc


def search_knowledge(query: str, scope: Scope = "private", limit: int = 5) -> str:
    if not query.strip():
        raise RAGError("The knowledge search query cannot be empty.")
    collection = _collection()
    try:
        if collection.count() == 0:
            return "No indexed knowledge matched this query."
        results = collection.query(
            query_embeddings=_embed([query]),
            n_results=limit,
            where={"scope": scope},
            include=["documents", "metadatas", "distances"],
        )
    except (ChromaError, sqlite3.Error) as exc:
        raise RAGError(f"Could not search the local Chroma index: {exc}") from exc
    documents = results.get("documents")
    metadatas = results.get("metadatas")
    if not documents or not documents[0] or not metadatas or not metadatas[0]:
        return "No indexed knowledge matched this query."

    matches: list[str] = []
    source_names: list[str] = []
    for document, metadata in zip(documents[0], metadatas[0], strict=True):
        if not isinstance(document, str) or not isinstance(metadata, dict):
            continue
        source = metadata.get("source", "unknown")
        path = metadata.get("path", "")
        if isinstance(source, str) and source not in source_names:
            source_names.append(source)
        matches.append(f"[{source}] {path}\n{document}")
    if not matches:
        return "No indexed knowledge matched this query."
    citation_header = f"Citation filenames: {json.dumps(source_names, ensure_ascii=False)}"
    return f"{citation_header}\n\n" + "\n\n".join(matches)
