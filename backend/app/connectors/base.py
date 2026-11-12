from abc import ABC, abstractmethod
from collections.abc import Sequence
from dataclasses import dataclass

from ..tools import ToolDefinition


@dataclass(frozen=True)
class ConnectorHealth:
    connected: bool
    status: str


class Connector(ABC):
    """Base interface for local connectors; credentials must come from a secret store."""

    name: str

    @abstractmethod
    async def connect(self) -> None:
        raise NotImplementedError

    @abstractmethod
    async def disconnect(self) -> None:
        raise NotImplementedError

    @abstractmethod
    async def healthcheck(self) -> ConnectorHealth:
        raise NotImplementedError

    @abstractmethod
    def tools(self) -> Sequence[ToolDefinition]:
        raise NotImplementedError
