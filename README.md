# Roborock Pause Schedules

This project adds HomeKit switches that temporarily turn off your Roborock
cleaning schedules and later restore the saved states owned by that pause,
while preserving detected manual changes.
It works with schedules exposed by `homebridge-roborock-matter` through the
Homebridge API.

You do **not** need to fork this repository or know how to program. Choose one
of the download methods below, then follow the fresh-install checklist.

## Before you begin

You need:

- a Linux computer running Homebridge and `homebridge-roborock-matter`;
- Python 3 and Bash, plus `unzip` for ZIP installations;
- systemd for the documented background reconciliation setup;
- Script2 (or another command-switch plugin) for the HomeKit pause switches;
- a dedicated Homebridge user account for this automation; and
- the vacuum, schedule, cleaning, docked, and return-to-dock services visible
  in Homebridge.

The commands below assume the final installation folder is
`/var/lib/homebridge/roborockPauseSchedules`. If you choose another folder, set
`ROBOROCK_PAUSE_ROOT` to that absolute path before running the tools.

The pause controller uses the existing Homebridge interface. It does not need
a separate Roborock login or a pause-specific API in the vacuum plugin.
Homebridge switch values provide **best-effort observations**, not guaranteed
robot acknowledgement. Background reconciliation is part of the normal setup;
the separate midnight-expiration timer is optional.

## 1. Download the project

### Option A: download a ZIP (simplest)

1. Open the project's [GitHub Releases](https://github.com/pponce/roborockPauseSchedules/releases) page.
2. Open the newest release and download the attached
   `roborockPauseSchedules-<version>.zip` file.
3. Download the matching `.zip.sha256` file and verify the checksum.
4. Extract the ZIP and move the extracted folder to the final installation
   path.

Example for v1.0.2, from the directory containing both downloads. Use the
attached project ZIP, rather than GitHub's automatic **Source code (zip)**.
These commands are for a **fresh installation**; the destination must not
already exist:

```bash
sha256sum --check roborockPauseSchedules-1.0.2.zip.sha256 &&
unzip roborockPauseSchedules-1.0.2.zip &&
test ! -e /var/lib/homebridge/roborockPauseSchedules &&
sudo mv roborockPauseSchedules-1.0.2 /var/lib/homebridge/roborockPauseSchedules &&
sudo chown -R homebridge:homebridge /var/lib/homebridge/roborockPauseSchedules &&
cd /var/lib/homebridge/roborockPauseSchedules
```

ZIP installations run normally. The only limitation is that the guarded Git
upgrade commands cannot update them. Use the [ZIP upgrade guide](docs/ZIP_UPGRADE.md)
to update program files while preserving your registry, credentials, and snapshots.

### Option B: clone with Git (easier future upgrades)

```bash
sudo -H -u homebridge git clone https://github.com/pponce/roborockPauseSchedules.git \
  /var/lib/homebridge/roborockPauseSchedules &&
cd /var/lib/homebridge/roborockPauseSchedules
```

No fork is required. A clone has Git history, so it can later use
`upgrade-plan` and `upgrade-apply` as described in
[the upgrade guide](docs/MANAGED_UPGRADE.md).

### Run setup as the runtime owner

The examples assume the Linux service account and group are both `homebridge`.
Run the remaining Python and project-script commands as that account, for
example in a shell opened with:

```bash
sudo -H -u homebridge bash
cd /var/lib/homebridge/roborockPauseSchedules
```

Use your regular sudo-capable login for commands beginning with `sudo`, such as
installing systemd files. Adjust the account and registry settings if Homebridge
runs under another user. Do not change an existing checkout's ownership or add
a global Git `safe.directory` exception to fix a command run as the wrong user.

A separate development checkout does not update the running installation.
Script2 commands and systemd `WorkingDirectory` must point to the **live root**.

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

For a fresh installation, migrate the new version-1 manifest to version 2 while
all pauses are still inactive. This enables the later add/remove-vacuum and
managed Git-upgrade workflows. Reuse the reviewed stage from Step 3:

```bash
python3 install-roborock-pause.py migrate-plan --stage /tmp/roborock-pause-stage
python3 install-roborock-pause.py migrate-apply \
  --stage /tmp/roborock-pause-stage --dry-run
python3 install-roborock-pause.py migrate-apply \
  --stage /tmp/roborock-pause-stage --yes
python3 install-roborock-pause.py verify
```

For an existing version-2 manifest, skip this migration. It is an administrative
step; it does not install timers or operate a vacuum.

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

