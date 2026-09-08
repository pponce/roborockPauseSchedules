# Guarded upgrade procedure

Managed installations use the installer's `upgrade-plan` and `upgrade-apply`
commands. `install` is only for establishing a new installation and
must not be used for source updates. See
[Managed source upgrades](MANAGED_UPGRADE.md) for the canonical workflow.

## 1. Establish an inactive, recoverable starting point

Before changing tracked source files:

1. Confirm every per-vacuum Pause switch and Pause All report `false`.
2. Do not upgrade during an incomplete pause, restore, reconcile, or manual
   recovery.
3. Run `python3 install-roborock-pause.py verify --live`. This is read-only for
   accessory state, although shared authentication may refresh a local token.
4. Record the current commit with `git rev-parse HEAD`.
5. Back up deployment-owned files outside the checkout: the external registry,
   installer manifest, state files, active/backup/manual-recovery snapshots,
   token and credential files, and any locally installed systemd units.
6. Record file ownership, modes, and SHA-256 hashes. Treat all backup output as
   private.

If Homebridge cannot provide authoritative live state, stop after local state
and artifact checks. Do not use an upgrade as a reason to force a pause write.

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

When elevated Git access is required, restore the runtime tree to the service
user and Git metadata to the operator immediately afterward. Never commit or
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

Run `verify --live` separately only when Homebridge cloud state is authoritative.

## 4. Review systemd separately

Render units into a new temporary directory and compare them with installed
units. Rendering never installs units or invokes `systemctl`:

```bash
python3 render-systemd-units.py --output /tmp/roborock-systemd-review
diff -u /etc/systemd/system/roborock-pause-until-tomorrow.service \
  /tmp/roborock-systemd-review/roborock-pause-until-tomorrow.service
diff -u /etc/systemd/system/roborock-pause-until-tomorrow.timer \
  /tmp/roborock-systemd-review/roborock-pause-until-tomorrow.timer
```

Install changed units and run `daemon-reload` only after explicit review. Do not
restart Homebridge or trigger an expiration merely because source was updated.

## 5. Rollback boundary

A source rollback must restore only the previously recorded tracked commit.
Preserve current runtime state and recovery artifacts. Re-run deterministic and
installation verification after rollback. If the upgrade changed a persisted
schema or a live operation began, stop and perform incident-specific recovery
instead of blindly resetting files.
