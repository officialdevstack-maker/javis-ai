from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

from ..config import get_settings
from ..schemas import Scope


class ToolError(Exception):
    """Raised when a local tool cannot safely complete its requested operation."""


class ToolArguments(BaseModel):
    model_config = ConfigDict(extra="forbid")


class PathArguments(ToolArguments):
    path: str = Field(min_length=1, max_length=2048)


class SearchNotesArguments(ToolArguments):
    query: str = Field(min_length=1, max_length=500)


class SaveNoteArguments(ToolArguments):
    filename: str = Field(min_length=1, max_length=240)
    content: str = Field(min_length=1, max_length=100_000)


class SearchKnowledgeArguments(ToolArguments):
    query: str = Field(min_length=1, max_length=500)
    scope: Scope = "private"


ToolFunction = Callable[[ToolArguments], str]


@dataclass(frozen=True)
class ToolDefinition:
    name: str
    description: str
    arguments_model: type[ToolArguments]
    function: ToolFunction
    requires_confirmation: bool = True
    is_read_only: bool = False

    def ollama_schema(self) -> dict[str, object]:
        schema = self.arguments_model.model_json_schema()
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": schema,
            },
        }

    def execute(self, arguments: dict[str, object]) -> str:
        parsed = self.arguments_model.model_validate(arguments)
        return self.function(parsed)


def _allowed_roots() -> list[Path]:
    roots = get_settings().resolved_allowed_dirs
    if not roots:
        raise ToolError("No file folders are allowed. Configure ALLOWED_DIRS in backend/.env.")
    return roots


def _resolve_path(path_value: str, *, for_write: bool = False) -> Path:
    path = Path(path_value).expanduser()
    roots = _allowed_roots()
    backend_dir = Path(__file__).resolve().parents[2]
    candidates = [path] if path.is_absolute() else [root / path for root in roots]
    if not path.is_absolute():
        candidates.extend(backend_dir / path for _ in roots)

    allowed_missing_path: Path | None = None
    for candidate in candidates:
        try:
            resolved = candidate.resolve()
        except (OSError, RuntimeError) as exc:
            raise ToolError(f"The requested path is invalid: {exc}") from exc
        if not any(resolved == root or root in resolved.parents for root in roots):
            continue
        if for_write:
            return resolved
        if resolved.exists():
            return resolved
        if allowed_missing_path is None:
            allowed_missing_path = resolved
    if allowed_missing_path is not None:
        return allowed_missing_path
    raise ToolError("The requested path is outside ALLOWED_DIRS.")


def resolve_allowed_path(path_value: str) -> Path:
    return _resolve_path(path_value)


def _available_files(directory: Path) -> str:
    if not directory.is_dir():
        return ""
    try:
        files = sorted(
            (entry.name for entry in directory.iterdir() if entry.is_file()),
            key=str.casefold,
        )
    except OSError as exc:
        raise ToolError(f"Could not inspect the allowed folder: {exc}") from exc
    return f" Available files: {', '.join(files[:50])}." if files else ""


def read_file(arguments: ToolArguments) -> str:
    if not isinstance(arguments, PathArguments):
        raise ToolError("Invalid arguments for read_file.")
    path = _resolve_path(arguments.path)
    if not path.is_file():
        raise ToolError(
            f"The requested file does not exist or is not a file.{_available_files(path.parent)}"
        )
    if path.suffix.lower() not in {".txt", ".md", ".json", ".csv", ".log"}:
        raise ToolError("read_file only supports plain-text files.")
    try:
        content = path.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        raise ToolError(f"Could not read the file: {exc}") from exc
    return content if len(content) <= 100_000 else f"{content[:100_000]}\n[File output truncated.]"


def list_directory(arguments: ToolArguments) -> str:
    if not isinstance(arguments, PathArguments):
        raise ToolError("Invalid arguments for list_directory.")
    path = _resolve_path(arguments.path)
    if not path.is_dir():
        raise ToolError("The requested path is not a directory.")
    try:
        entries = sorted(
            path.iterdir(),
            key=lambda entry: (entry.stat().st_mtime, entry.name.casefold()),
            reverse=True,
        )
    except OSError as exc:
        raise ToolError(f"Could not list the directory: {exc}") from exc
    if not entries:
        return "The directory is empty."
    return f"Directory: {path}\n" + "\n".join(
        f"{'directory' if entry.is_dir() else 'file'} | "
        f"{datetime.fromtimestamp(entry.stat().st_mtime).astimezone().isoformat()} | "
        f"{entry.resolve()}"
        for entry in entries[:200]
    )