Use absolute paths in Script2. For example, the Pause All ON command is
`bash /var/lib/homebridge/roborockPauseSchedules/all-vacuums-pause-on.sh`.
Use the matching OFF and STATE scripts in the same directory. These aggregate
wrappers return promptly; direct per-vacuum Python commands wait for the initial
attempt. Neither successful exit nor an immediate switch change proves that
Roborock has completed the schedule operation.

### Step 6: enable background reconciliation

Complete [Background reconciliation timer](#background-reconciliation-timer)
below before using the pause switches. Both ZIP and Git installations need this
step. The installer and ZIP supply the files but do not enable the timer.

The midnight timer has a different purpose and is configured separately under
[Optional automatic expiration timer](#optional-automatic-expiration-timer).

### Step 7: test without changing a schedule

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

After an intentional pause or restore, inspect progress with:

```bash
python3 controller/all-vacuums-pause-controller.py status
```

`planning` or `settling` means work remains; `complete` means the exposed values
passed the observation window; `needs-attention` means a disagreement or error
needs investigation. `complete` is still best-effort, not cloud-authoritative proof.

## Everyday use

- Turn a vacuum's switch **on** to save its schedule states and turn all of its
  schedules off.
- Turn that switch **off** to restore only the states owned by that pause.
- Turn **Pause All** on or off to operate every configured vacuum.
- Leave **Pause Until Tomorrow** on if the optional midnight timer should end
  active pauses. Turn it off when pauses should remain until manually ended.

## Add a vacuum

Do not edit the live registry directly. These commands require a version-2
manifest; older installations should first follow the
[manifest migration guide](docs/MANIFEST_V2_MIGRATION.md).

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

## Background reconciliation timer

Two independent timers serve different purposes:

| Timer | Purpose | When it runs |
|---|---|---|
| `roborock-pause-reconcile.timer` | Rechecks pending pause/restore operations and retries mismatched schedules within their budget. | 60 seconds after boot and 60 seconds after the previous service run finishes. |
| `roborock-pause-until-tomorrow.timer` | Requests restoration of active pauses when Pause Until Tomorrow is ON. | Optional; 00:05 local time by default. |

Keep reconciliation enabled even if you disable Pause Until Tomorrow. Each pause
or restore request opens a **ten-minute verification window for the affected
vacuum**. Timer ticks observe Homebridge approximately once per minute during that
window. Outside it, they inspect local files only: **no Homebridge reads, schedule
writes, or docking requests**. Completed restores no longer cause overnight audits.

Systemd supplies a reliable wake-up after the button command exits, including
across restarts. A separate daemon is unnecessary. The timer remains enabled but
does no network work when idle; an alternative scheduler can call `maintain` at
the same cadence. Do not run both. Existing v1.0.1 timer/service files are unchanged
and do not need reinstalling for v1.0.2.

### Render and review reconciliation units

From the live installation, as its owner:

```bash
cd /var/lib/homebridge/roborockPauseSchedules
reconcile_directory="$(mktemp -d /tmp/roborock-reconcile.XXXXXX)"
python3 render-systemd-units.py --output "$reconcile_directory"
cat "$reconcile_directory/roborock-pause-reconcile.service"
cat "$reconcile_directory/roborock-pause-reconcile.timer"
printf 'Prepared files: %s\n' "$reconcile_directory"
```

The renderer reads `controller/vacuums.json` and produces all four unit files.
Check that the reconciliation service's `User`, `Group`, and `WorkingDirectory`
match the live installation. Its command is
`python3 controller/all-vacuums-pause-controller.py maintain`.

### Install and enable reconciliation

Return to your regular sudo-capable shell. Set `reconcile_directory` to the
exact prepared directory printed above; `/tmp/roborock-reconcile.XXXXXX` below
is a placeholder, not a literal directory to use. Stop if rendering or review
failed. If units are already installed, compare and back them up before replacing
them; installers that track their hashes also need a reviewed manifest update.

```bash
reconcile_directory=/tmp/roborock-reconcile.XXXXXX
sudo systemd-analyze verify \
  "$reconcile_directory/roborock-pause-reconcile.service" \
  "$reconcile_directory/roborock-pause-reconcile.timer" &&
sudo install -o root -g root -m 0644 \
  "$reconcile_directory/roborock-pause-reconcile.service" \
  "$reconcile_directory/roborock-pause-reconcile.timer" \
  /etc/systemd/system/ &&
sudo systemctl daemon-reload &&
sudo systemctl enable --now roborock-pause-reconcile.timer
```

Enable the `.timer`, not the oneshot `.service`. No Homebridge restart is needed.
Enabling it after the machine has been up for more than 60 seconds can trigger
the first run immediately. That run can retry an already pending schedule write
or submit its still-pending docking action; enabling it is not a read-only test.

### Verify and troubleshoot

```bash
systemctl is-enabled roborock-pause-reconcile.timer
systemctl is-active roborock-pause-reconcile.timer
systemctl list-timers --all roborock-pause-reconcile.timer
systemctl show roborock-pause-reconcile.service \
  -p Result -p ExecMainStatus -p ActiveState -p SubState
sudo journalctl -u roborock-pause-reconcile.service --since today --no-pager
```

A successful oneshot service normally returns to `inactive (dead)` between runs;
the **timer** should remain active. `NEXT` can briefly show `-` while the service
is running. If it stays unset, inspect the service result and journal for a stuck
operation, configuration error, or Homebridge connectivity problem. Systemd logs
cover maintenance; immediate Pause All workers also log to
`controller/all-vacuums-pause-operation.log`. Review logs before sharing them.

To run one cycle immediately, use `sudo systemctl start roborock-pause-reconcile.service`.
It may perform pending writes. For a read-only local summary instead, run the
controller's `status` command as the runtime owner.

To suspend checks, run `sudo systemctl stop roborock-pause-reconcile.timer`.
This does not cancel a service run already in progress. To keep checks disabled
across reboots, use `sudo systemctl disable --now roborock-pause-reconcile.timer`.
Neither command restores schedules. Finish pending work before an upgrade and
restart the timer afterward. See [upgrade guidance](docs/UPGRADE.md).

Systemd is the provided scheduler, not part of the reconciliation algorithm.
Another scheduler can invoke `maintain` as the runtime owner from the live root,
but must handle reboot startup and avoid accumulating overlapping invocations.
Do not run two independent reconciliation schedulers.

## Optional automatic expiration timer

The optional systemd timer automatically restores paused schedules shortly
after midnight. By default it runs at **00:05 in the Homebridge host's local
time zone**.

The timer works together with the pause switches as follows:

| Action or switch | Effect |
|---|---|
| Turn an individual vacuum's pause switch ON | Saves that vacuum's current schedule states and pauses its schedules. |
| Turn an individual vacuum's pause switch OFF | Requests restoration of the saved states owned by that pause; reconciliation checks completion. |
| Turn Pause All ON | Pauses every configured vacuum. |
| Turn Pause All OFF | Requests restoration of every active pause, including pauses started with an individual switch. |
| Leave Pause Until Tomorrow ON | Allows the systemd timer to restore all active pauses when the timer runs. |
| Turn Pause Until Tomorrow OFF | The timer still runs, but exits without changing any pause. Pauses remain active until manually restored. |

Pause Until Tomorrow is a persistent preference, not a pause command. It
defaults to ON until you turn it OFF. Turning it ON does not pause a vacuum,
and turning it OFF does not immediately restore one. The preference remains
ON after an automatic restoration, so future pauses will also expire after
midnight unless you turn the switch OFF.

The Pause All state is ON whenever at least one configured vacuum's pause display
is ON, including optimistic requests still settling. When automatic expiration
runs, it performs the same aggregate restore
operation as turning Pause All OFF.

### Render and review the units

Run the renderer from the installed project directory. It reads the installation
root, Homebridge service name, execution user, and timer schedule from
`controller/vacuums.json`.

Use a new temporary output directory because the renderer intentionally refuses
to replace existing output:

```bash
cd /var/lib/homebridge/roborockPauseSchedules
render_directory="$(mktemp -d /tmp/roborock-systemd.XXXXXX)"

python3 render-systemd-units.py --output "$render_directory"

sed -n '1,200p' \
  "$render_directory/roborock-pause-until-tomorrow.service"
sed -n '1,200p' \
  "$render_directory/roborock-pause-until-tomorrow.timer"
```

The renderer only creates files for review. It never invokes `systemctl` or
writes to `/etc/systemd/system`.

The generated service:

- runs once as the configured Homebridge user and group;
- invokes `all-vacuums-pause-expire.sh` from the installed project directory;
- restores all active pauses when Pause Until Tomorrow is ON; and
- exits successfully without changing pauses when Pause Until Tomorrow is OFF.

The generated timer uses `Persistent=true`. If the computer was off or the
timer was inactive at 00:05, systemd may run the missed expiration shortly
after the timer is next started. Before enabling it, restore any pause that
should remain active or turn Pause Until Tomorrow OFF.

### Install and enable the timer

After reviewing both rendered files, install them from your sudo-capable login.
If you changed shells, set `render_directory` to the prepared path first:

```bash
sudo install -o root -g root -m 0644 \
  "$render_directory/roborock-pause-until-tomorrow.service" \
  /etc/systemd/system/roborock-pause-until-tomorrow.service

sudo install -o root -g root -m 0644 \
  "$render_directory/roborock-pause-until-tomorrow.timer" \
  /etc/systemd/system/roborock-pause-until-tomorrow.timer

sudo systemctl daemon-reload
sudo systemctl enable --now roborock-pause-until-tomorrow.timer
```

Enable the `.timer` unit only. The timer starts the oneshot `.service` whenever
expiration is due; the service does not need to remain enabled or running.

### Verify scheduling and operation

```bash
systemctl is-enabled roborock-pause-until-tomorrow.timer
systemctl is-active roborock-pause-until-tomorrow.timer
systemctl list-timers --all roborock-pause-until-tomorrow.timer
systemctl status roborock-pause-until-tomorrow.timer --no-pager
```

`list-timers` shows the next scheduled run using the host's configured time
zone. Use `timedatectl status` if the displayed time is unexpected.

After the service has run, inspect its most recent result with:

```bash
systemctl status roborock-pause-until-tomorrow.service --no-pager
journalctl \
  -u roborock-pause-until-tomorrow.service \
  --since today \
  --no-pager
```

To test expiration immediately, first decide whether active pauses should be
restored. Starting the service manually performs a real expiration check:

```bash
sudo systemctl start roborock-pause-until-tomorrow.service
systemctl status roborock-pause-until-tomorrow.service --no-pager
```

If Pause Until Tomorrow is ON, that test restores all active pauses. If it is
OFF, the service reports that automatic expiration is disabled and changes
nothing.

## Script and file guide

| File or folder | Plain-English purpose |
|---|---|
| `discover-vacuum-config.py` | Finds compatible Homebridge services without changing them. |
| `install-roborock-pause.py` | Safely stages, installs, checks, reconfigures, upgrades, or uninstalls local configuration. Run it with `--help` to see every command. |
| `controller/vacuum-pause-controller.py` | Runs `init`, `state`, `on`, `off`, `maintain`, `reconcile`, or emergency `abandon` for one vacuum ID. |
| `controller/all-vacuums-pause-controller.py` | Coordinates Pause All and provides the local `status` summary and periodic `maintain` command. |
| `all-vacuums-pause-*.sh` | Runs ON, OFF, STATE, or expiration for every configured vacuum. |
| `pause-until-tomorrow-*.sh` | Enables, disables, or reads the automatic-expiration preference. |
| `setup-homebridge-auto-auth.sh` | Securely creates the local Homebridge login and token files. |
| `render-systemd-units.py` | Renders both reconciliation and optional midnight timer/service pairs; it does not install or enable them. |
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

Homebridge can acknowledge a switch write and display the requested value before
Roborock finishes it. Matching switch values are therefore best-effort observations,
not proof of robot acknowledgement. The controller preserves its desired operation
and snapshot and checks again using the existing Homebridge API; no separate
Roborock login or plugin-specific API is required.

Enable `roborock-pause-reconcile.timer` after deploying the runtime. Each user
pause or restore, including a midnight restore, has a fixed ten-minute deadline
persisted before discovery. Pause All creates a window for each affected vacuum;
a single-vacuum request does not start monitoring for other vacuums. Homebridge's
accessories endpoint may itself read accessories belonging to other vacuums.

Settlement requires at least three matching observations spanning two minutes,
and cannot happen before six minutes from the request. This allows time beyond
the plugin's default five-minute schedule cache. Cached values and background
refreshes mean this is still best-effort verification, not guaranteed completion.
Avoid editing schedules manually while an operation is settling. Only mismatched
schedules are retried, at least two minutes apart, with at most three attempts per
schedule per operation. After settlement, remaining checks in the same window
are read-only: possible manual changes are reported, not overwritten.

At the deadline, all background reads and retries stop. An already submitted HTTP
request may finish; the controller starts no further request after expiry. A
settled operation retains its last observed result. An unsettled one becomes
`needs-attention`, with its recovery snapshot preserved. A restart resumes only
the unused part of an existing window; it does not grant another ten minutes.
Stopping and restarting the timer cannot renew a window.

Repeated presses while a window is open preserve its deadline and retry budget.
After an unsuccessful window, explicitly retry the same ON/OFF request to open a
new window. `reconcile` explicitly rechecks the saved requested operation, including
a failed restore. Opposite requests supersede unfinished work; OFF is persisted
before discovery so an outage at midnight cannot continue the old pause retries.
A repeated request for an already completed operation is a no-op.

During settlement the switch displays the requested state. On a known disagreement
requiring attention it reflects whether Homebridge last showed that vacuum's
schedules all OFF; Pause All is ON if any vacuum reports ON. A discovery-only pause
that times out before any writes displays OFF and retains a `needs-attention`
request that can be retried or cancelled with OFF. `pauseActive` separately records
restoration ownership; it is not proof that schedules are disabled.

**After the window closes, the displayed state is the last observation, not a
continuously refreshed status.** Later cloud failures or manual edits are not
detected automatically. Fix connectivity and explicitly retry or restore if needed;
do not delete recovery files. On upgrading from v1.0.1, old journals without a
deadline stop their indefinite audits immediately. Completed results remain saved;
unfinished journals become `needs-attention` and need an explicit retry. Legacy
snapshots without a journal remain restorable and are not automatically polled.

When Homebridge displays the schedules OFF, a cleaning vacuum receives one Return
to Dock request. `return-to-dock-submitted` means Homebridge accepted the press,
not robot acknowledgement. Neither docking arrival nor a repeated docking command
is required for schedule settlement. An ambiguous submitted action is not replayed
after restart. Runtime JSON files are replaced atomically with mode `0600`.

The Script2-facing Pause All ON and OFF wrappers dispatch their aggregate work
to a detached controller process, allowing the HomeKit write callback to return
promptly instead of waiting for every Roborock schedule transaction. The direct
Python `on` and `off` commands wait for the initial attempt but may return
`ACCEPTED` while background settlement remains pending. Exit zero alone does not
mean that schedules have settled.
Background output is written privately to
`controller/all-vacuums-pause-operation.log`; the state command continues to
report the persisted controller view as the operation progresses. To inspect the
per-vacuum phase, desired state, verification window and latest error, run
`python3 controller/all-vacuums-pause-controller.py status` from the installed root.
`maintain` performs one reconciliation cycle; normal periodic use is owned by the
systemd timer, not by a HomeKit state-read callback.

The renderer and installer stage now include both reconciliation unit files in
addition to the midnight units. Review and install the generated
`roborock-pause-reconcile.service` and `roborock-pause-reconcile.timer` in the same
way as the midnight pair, then enable the reconciliation timer. Rendering or
updating the source checkout does not install or enable it. Its working directory
must be the deployed runtime, not a separate source checkout. Stop the reconciliation
timer during runtime upgrades. Settled restore snapshots are retained recovery
records and do not block installer upgrades; unresolved operations still do.

If a normal `off` or `reconcile` fails, do not delete state or snapshot files.
Fix Homebridge/cloud connectivity and retry. The emergency `abandon` command
does not restore schedules; use it only after independently restoring schedules
in the Roborock app:

```bash
python3 controller/vacuum-pause-controller.py VACUUM_ID abandon \
  --confirm I-WILL-RESTORE-SCHEDULES-MANUALLY
```

Before uninstalling, restore schedules and resolve pending operations while the
controller and reconciliation timer are still available. Then disable both timers
from your sudo-capable login and allow any running services to finish:

```bash
sudo systemctl disable --now roborock-pause-reconcile.timer
sudo systemctl disable --now roborock-pause-until-tomorrow.timer
```

Skip the second command if the optional midnight timer was never installed.
Remove the Script2 switches to prevent further requests. As the runtime owner,
review removal of installer-owned configuration while preserving vacuum state files:

```bash
python3 install-roborock-pause.py uninstall --dry-run
python3 install-roborock-pause.py uninstall --yes
```

Manually installed systemd units may not be installer-owned and can remain on disk.
After reviewing the uninstall plan, remove any remaining project unit files from
`/etc/systemd/system` and run `sudo systemctl daemon-reload`. Do not leave an enabled
timer pointing at removed configuration. Keep private recovery backups until you
have confirmed the desired schedules independently.

## More detailed documentation

- [Managed reconfiguration](docs/RECONFIGURE.md)
- [Git-based upgrades](docs/MANAGED_UPGRADE.md)
- [ZIP and separate-runtime upgrades](docs/ZIP_UPGRADE.md)
- [Upgrade safety notes](docs/UPGRADE.md)
- [Older manifest migration](docs/MANIFEST_V2_MIGRATION.md)
- [Release checklist](docs/RELEASE_CHECKLIST.md)
- [Publishing releases](docs/RELEASING.md)
