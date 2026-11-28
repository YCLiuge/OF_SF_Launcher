"""Single place for the three supported PC channel differences.

The launcher ships this module to both ``client_tools`` and the bundled
``OverField-Server/app`` directory.  Keeping the build-specific choices here
avoids scattered TapTap/Bilibili/Steam branches in prepare, launch and dispatch.
"""
from typing import NamedTuple, Optional


class Channel(NamedTuple):
    key: str
    name: str
    build: str
    keep_official_data: bool
    login_url_mode: str
    steam: bool = False


TAPTAP = Channel("taptap-cn-1.1.12", "taptap", "2026-10-21-14-05-50", False, "tap")
BILIBILI = Channel("bilibili-cn-1.1.12", "bilibili", "2026-10-21-14-06-13", False, "empty_list")
STEAM = Channel("steam-global-26178963", "steam", "2026-10-24-12-00-58", True, "empty", True)
CHANNELS = (TAPTAP, BILIBILI, STEAM)


def for_build(build: Optional[str]) -> Optional[Channel]:
    if not isinstance(build, str):
        return None
    return next((channel for channel in CHANNELS if channel.build == build), None)


def for_id(channel_id: Optional[str]) -> Optional[Channel]:
    if not isinstance(channel_id, str):
        return None
    return next((channel for channel in CHANNELS if channel.key == channel_id), None)
