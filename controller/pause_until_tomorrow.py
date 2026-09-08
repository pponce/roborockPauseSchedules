#!/usr/bin/env python3

import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from vacuum_registry import configured_root


def state_file():
    default = (
        configured_root()
        / "controller"
        / "pause-until-tomorrow-state.json"
    )
    return Path(
        os.environ.get(
            "PAUSE_UNTIL_TOMORROW_STATE_FILE",
            default,
        )
    )


def read_enabled():
    path = state_file()
    if not path.exists():
        return True

    with path.open(encoding="utf-8") as handle:
        state = json.load(handle)

    if not isinstance(state, dict) or state.get("version") != 1:
        raise RuntimeError("Pause Until Tomorrow state schema is invalid")
    if state.get("settingId") != "pause-until-tomorrow":
        raise RuntimeError("Pause Until Tomorrow state identity is invalid")
    if not isinstance(state.get("enabled"), bool):
        raise RuntimeError("Pause Until Tomorrow enabled value is invalid")
    return state["enabled"]


def write_enabled(enabled):
    path = state_file()
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp")
    with temporary.open("w", encoding="utf-8") as handle:
        json.dump(
            {
                "version": 1,
                "settingId": "pause-until-tomorrow",
                "enabled": bool(enabled),
            },
            handle,
            indent=2,
        )
        handle.write("\n")
    temporary.chmod(0o600)
    os.replace(temporary, path)