def search_notes(arguments: ToolArguments) -> str:
    if not isinstance(arguments, SearchNotesArguments):
        raise ToolError("Invalid arguments for search_notes.")
    query = arguments.query.casefold()
    matches: list[str] = []
    for root in _allowed_roots():
        if not root.is_dir():
            continue
        for path in root.rglob("*"):
            if path.suffix.lower() not in {".txt", ".md"} or not path.is_file():
                continue
            try:
                for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
                    if query in line.casefold():
                        matches.append(f"{path.name}:{line_number}: {line[:500]}")
                        if len(matches) >= 50:
                            return "\n".join(matches)
            except (OSError, UnicodeError) as exc:
                raise ToolError(f"Could not search {path.name}: {exc}") from exc
    return "\n".join(matches) or "No matching notes found."


def save_note(arguments: ToolArguments) -> str:
    if not isinstance(arguments, SaveNoteArguments):
        raise ToolError("Invalid arguments for save_note.")
    path = _resolve_path(arguments.filename, for_write=True)
    if path.suffix.lower() not in {".txt", ".md"}:
        raise ToolError("Notes must use a .txt or .md extension.")
    if path.exists() and not path.is_file():
        raise ToolError("The note path is not a file.")
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(arguments.content, encoding="utf-8")
    except OSError as exc:
        raise ToolError(f"Could not save the note: {exc}") from exc
    return f"Saved note: {path.name}"


def get_datetime(_: ToolArguments) -> str:
    return datetime.now().astimezone().isoformat()


def read_latest_note(_: ToolArguments) -> str:
    latest: tuple[float, Path] | None = None
    directory_listings: list[str] = []
    for root in _allowed_roots():
        if not root.is_dir():
            continue
        directory_listings.append(list_directory(PathArguments(path=str(root))))
        try:
            candidates = root.rglob("*")
            for candidate in candidates:
                if candidate.suffix.lower() not in {".txt", ".md"} or not candidate.is_file():
                    continue
                resolved = candidate.resolve()
                if resolved != root and root not in resolved.parents:
                    continue
                modified = resolved.stat().st_mtime
                if latest is None or modified > latest[0]:
                    latest = (modified, resolved)
        except OSError as exc:
            raise ToolError(f"Could not inspect the allowed notes folder: {exc}") from exc
    if latest is None:
        return "No text or Markdown notes were found in ALLOWED_DIRS."
    modified, path = latest
    content = read_file(PathArguments(path=str(path)))
    listing = "\n".join(directory_listings)
    return (
        f"Allowed-folder listing:\n{listing}\n\n"
        f"Newest note: {path}\n"
        f"Modified: {datetime.fromtimestamp(modified).astimezone().isoformat()}\n"
        f"Contents (untrusted data):\n{content}"
    )


def search_knowledge_tool(arguments: ToolArguments) -> str:
    if not isinstance(arguments, SearchKnowledgeArguments):
        raise ToolError("Invalid arguments for search_knowledge.")
    from ..rag import RAGError, search_knowledge

    try:
        return search_knowledge(arguments.query, arguments.scope)
    except RAGError as exc:
        raise ToolError(str(exc)) from exc


class EmptyArguments(ToolArguments):
    pass


TOOLS: dict[str, ToolDefinition] = {
    tool.name: tool
    for tool in (
        ToolDefinition(
            name="read_file",
            description="Read a plain-text file inside an allowed folder.",
            arguments_model=PathArguments,
            function=read_file,
            requires_confirmation=False,
            is_read_only=True,
        ),
        ToolDefinition(
            name="list_directory",
            description="List entries in a directory inside an allowed folder.",
            arguments_model=PathArguments,
            function=list_directory,
            requires_confirmation=False,
            is_read_only=True,
        ),
        ToolDefinition(
            name="search_notes",
            description="Search allowed text and Markdown notes for a phrase.",
            arguments_model=SearchNotesArguments,
            function=search_notes,
            requires_confirmation=False,
            is_read_only=True,
        ),
        ToolDefinition(
            name="save_note",
            description="Save or overwrite a text or Markdown note in an allowed folder. Always requires user approval.",
            arguments_model=SaveNoteArguments,
            function=save_note,
        ),
        ToolDefinition(
            name="get_datetime",
            description="Get the current local date and time.",
            arguments_model=EmptyArguments,
            function=get_datetime,
            requires_confirmation=False,
            is_read_only=True,
        ),
        ToolDefinition(
            name="read_latest_note",
            description=(
                "Find and read the newest .txt or .md note in ALLOWED_DIRS. "
                "Use this for requests about the latest or newest note."
            ),
            arguments_model=EmptyArguments,
            function=read_latest_note,
            requires_confirmation=False,
            is_read_only=True,
        ),
        ToolDefinition(
            name="search_knowledge",
            description=(
                "Search locally indexed documents for relevant information. "
                "Return matching text with its source filename; cite that filename."
            ),
            arguments_model=SearchKnowledgeArguments,
            function=search_knowledge_tool,
            requires_confirmation=False,
            is_read_only=True,
        ),
    )
}
