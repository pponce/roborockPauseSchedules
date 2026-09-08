#!/usr/bin/env python3
"""Build the public runtime ZIP and its SHA-256 checksum."""

import argparse
import hashlib
import re
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
INCLUDED_FILES = (
    ".gitignore", "README.md", "all-vacuums-pause-expire.sh",
    "all-vacuums-pause-off.sh", "all-vacuums-pause-on.sh",
    "all-vacuums-pause-state.sh", "discover-vacuum-config.py",
    "get-homebridge-token.sh", "install-roborock-pause.py",
    "pause-until-tomorrow-off.sh", "pause-until-tomorrow-on.sh",
    "pause-until-tomorrow-state.sh", "render-systemd-units.py",
    "setup-homebridge-auto-auth.sh",
)
INCLUDED_GLOBS = (
    "config/*.example.json",
    "controller/*.py",
    "docs/*.md",
    "systemd/*",
)
PRIVATE_NAMES = {
    "homebridge-access-token", "homebridge-api-credentials.json",
    "installer-manifest.json", "vacuums.json",
}


def release_files():
    paths = [ROOT / name for name in INCLUDED_FILES]
    for pattern in INCLUDED_GLOBS:
        paths.extend(path for path in ROOT.glob(pattern) if path.is_file())
    return sorted(
        path for path in paths
        if path.name not in PRIVATE_NAMES
        and "__pycache__" not in path.parts
        and not any(part.startswith("removed-vacuums-") for part in path.parts)
    )


def build(version, output_directory):
    if not re.fullmatch(r"[0-9]+(?:\.[0-9]+){2}(?:[-+][A-Za-z0-9.-]+)?", version):
        raise ValueError("version must look like 1.2.3 or 1.2.3-rc1")
    files = release_files()
    missing = [str(path.relative_to(ROOT)) for path in files if not path.exists()]
    if missing:
        raise FileNotFoundError("missing release files: " + ", ".join(missing))
    output_directory.mkdir(parents=True, exist_ok=True)
    archive = output_directory / f"roborockPauseSchedules-{version}.zip"
    prefix = f"roborockPauseSchedules-{version}"
    with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED) as bundle:
        for path in files:
            bundle.write(path, f"{prefix}/{path.relative_to(ROOT)}")
    digest = hashlib.sha256(archive.read_bytes()).hexdigest()
    checksum = archive.with_suffix(archive.suffix + ".sha256")
    checksum.write_text(f"{digest}  {archive.name}\n", encoding="utf-8")
    return archive, checksum


def main(arguments=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--version", required=True)
    parser.add_argument("--output", default=str(ROOT / "dist"))
    args = parser.parse_args(arguments)
    try:
        archive, checksum = build(args.version, Path(args.output).resolve())
    except (OSError, ValueError) as exc:
        parser.error(str(exc))
    print(archive)
    print(checksum)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
