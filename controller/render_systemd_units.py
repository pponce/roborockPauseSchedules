#!/usr/bin/env python3

"""Render systemd units for the configured installation without installing them."""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from vacuum_registry import load_registry


SERVICE_NAME = "roborock-pause-until-tomorrow.service"
TIMER_NAME = "roborock-pause-until-tomorrow.timer"
RECONCILE_SERVICE_NAME = "roborock-pause-reconcile.service"
RECONCILE_TIMER_NAME = "roborock-pause-reconcile.timer"


def render_units(registry):
    root = registry["root"]
    settings = registry["systemd"]
    if any(character.isspace() for character in str(root)):
        raise ValueError("systemd installation root cannot contain whitespace")
    if any(character.isspace() for character in settings["bashPath"]):
        raise ValueError("systemd bashPath cannot contain whitespace")
    service = f"""[Unit]
Description=Restore Roborock schedules paused until tomorrow
After=network-online.target {settings['homebridgeUnit']}
Wants=network-online.target

[Service]
Type=oneshot
User={settings['user']}
Group={settings['group']}
WorkingDirectory={root}
ExecStart={settings['bashPath']} {root / 'all-vacuums-pause-expire.sh'}
"""
    timer = f"""[Unit]
Description=Restore Roborock schedules shortly after midnight

[Timer]
OnCalendar={settings['onCalendar']}
Persistent=true
Unit={SERVICE_NAME}

[Install]
WantedBy=timers.target
"""
    reconcile_service = f"""[Unit]
Description=Reconcile Roborock pause operations against Homebridge
After=network-online.target {settings['homebridgeUnit']}
Wants=network-online.target

[Service]
Type=oneshot
User={settings['user']}
Group={settings['group']}
WorkingDirectory={root}
ExecStart={settings['bashPath']} -c 'python3 controller/all-vacuums-pause-controller.py maintain'
"""
    reconcile_timer = f"""[Unit]
Description=Recheck Roborock pause operations every minute

[Timer]
OnBootSec=60
OnUnitInactiveSec=60
Unit={RECONCILE_SERVICE_NAME}

[Install]
WantedBy=timers.target
"""
    return {SERVICE_NAME: service, TIMER_NAME: timer,
            RECONCILE_SERVICE_NAME: reconcile_service,
            RECONCILE_TIMER_NAME: reconcile_timer}


def write_units(units, output_directory):
    directory = Path(output_directory)
    paths = [directory / name for name in units]
    existing = [path for path in paths if path.exists()]
    if existing:
        raise FileExistsError(
            "refusing to overwrite existing unit(s): "
            + ", ".join(str(path) for path in existing)
        )
    directory.mkdir(parents=True, exist_ok=True)
    for name, contents in units.items():
        (directory / name).write_text(contents, encoding="utf-8")


def main(arguments=None):
    parser = argparse.ArgumentParser(
        description="Render, but do not install, Pause Until Tomorrow systemd units"
    )
    parser.add_argument("--output", help="new directory for rendered unit files")
    args = parser.parse_args(arguments)
    try:
        units = render_units(load_registry())
        if args.output:
            write_units(units, args.output)
        else:
            for name, contents in units.items():
                print(f"### {name}")
                print(contents, end="")
        return 0
    except (FileExistsError, OSError, TypeError, ValueError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
