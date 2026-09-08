# Unified manifest v2 migration

Manifest v2 is the administrative model for treating version-1
installations uniformly. Planning remains strictly read-only. Applying a plan
requires a separate command and explicit confirmation; it never edits
Homebridge, invokes `systemctl`, or changes an accessory.

## Generate a plan

Copy the current external registry (or prepare a proposed edited copy), create a
structurally validated offline stage, and then point the planner at it:

```bash
cp controller/vacuums.json /tmp/vacuums.proposed.json
python3 install-roborock-pause.py stage \
  --output /tmp/roborock-migration \
  --registry /tmp/vacuums.proposed.json
python3 install-roborock-pause.py show --stage /tmp/roborock-migration
python3 install-roborock-pause.py migrate-plan \
  --stage /tmp/roborock-migration
```

The JSON output contains:

- an ID-based registry diff with added, removed, changed, and unchanged vacuums;
- state files that would be initialized for additions;
- state and recovery artifacts that must be archived for removals;
- migration provenance from the current v1 version-1 manifest; and
- the proposed normalized version 2 `managed` manifest.

The `--registry` staging path performs no accessory discovery. The planner
requires the current installation to have an external registry and a valid
version 1 manifest. It rejects root, Homebridge-setting, or systemd-setting
changes in this first migration phase. Every current vacuum must have a valid
inactive state and no active primary snapshot. Added vacuum IDs must not
collide with an existing state path. When cloud state is authoritative, run
the separately documented live read-only verification before approving a plan
that changes selectors or adds a vacuum.

`migrate-plan` always reports `"appliesChanges": false` and has no confirmation
or apply option. Preserve the generated plan as private deployment material:
paths, selectors, and artifact inventory may reveal local installation details.

## Guarded apply

First inspect the no-write result, then exercise the exact apply path without
mutation:

```bash
python3 install-roborock-pause.py migrate-apply \
  --stage /tmp/roborock-migration \
  --dry-run
```

Only after separately reviewing that output, apply it with `--yes`:

```bash
python3 install-roborock-pause.py migrate-apply \
  --stage /tmp/roborock-migration \
  --yes
```

The apply command takes the all-vacuum and affected per-vacuum locks and then
recreates the complete plan under those locks. Added vacuums receive a new
inactive mode-0600 state file. Removed-vacuum state, backup, active, and manual
recovery snapshots are moved into a private timestamped archive. Registry and
manifest replacement is atomic, post-apply local verification is mandatory,
and any failure restores the prior registry, manifest, removed artifacts, and
addition state. An unchanged registry therefore performs only the controlled
v1-to-v2 administrative transition.

This command intentionally performs no accessory discovery. Use `verify
--live` as a separate read-only gate when the cloud source is authoritative.
Homebridge configuration and systemd activation remain explicit operator
steps.
