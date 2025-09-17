"""RID helpers for Damiao motor configuration."""

from __future__ import annotations

RID_ESC_ID = 0x08
RID_MST_ID = 0x07
RID_CTRL_MODE = 0x0A
RID_P_MAX = 0x15
RID_V_MAX = 0x16
RID_T_MAX = 0x17

MANAGEMENT_WRITE = 0x55
MANAGEMENT_SAVE = 0xAA
MANAGEMENT_READ = 0x33
MANAGEMENT_REFRESH = 0xCC

__all__ = [
    "RID_ESC_ID",
    "RID_MST_ID",
    "RID_CTRL_MODE",
    "RID_P_MAX",
    "RID_V_MAX",
    "RID_T_MAX",
    "MANAGEMENT_WRITE",
    "MANAGEMENT_SAVE",
    "MANAGEMENT_READ",
    "MANAGEMENT_REFRESH",
]
