# Managed vacuum reconfiguration

After an installation uses manifest version 2 in `managed` mode, add, remove,
or change a vacuum through one guarded registry workflow. Do not edit the live
registry in place.

## Prepare and review

Copy the current registry to private persistent storage, edit only that copy,
and create an offline stage:

```bash
install -m 600 controller/vacuums.json /private/path/vacuums.proposed.json
python3 install-roborock-pause.py stage \
  --output /private/path/stage \
  --registry /private/path/vacuums.proposed.json
python3 install-roborock-pause.py reconfigure-plan \
  --stage /private/path/stage
python3 install-roborock-pause.py reconfigure-apply \
  --stage /private/path/stage \
  --dry-run
```

Planning and dry-run never modify the installation. Both reports include an
ID-based diff, addition state paths, removal archives, and the proposed updated
manifest. When Homebridge cloud state is authoritative, separately validate a
new or changed selector using read-only discovery before approving the apply.

## Apply

Every current vacuum must be inactive and have no active primary snapshot.
After reviewing the exact stage and keeping a protected backup, apply it:

```bash
python3 install-roborock-pause.py reconfigure-apply \
  --stage /private/path/stage \
  --yes
python3 install-roborock-pause.py verify
```

Apply holds the all-vacuum lock plus locks for all current and proposed vacuum
IDs, then regenerates the plan under lock. It creates additions with inactive
mode-0600 state, moves removed-vacuum state and recovery artifacts to a private
timestamped archive, atomically replaces the registry and manifest, and runs
local verification. Any failure rolls back the registry, manifest, additions,
and archived removals.

An unchanged proposal is a successful no-op even with `--yes`. Reconfiguration
does not edit Homebridge configuration, call a Homebridge or Roborock API,
install systemd units, or invoke `systemctl`. Update command-switch configuration
and systemd only as separately reviewed operator actions.
