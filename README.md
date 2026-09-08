# Roborock Pause Schedules

This project adds HomeKit switches that temporarily turn off your Roborock
cleaning schedules and later restore them to exactly the state they were in.
It works with schedules exposed by `homebridge-roborock-matter` through the
Homebridge API.

You do **not** need to fork this repository or know how to program. Choose one
of the download methods below, then follow the fresh-install checklist.

## Before you begin

You need:

- a Linux computer running Homebridge and `homebridge-roborock-matter`;
- Python 3 and Bash;
- a dedicated Homebridge user account for this automation; and
- the vacuum, schedule, cleaning, docked, and return-to-dock services visible
  in Homebridge.

The commands below assume the final installation folder is
`/var/lib/homebridge/roborockPauseSchedules`. If you choose another folder, set
`ROBOROCK_PAUSE_ROOT` to that absolute path before running the tools.

## 1. Download the project

### Option A: download a ZIP (simplest)

1. Open the project's GitHub **Releases** page.
2. Open the newest release and download the attached
   `roborockPauseSchedules-<version>.zip` file.
3. Optionally compare the download with the attached `.sha256` file.
4. Extract the ZIP and move the extracted folder to the final installation
   path.

Example:

```bash
sudo mv roborockPauseSchedules-1.0.0 /var/lib/homebridge/roborockPauseSchedules
cd /var/lib/homebridge/roborockPauseSchedules
```

ZIP installations run normally. The only limitation is that the guarded Git
upgrade commands cannot update them; download and review a newer ZIP when you
want to upgrade.

### Option B: clone with Git (easier future upgrades)

```bash
sudo git clone https://github.com/pponce/roborockPauseSchedules.git \
  /var/lib/homebridge/roborockPauseSchedules
cd /var/lib/homebridge/roborockPauseSchedules
```

No fork is required. A clone has Git history, so it can later use
`upgrade-plan` and `upgrade-apply` as described in
[the upgrade guide](docs/MANAGED_UPGRADE.md).

## 2. Fresh install, step by step

### Step 1: create the Homebridge login files

Run this from the project folder and enter the dedicated Homebridge account
when prompted:

```bash
bash setup-homebridge-auto-auth.sh
```

The password and token are stored locally with restricted permissions. They
are ignored by Git and are never part of a release ZIP.

### Step 2: discover your vacuums (read only)

```bash
python3 discover-vacuum-config.py --report
python3 discover-vacuum-config.py --output /tmp/vacuums.proposed.json
python3 discover-vacuum-config.py --validate /tmp/vacuums.proposed.json
```

Discovery only reads Homebridge accessories. Review the proposed file. Each
vacuum should have the correct display name and five service-name settings.

### Step 3: prepare and review the installation

```bash
python3 install-roborock-pause.py stage \
  --output /tmp/roborock-pause-stage \
  --registry /tmp/vacuums.proposed.json
python3 install-roborock-pause.py show --stage /tmp/roborock-pause-stage
python3 install-roborock-pause.py install \
  --stage /tmp/roborock-pause-stage --dry-run
```

The first command makes a private installation package. The next two commands
show what it contains and what would change. Nothing is installed yet.

### Step 4: install and verify

```bash
python3 install-roborock-pause.py install \
  --stage /tmp/roborock-pause-stage --yes
python3 install-roborock-pause.py verify --live
```

`--yes` is intentionally required for a real change. Installation creates the
registry, an inactive state file for each vacuum, and a tamper-evident manifest.
It does not edit Homebridge, contact Roborock with a write, or run `systemctl`.

### Step 5: add the HomeKit switches

The staged `command-switches.json` file contains the exact commands for your
vacuum IDs. Add them to Script2 (or another Homebridge command-switch plugin):

```text
ON:    python3 /var/lib/homebridge/roborockPauseSchedules/controller/vacuum-pause-controller.py VACUUM_ID on
OFF:   python3 /var/lib/homebridge/roborockPauseSchedules/controller/vacuum-pause-controller.py VACUUM_ID off
STATE: python3 /var/lib/homebridge/roborockPauseSchedules/controller/vacuum-pause-controller.py VACUUM_ID state
```

You may also add these project-wide switches:

| Switch | ON command | OFF command | State command |
|---|---|---|---|
| Pause All | `all-vacuums-pause-on.sh` | `all-vacuums-pause-off.sh` | `all-vacuums-pause-state.sh` |
| Pause Until Tomorrow | `pause-until-tomorrow-on.sh` | `pause-until-tomorrow-off.sh` | `pause-until-tomorrow-state.sh` |

Keep existing HomeKit accessory names and identifiers when changing commands,
otherwise HomeKit may treat them as new accessories.

### Step 6: test without changing a schedule

Replace `VACUUM_ID` with an ID from `controller/vacuums.json`:

```bash
python3 controller/vacuum-pause-controller.py VACUUM_ID state
python3 controller/vacuum-pause-controller.py VACUUM_ID off --dry-run
bash all-vacuums-pause-state.sh
bash pause-until-tomorrow-state.sh
```

Only use the HomeKit ON/OFF switches for a real test when you are ready for the
controller to change schedules. The controller saves the original schedule
states before writing and keeps its recovery snapshot if verification fails.

## Everyday use

- Turn a vacuum's switch **on** to save its schedule states and turn all of its
  schedules off.
- Turn that switch **off** to restore only the states owned by that pause.
- Turn **Pause All** on or off to operate every configured vacuum.
- Leave **Pause Until Tomorrow** on if the optional midnight timer should end
  active pauses. Turn it off when pauses should remain until manually ended.

## Add a vacuum

Do not edit the live registry directly.

