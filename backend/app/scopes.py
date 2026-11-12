from contextvars import ContextVar

from .schemas import Scope

active_scope: ContextVar[Scope] = ContextVar("active_scope", default="private")
