# Guarded upgrade procedure

Live Git checkouts with a version-2 manifest use the installer's `upgrade-plan`
and `upgrade-apply` commands. ZIP installations and separate source/runtime
directories use [the ZIP deployment guide](ZIP_UPGRADE.md). `install` is only for establishing a new installation and
must not be used for source updates. See
[Managed source upgrades](MANAGED_UPGRADE.md) for the canonical workflow.

## 1. Establish an inactive, recoverable starting point

Before changing tracked source files:

1. Confirm every per-vacuum Pause switch and Pause All report `false`, then
   inspect `python3 controller/all-vacuums-pause-controller.py status` if the
   installed version supports it. An optimistic OFF value alone is insufficient:
   no operation may still be `planning`, `settling`, or `needs-attention`.
2. Do not upgrade during an incomplete pause, restore, reconcile, or manual
   recovery.
3. Run `python3 install-roborock-pause.py verify --live`. This is read-only for
   accessory state, although shared authentication may refresh a local token.
4. Record the current commit with `git rev-parse HEAD` for a live Git checkout,
   or the installed release version and program-file hashes for a ZIP runtime.
5. Back up deployment-owned files outside the checkout: the external registry,
   installer manifest, state files, active/backup/manual-recovery snapshots,
   token and credential files, and any locally installed systemd units.
6. Record file ownership, modes, and SHA-256 hashes. Treat all backup output as
   private.

Homebridge values are best-effort observations, not authoritative proof of robot
completion. If recovery remains incomplete or Homebridge is unavailable, stop
after local state and artifact checks. Do not force writes merely to upgrade.

After pending work finishes, record which timers are active, then stop the
reconciliation timer and the optional midnight timer (skip units never installed):

```bash
sudo systemctl stop roborock-pause-reconcile.timer
sudo systemctl stop roborock-pause-until-tomorrow.timer
systemctl show roborock-pause-reconcile.service -p ActiveState -p SubState
systemctl show roborock-pause-until-tomorrow.service -p ActiveState -p SubState
```

Stopping a timer does not stop a running service. Wait for services and detached
Pause All workers to finish; avoid operating Script2 switches during deployment.
Keep these timers stopped until validation succeeds. This does not unpause a robot.

## 2. Fetch and review tracked source

The production checkout must be clean. Fetch as the Git owner, then use the
installer to review the already-fetched target:

```bash
git status --short --branch
git fetch origin
python3 install-roborock-pause.py upgrade-plan --target origin/master
python3 install-roborock-pause.py upgrade-apply \
  --target origin/master --dry-run
python3 install-roborock-pause.py upgrade-apply \
  --target origin/master --yes
```

Run these commands as the account owning the checkout and `.git`; a Git author
identity is needed for commits, not ordinary downloads. Public HTTPS fetches do
not require the service account to have your personal SSH key. Never commit or
remove ignored runtime files merely to obtain a clean Git status.

`upgrade-apply` accepts only a fast-forward and automatically runs the source
checks below. If a check fails, it restores the recorded starting commit.

## 3. Validation before operating switches

```bash
python3 -m py_compile controller/*.py *.py
python3 -m unittest discover -s tests -v
for file in ./*.sh; do bash -n "$file"; done
python3 install-roborock-pause.py verify
```

Then confirm individual Pause, Pause All, and Pause Until Tomorrow state
wrappers still produce exactly `true` or `false`. Compare runtime artifact
hashes and modes with the pre-upgrade record.

Run `verify --live` separately when Homebridge is available. It checks exposed
accessories, not whether Roborock has finished a prior command.

## 4. Review systemd separately

Render units into a new temporary directory and compare them with installed
units. Rendering never installs units or invokes `systemctl`:

```bash
python3 render-systemd-units.py --output /tmp/roborock-systemd-review
diff -u /etc/systemd/system/roborock-pause-until-tomorrow.service \
  /tmp/roborock-systemd-review/roborock-pause-until-tomorrow.service
diff -u /etc/systemd/system/roborock-pause-until-tomorrow.timer \
  /tmp/roborock-systemd-review/roborock-pause-until-tomorrow.timer
diff -u /etc/systemd/system/roborock-pause-reconcile.service \
  /tmp/roborock-systemd-review/roborock-pause-reconcile.service
diff -u /etc/systemd/system/roborock-pause-reconcile.timer \
  /tmp/roborock-systemd-review/roborock-pause-reconcile.timer
```

Install changed units and run `daemon-reload` only after explicit review. Do not
restart Homebridge or trigger an expiration merely because source was updated.
If upgrading from v1.0.0, the reconciliation units will be absent; install them
using the [README timer instructions](../README.md#background-reconciliation-timer).
Once validation succeeds, enable/start reconciliation and restart the midnight
timer only if it was previously in use. Its persistent schedule may immediately
run a missed expiration; respect the Pause Until Tomorrow preference.

## 5. Rollback boundary

A source rollback must restore only the previously recorded tracked commit.
Preserve current runtime state and recovery artifacts. Re-run deterministic and
installation verification after rollback. If the upgrade changed a persisted
schema or a live operation began, stop and perform incident-specific recovery
instead of blindly resetting files.