1. Make sure all existing pause switches are off.
2. Run read-only discovery and save a new complete proposal:

   ```bash
   python3 discover-vacuum-config.py --output /tmp/vacuums.updated.json
   python3 discover-vacuum-config.py --validate /tmp/vacuums.updated.json
   ```

3. Stage and review the proposal:

   ```bash
   python3 install-roborock-pause.py stage \
     --output /tmp/roborock-reconfigure-stage \
     --registry /tmp/vacuums.updated.json
   python3 install-roborock-pause.py reconfigure-plan \
     --stage /tmp/roborock-reconfigure-stage
   python3 install-roborock-pause.py reconfigure-apply \
     --stage /tmp/roborock-reconfigure-stage --dry-run
   ```

4. Apply and verify only after the plan shows the expected addition:

   ```bash
   python3 install-roborock-pause.py reconfigure-apply \
     --stage /tmp/roborock-reconfigure-stage --yes
   python3 install-roborock-pause.py verify --live
   ```

5. Add the new vacuum's ON, OFF, and STATE commands to Homebridge using the
   new vacuum ID.

## Remove a vacuum

1. Turn that vacuum's pause switch off and confirm its schedules are restored.
2. Copy the live registry and remove only that vacuum's object from the
   `vacuums` list:

   ```bash
   cp controller/vacuums.json /tmp/vacuums.updated.json
   nano /tmp/vacuums.updated.json
   ```

3. Use the same `stage`, `reconfigure-plan`, `reconfigure-apply --dry-run`,
   `reconfigure-apply --yes`, and `verify --live` commands shown in **Add a
   vacuum**.
4. Remove that vacuum's command switch from Homebridge.

Removal archives the vacuum's state and recovery files instead of silently
deleting them. Full safety and rollback details are in
[the reconfiguration guide](docs/RECONFIGURE.md).

## Optional automatic expiration timer

The supplied systemd timer runs at 00:05 local time. Render the files first so
you can review them:

```bash
python3 render-systemd-units.py --output /tmp/roborock-systemd
```

The renderer never runs `systemctl` and never replaces existing output. See
the generated files in `/tmp/roborock-systemd` before installing and enabling
them yourself.

## Script and file guide

| File or folder | Plain-English purpose |
|---|---|
| `discover-vacuum-config.py` | Finds compatible Homebridge services without changing them. |
| `install-roborock-pause.py` | Safely stages, installs, checks, reconfigures, upgrades, or uninstalls local configuration. Run it with `--help` to see every command. |
| `controller/vacuum-pause-controller.py` | Runs `init`, `state`, `on`, `off`, `reconcile`, or emergency `abandon` for one vacuum ID. |
| `all-vacuums-pause-*.sh` | Runs ON, OFF, STATE, or expiration for every configured vacuum. |
| `pause-until-tomorrow-*.sh` | Enables, disables, or reads the automatic-expiration preference. |
| `setup-homebridge-auto-auth.sh` | Securely creates the local Homebridge login and token files. |
| `render-systemd-units.py` | Makes optional timer files for review; it does not install or enable them. |
| `config/vacuums.example.json` | Example only; your real registry is `controller/vacuums.json`. |
| `controller/` | The main program plus private local state, registry, and recovery files. |
| `systemd/` | Default timer and service examples. |
| `docs/` | Detailed administrator, upgrade, reconfiguration, and release notes. |
| `tests/` | Automated safety checks for developers; they do not perform live Roborock writes. |

Useful help commands:

```bash
python3 install-roborock-pause.py --help
python3 install-roborock-pause.py stage --help
python3 controller/vacuum-pause-controller.py --help
python3 discover-vacuum-config.py --help
python3 render-systemd-units.py --help
```

## Recovery and uninstalling

Schedule reads use a finite retry window so an account-session recovery in the
Roborock Homebridge plugin can finish without creating an unbounded controller
operation. A repeated ON request reconciles an existing incomplete transaction:
it reads current state first, disables only schedules still observed ON, and
verifies the result. `Pause All` remains ON for a valid active transaction while
reporting whether it is complete, in progress, or requires recovery; malformed
or mismatched state still fails closed. Runtime state and snapshot JSON files
are replaced atomically with mode `0600`.

When a vacuum is actively cleaning, activation verifies that its schedules are
OFF and then submits Return to Dock. The pause transaction is complete when that
command is acknowledged; it does not wait for or claim that the vacuum physically
reached the dock. Dock-arrival monitoring is intentionally outside this project's
scope.

The Script2-facing Pause All ON and OFF wrappers dispatch their aggregate work
to a detached controller process, allowing the HomeKit write callback to return
promptly instead of waiting for every Roborock schedule transaction. The direct
Python `on` and `off` commands remain synchronous for administration and tests.
Background output is written privately to
`controller/all-vacuums-pause-operation.log`; the state command continues to
report the persisted, verified controller state as the operation progresses.

If a normal `off` or `reconcile` fails, do not delete state or snapshot files.
Fix Homebridge/cloud connectivity and retry. The emergency `abandon` command
does not restore schedules; use it only after independently restoring schedules
in the Roborock app:

```bash
python3 controller/vacuum-pause-controller.py VACUUM_ID abandon \
  --confirm I-WILL-RESTORE-SCHEDULES-MANUALLY
```

To remove installer-owned configuration while preserving vacuum state files:

```bash
python3 install-roborock-pause.py uninstall --dry-run
python3 install-roborock-pause.py uninstall --yes
```

## More detailed documentation

- [Managed reconfiguration](docs/RECONFIGURE.md)
- [Git-based upgrades](docs/MANAGED_UPGRADE.md)
- [Upgrade safety notes](docs/UPGRADE.md)
- [Older manifest migration](docs/MANIFEST_V2_MIGRATION.md)
- [Release checklist](docs/RELEASE_CHECKLIST.md)
- [Publishing releases](docs/RELEASING.md)
