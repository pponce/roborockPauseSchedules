#!/usr/bin/env python3

import fcntl
import sys
from contextlib import contextmanager
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from pause_until_tomorrow import read_enabled, state_file, write_enabled


@contextmanager
def operation_lock():
    path = state_file().with_suffix(".lock")
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a+", encoding="utf-8") as handle:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        yield


def main():
    if len(sys.argv) != 2 or sys.argv[1].lower() not in ("on", "off", "state"):
        print(
            "Usage: pause-until-tomorrow-controller.py on|off|state",
            file=sys.stderr,
        )
        return 2

    command = sys.argv[1].lower()
    try:
        if command == "state":
            print("true" if read_enabled() else "false")
            return 0

        with operation_lock():
            enabled = command == "on"
            write_enabled(enabled)
            print(f"Pause Until Tomorrow is now {'ENABLED' if enabled else 'DISABLED'}.")
            return 0
    except (OSError, ValueError, RuntimeError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
