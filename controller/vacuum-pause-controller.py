#!/usr/bin/env python3

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import schedule_pause_controller as engine
from vacuum_registry import engine_config, homebridge_config, load_registry


def main():
    if len(sys.argv) < 3:
        print(
            "Usage: vacuum-pause-controller.py VACUUM_ID "
            "on|off|state|reconcile|abandon|init "
            "[--dry-run|--confirm PHRASE]",
            file=sys.stderr,
        )
        return 2

    vacuum_id = sys.argv[1]
    command_arguments = sys.argv[2:]
    try:
        registry = load_registry()
        config = engine_config(engine, registry, vacuum_id)
        engine.configure_homebridge(**homebridge_config(registry))
    except (OSError, ValueError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2

    command = command_arguments[0].lower()
    if command == "state":
        if len(command_arguments) != 1:
            print("ERROR: state does not accept arguments", file=sys.stderr)
            return 2
        try:
            engine.configure(config)
            state = engine.load_state()
            print("true" if state["pauseActive"] else "false")
            return 0
        except (OSError, ValueError, RuntimeError) as exc:
            print(f"ERROR: {exc}", file=sys.stderr)
            return 1

    if command == "init":
        if len(command_arguments) != 1:
            print("ERROR: init does not accept arguments", file=sys.stderr)
            return 2
        try:
            engine.configure(config)
            with engine.operation_lock():
                if Path(config.state_file).exists():
                    raise RuntimeError("pause state already exists")
                engine.persist_state(False, f"{vacuum_id}-initial-{engine.utc_now()}")
            return 0
        except (OSError, ValueError, RuntimeError) as exc:
            print(f"ERROR: {exc}", file=sys.stderr)
            return 1

    original_arguments = sys.argv
    try:
        sys.argv = [original_arguments[0], *command_arguments]
        return engine.main(config)
    finally:
        sys.argv = original_arguments


if __name__ == "__main__":
    raise SystemExit(main())
