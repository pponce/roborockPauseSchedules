# Managed source upgrades

Use the guarded upgrade commands for tracked controller-source updates after an
installation has a valid managed manifest. Runtime registry, manifest, state,
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

The checkout must have no tracked changes, the target must exist locally and be
a descendant of `HEAD`, every configured vacuum must be inactive, no active
primary snapshot may exist, and local installation verification must pass.
Planning and dry-run do not change tracked source. Fetch updates Git metadata
only and is deliberately separate so authentication remains under the operator's
control.

## Apply and ownership

The account running apply must be able to write both the checkout and `.git`.
On split-ownership deployments, temporarily assign the checkout to the Git
operator, then restore runtime ownership immediately afterward:

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
changes separately. When cloud state is authoritative, `verify --live` remains
an optional separate read-only operational check.
