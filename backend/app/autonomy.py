from __future__ import annotations

from threading import RLock
from typing import TYPE_CHECKING

from .db import get_setting, set_setting
from .schemas import AutonomyStatus, ToolPolicyInfo

if TYPE_CHECKING:
    from .tools import ToolDefinition


_KILL_SWITCH_KEY = "autonomy.kill_switch_enabled"
_action_lock = RLock()


def kill_switch_enabled() -> bool:
    return get_setting(_KILL_SWITCH_KEY) == "true"


def set_kill_switch(enabled: bool) -> None:
    with _action_lock:
        set_setting(_KILL_SWITCH_KEY, "true" if enabled else "false")


def execute_tool_if_enabled(
    tool: ToolDefinition,
    arguments: dict[str, object],
) -> tuple[bool, str]:
    with _action_lock:
        if tool.tier >= 2 and kill_switch_enabled():
            return False, "Blocked: the autonomy kill switch is enabled; no action was taken."
        return True, tool.execute(arguments)


def requires_approval(
    tool: ToolDefinition,
    *,
    untrusted_context: bool,
) -> bool:
    if untrusted_context and not tool.is_read_only:
        return True
    return tool.tier >= 2 or tool.requires_confirmation or not tool.is_read_only


def status() -> AutonomyStatus:
    from .tools import TOOLS

    return AutonomyStatus(
        kill_switch_enabled=kill_switch_enabled(),
        tools=[
            ToolPolicyInfo(
                name=tool.name,
                tier=tool.tier,
                requires_confirmation=requires_approval(
                    tool,
                    untrusted_context=False,
                ),
                is_read_only=tool.is_read_only,
                tier3_opt_in=tool.tier3_opt_in,
                tier3_limits=tool.tier3_limits,
            )
            for tool in TOOLS.values()
        ],
    )
