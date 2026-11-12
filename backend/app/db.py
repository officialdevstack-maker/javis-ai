from collections.abc import Iterator
from contextlib import contextmanager
import json
import sqlite3
from datetime import UTC, datetime
from uuid import UUID, uuid4

from .config import get_settings
from .schemas import (
    AuditEntry,
    ConversationDetail,
    ConversationSummary,
    IndexedSource,
    MemoryItem,
    Message,
    Scope,
    StoredMessage,
)


NEW_CONVERSATION_TITLE = "New chat"

@contextmanager
def _connect() -> Iterator[sqlite3.Connection]:
    database_path = get_settings().resolved_database_path
    database_path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(database_path, timeout=10)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    connection.execute("PRAGMA busy_timeout = 10000")
    try:
        yield connection
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def _now() -> str:
    return datetime.now(UTC).isoformat()


def initialize_database() -> None:
    with _connect() as connection:
        connection.execute("PRAGMA journal_mode = WAL")
        connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS conversations (
                id TEXT PRIMARY KEY,
                title TEXT NOT NULL,
                scope TEXT NOT NULL DEFAULT 'private' CHECK (scope IN ('private', 'public', 'business')),
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                conversation_id TEXT NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
                role TEXT NOT NULL CHECK (role IN ('user', 'assistant')),
                content TEXT NOT NULL,
                created_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_messages_conversation
                ON messages(conversation_id, id);
            CREATE TABLE IF NOT EXISTS settings (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS sources (
                id TEXT PRIMARY KEY,
                path TEXT NOT NULL,
                folder_path TEXT NOT NULL,
                source TEXT NOT NULL,
                scope TEXT NOT NULL CHECK (scope IN ('private', 'public', 'business')),
                indexed_at TEXT NOT NULL,
                chunk_count INTEGER NOT NULL CHECK (chunk_count >= 0),
                UNIQUE (path, scope)
            );
            CREATE INDEX IF NOT EXISTS idx_sources_folder_scope
                ON sources(folder_path, scope);
            CREATE TABLE IF NOT EXISTS memories (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                text TEXT NOT NULL,
                scope TEXT NOT NULL CHECK (scope IN ('private', 'public', 'business')),
                created_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_memories_scope
                ON memories(scope, id);
            CREATE TABLE IF NOT EXISTS tool_audit (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                tool_name TEXT NOT NULL,
                scope TEXT NOT NULL CHECK (scope IN ('private', 'public', 'business')),
                tier INTEGER NOT NULL CHECK (tier BETWEEN 0 AND 3),
                arguments_json TEXT NOT NULL,
                result TEXT,
                status TEXT NOT NULL CHECK (status IN ('running', 'completed', 'denied', 'blocked', 'error')),
                approval_by TEXT,
                created_at TEXT NOT NULL,
                completed_at TEXT
            );
            CREATE INDEX IF NOT EXISTS idx_tool_audit_created
                ON tool_audit(created_at DESC, id DESC);
            CREATE INDEX IF NOT EXISTS idx_tool_audit_scope_tool
                ON tool_audit(scope, tool_name, id DESC);
            """
        )
        columns = {
            row["name"]
            for row in connection.execute("PRAGMA table_info(conversations)")
        }
        if "scope" not in columns:
            connection.execute(
                "ALTER TABLE conversations ADD COLUMN scope TEXT NOT NULL DEFAULT 'private'"
            )


def _summary(row: sqlite3.Row) -> ConversationSummary:
    return ConversationSummary(
        id=UUID(row["id"]),
        title=row["title"],
        scope=row["scope"],
        created_at=datetime.fromisoformat(row["created_at"]),
        updated_at=datetime.fromisoformat(row["updated_at"]),
    )


def create_conversation(scope: Scope = "private") -> ConversationSummary:
    conversation_id = uuid4()
    now = _now()
    with _connect() as connection:
        connection.execute(
            "INSERT INTO conversations (id, title, scope, created_at, updated_at) VALUES (?, ?, ?, ?, ?)",
            (str(conversation_id), NEW_CONVERSATION_TITLE, scope, now, now),
        )
        row = connection.execute(
            "SELECT * FROM conversations WHERE id = ?", (str(conversation_id),)
        ).fetchone()
    if row is None:
        raise RuntimeError("The new conversation was not persisted")
    return _summary(row)


def list_conversations(scope: Scope | None = None) -> list[ConversationSummary]:
    with _connect() as connection:
        if scope is None:
            rows = connection.execute(
                "SELECT * FROM conversations ORDER BY updated_at DESC, id DESC"
            ).fetchall()
        else:
            rows = connection.execute(
                "SELECT * FROM conversations WHERE scope = ? ORDER BY updated_at DESC, id DESC",
                (scope,),
            ).fetchall()
    return [_summary(row) for row in rows]


def get_conversation(conversation_id: UUID) -> ConversationDetail | None:
    with _connect() as connection:
        row = connection.execute(
            "SELECT * FROM conversations WHERE id = ?", (str(conversation_id),)
        ).fetchone()
        if row is None:
            return None
        message_rows = connection.execute(
            "SELECT id, role, content, created_at FROM messages "
            "WHERE conversation_id = ? ORDER BY id",
            (str(conversation_id),),
        ).fetchall()
    return ConversationDetail(
        **_summary(row).model_dump(),
        messages=[
            StoredMessage(
                id=message["id"],
                role=message["role"],
                content=message["content"],
                created_at=datetime.fromisoformat(message["created_at"]),
            )
            for message in message_rows
        ],
    )


def delete_conversation(conversation_id: UUID) -> bool:
    with _connect() as connection:
        cursor = connection.execute(
            "DELETE FROM conversations WHERE id = ?", (str(conversation_id),)
        )
    return cursor.rowcount > 0


def begin_chat_turn(conversation_id: UUID, message: Message) -> tuple[list[Message], Scope] | None:
    now = _now()
    with _connect() as connection:
        row = connection.execute(
            "SELECT title, scope FROM conversations WHERE id = ?", (str(conversation_id),)
        ).fetchone()
        if row is None:
            return None
        scope = row["scope"]
        if row["title"] == NEW_CONVERSATION_TITLE:
            title = message.content.strip().splitlines()[0][:80] or NEW_CONVERSATION_TITLE
            connection.execute(
                "UPDATE conversations SET title = ? WHERE id = ?",
                (title, str(conversation_id)),
            )
        connection.execute(
            "INSERT INTO messages (conversation_id, role, content, created_at) "
            "VALUES (?, 'user', ?, ?)",
            (str(conversation_id), message.content, now),
        )
        connection.execute(
            "UPDATE conversations SET updated_at = ? WHERE id = ?",
            (now, str(conversation_id)),
        )
        rows = connection.execute(
            "SELECT role, content FROM messages WHERE conversation_id = ? ORDER BY id",
            (str(conversation_id),),
        ).fetchall()
    return [Message(role=row["role"], content=row["content"]) for row in rows], scope


def complete_chat_turn(conversation_id: UUID, content: str) -> None:
    now = _now()
    with _connect() as connection:
        connection.execute(
            "INSERT INTO messages (conversation_id, role, content, created_at) "
            "VALUES (?, 'assistant', ?, ?)",
            (str(conversation_id), content, now),
        )
        connection.execute(
            "UPDATE conversations SET updated_at = ? WHERE id = ?",
            (now, str(conversation_id)),
        )


def discard_unanswered_turn(conversation_id: UUID) -> None:
    """Delete the trailing user message if no assistant reply followed it."""
    with _connect() as connection:
        row = connection.execute(
            "SELECT id, role FROM messages WHERE conversation_id = ? "
            "ORDER BY id DESC LIMIT 1",
            (str(conversation_id),),
        ).fetchone()
        if row is not None and row["role"] == "user":
            connection.execute("DELETE FROM messages WHERE id = ?", (row["id"],))


def _memory(row: sqlite3.Row) -> MemoryItem:
    return MemoryItem(
        id=row["id"],
        text=row["text"],
        scope=row["scope"],
        created_at=datetime.fromisoformat(row["created_at"]),
    )


def add_memory(text: str, scope: Scope) -> MemoryItem:
    with _connect() as connection:
        cursor = connection.execute(
            "INSERT INTO memories (text, scope, created_at) VALUES (?, ?, ?)",
            (text, scope, _now()),
        )
        row = connection.execute(
            "SELECT * FROM memories WHERE id = ?",
            (cursor.lastrowid,),
        ).fetchone()
    if row is None:
        raise RuntimeError("The memory was not persisted")
    return _memory(row)


def list_memories(scope: Scope | None = None) -> list[MemoryItem]:
    with _connect() as connection:
        if scope is None:
            rows = connection.execute(
                "SELECT * FROM memories ORDER BY id DESC"
            ).fetchall()
        else:
            rows = connection.execute(
                "SELECT * FROM memories WHERE scope = ? ORDER BY id DESC",
                (scope,),
            ).fetchall()
    return [_memory(row) for row in rows]


def delete_memory(memory_id: int) -> bool:
    with _connect() as connection:
        cursor = connection.execute("DELETE FROM memories WHERE id = ?", (memory_id,))
    return cursor.rowcount > 0


def create_tool_audit(
    tool_name: str,
    scope: Scope,
    tier: int,
    arguments: dict[str, object],
    *,
    status: str = "running",
    result: str | None = None,
    approval_by: str | None = None,
) -> int:
    now = _now()
    with _connect() as connection:
        cursor = connection.execute(
            "INSERT INTO tool_audit "
            "(tool_name, scope, tier, arguments_json, result, status, approval_by, created_at, completed_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                tool_name,
                scope,
                tier,
                json.dumps(arguments, ensure_ascii=False, default=str),
                result,
                status,
                approval_by,
                now,
                now if status != "running" else None,
            ),
        )
    if cursor.lastrowid is None:
        raise RuntimeError("The tool audit entry was not persisted")
    return cursor.lastrowid


def complete_tool_audit(
    audit_id: int,
    result: str,
    *,
    status: str,
    approval_by: str | None,
) -> None:
    with _connect() as connection:
        cursor = connection.execute(
            "UPDATE tool_audit SET result = ?, status = ?, approval_by = ?, completed_at = ? "
            "WHERE id = ? AND status = 'running'",
            (result, status, approval_by, _now(), audit_id),
        )
        if cursor.rowcount != 1:
            raise RuntimeError(f"Could not complete tool audit entry {audit_id}")


def _audit_entry(row: sqlite3.Row) -> AuditEntry:
    arguments: object = json.loads(row["arguments_json"])
    if not isinstance(arguments, dict):
        raise ValueError(f"Tool audit entry {row['id']} has invalid arguments")
    return AuditEntry(
        id=row["id"],
        tool_name=row["tool_name"],
        scope=row["scope"],
        tier=row["tier"],
        arguments=arguments,
        result=row["result"],
        status=row["status"],
        approval_by=row["approval_by"],
        created_at=datetime.fromisoformat(row["created_at"]),
        completed_at=(
            datetime.fromisoformat(row["completed_at"])
            if row["completed_at"] is not None
            else None
        ),
    )


def list_tool_audit(
    *,
    scope: Scope | None = None,
    tool_name: str | None = None,
    limit: int = 100,
) -> list[AuditEntry]:
    clauses: list[str] = []
    values: list[str | int] = []
    if scope is not None:
        clauses.append("scope = ?")
        values.append(scope)
    if tool_name:
        clauses.append("tool_name = ?")
        values.append(tool_name)
    where_clause = f" WHERE {' AND '.join(clauses)}" if clauses else ""
    values.append(limit)
    with _connect() as connection:
        rows = connection.execute(
            f"SELECT * FROM tool_audit{where_clause} ORDER BY id DESC LIMIT ?",
            values,
        ).fetchall()
    return [_audit_entry(row) for row in rows]


def get_setting(key: str) -> str | None:
    with _connect() as connection:
        row = connection.execute(
            "SELECT value FROM settings WHERE key = ?", (key,)
        ).fetchone()
    return row["value"] if row is not None else None


def set_setting(key: str, value: str) -> None:
    with _connect() as connection:
        connection.execute(
            "INSERT INTO settings (key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, value),
        )


def _source(row: sqlite3.Row) -> IndexedSource:
    return IndexedSource(
        id=row["id"],
        path=row["path"],
        folder_path=row["folder_path"],
        source=row["source"],
        scope=row["scope"],
        indexed_at=datetime.fromisoformat(row["indexed_at"]),
        chunk_count=row["chunk_count"],
    )


def list_sources(
    *,
    folder_path: str | None = None,
    scope: Scope | None = None,
) -> list[IndexedSource]:
    clauses: list[str] = []
    values: list[str] = []
    if folder_path is not None:
        clauses.append("folder_path = ?")
        values.append(folder_path)
    if scope is not None:
        clauses.append("scope = ?")
        values.append(scope)
    where_clause = f" WHERE {' AND '.join(clauses)}" if clauses else ""
    with _connect() as connection:
        rows = connection.execute(
            f"SELECT * FROM sources{where_clause} ORDER BY indexed_at DESC, source",
            values,
        ).fetchall()
    return [_source(row) for row in rows]


def get_source(source_id: str) -> IndexedSource | None:
    with _connect() as connection:
        row = connection.execute("SELECT * FROM sources WHERE id = ?", (source_id,)).fetchone()
    return _source(row) if row is not None else None


def replace_folder_sources(
    folder_path: str,
    scope: Scope,
    sources: list[IndexedSource],
) -> list[str]:
    now = _now()
    with _connect() as connection:
        old_ids = [
            row["id"]
            for row in connection.execute(
                "SELECT id FROM sources WHERE folder_path = ? AND scope = ?",
                (folder_path, scope),
            ).fetchall()
        ]
        new_ids = {source.id for source in sources}
        connection.execute(
            "DELETE FROM sources WHERE folder_path = ? AND scope = ?",
            (folder_path, scope),
        )
        connection.executemany(
            "INSERT INTO sources "
            "(id, path, folder_path, source, scope, indexed_at, chunk_count) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            [
                (
                    source.id,
                    source.path,
                    source.folder_path,
                    source.source,
                    source.scope,
                    now,
                    source.chunk_count,
                )
                for source in sources
            ],
        )
    return [source_id for source_id in old_ids if source_id not in new_ids]


def delete_source_record(source_id: str) -> IndexedSource | None:
    with _connect() as connection:
        row = connection.execute("SELECT * FROM sources WHERE id = ?", (source_id,)).fetchone()
        if row is None:
            return None
        source = _source(row)
        connection.execute("DELETE FROM sources WHERE id = ?", (source_id,))
    return source
