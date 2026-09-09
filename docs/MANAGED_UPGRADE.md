# Managed source upgrades

Use the guarded upgrade commands only when the **live installation root itself**
is a Git checkout and has a valid version-2 managed manifest. A version-2 manifest
does not make a ZIP installation a Git checkout. A separate source clone is not
the live deployment; use [ZIP and separate-runtime upgrades](ZIP_UPGRADE.md) for
those layouts. Runtime registry, manifest, state,
snapshot, credential, token, and recovery files remain outside Git changes.

## Fetch and review

Fetch as the account that owns `.git`, then plan against the fetched target:

```bash
git fetch origin
python3 install-roborock-pause.py upgrade-plan --target origin/master
python3 install-roborock-pause.py upgrade-apply \
  --target origin/master \
  --dry-run
```

First finish pending work, then stop the reconciliation and optional midnight
timers as described in [upgrade safety notes](UPGRADE.md). The checkout must have
no tracked changes, the target must exist locally and be a descendant of `HEAD`,
every configured vacuum must be inactive, no unresolved primary snapshot may
exist, and local installation verification must pass. A completed restore's
retained snapshot can be accepted by the updated installer; do not delete it to
satisfy an older installer's check.
Planning and dry-run do not change tracked source. Fetch updates Git metadata
only and is deliberately separate so authentication remains under the operator's
control.

## Apply and ownership

The account running apply must be able to write both the checkout and `.git`.
Run Git and the installer as the checkout owner, normally `homebridge`; use your
regular sudo-capable login only for systemd administration. For example, prefix
commands with `sudo -H -u homebridge` when that account owns the live checkout.
Do not routinely change ownership or configure a global Git trust exception:

```bash
python3 install-roborock-pause.py upgrade-apply \
  --target origin/master \
  --yes
```

Apply acquires the all-vacuum and per-vacuum locks, recreates the plan under
lock, and runs only `git merge --ff-only`. It then byte-compiles Python, runs
the deterministic unit suite, and performs local installer verification. If
any validation fails, it resets tracked source to the recorded starting commit.
Ignored runtime files are not reset or removed.

The command never performs Homebridge or Roborock discovery or writes, never
changes systemd units, and never invokes `systemctl`. Review and install systemd
changes separately, including the reconciliation pair when upgrading from v1.0.0.
Restart the timers after successful validation. `verify --live` is a separate
Homebridge-read check, not proof of Roborock command completion.
