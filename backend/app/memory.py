import re

from .db import add_memory, list_memories
from .schemas import MemoryItem, Scope

__all__ = ["add_memory", "list_memories", "recall", "relevant_memories"]

_WORD = re.compile(r"[a-z0-9']{3,}")


def _tokens(text: str) -> set[str]:
    return set(_WORD.findall(text.casefold()))


def recall(query: str, scope: Scope, limit: int = 5) -> list[MemoryItem]:
    query_tokens = _tokens(query)
    scored = [(len(query_tokens & _tokens(item.text)), item) for item in list_memories(scope)]
    ranked = sorted(scored, key=lambda pair: -pair[0])
    return [item for score, item in ranked if score > 0][:limit]


def relevant_memories(query: str, scope: Scope, limit: int = 5) -> list[MemoryItem]:
    matches = recall(query, scope, limit)
    if matches:
        return matches
    return list_memories(scope)[:3]
