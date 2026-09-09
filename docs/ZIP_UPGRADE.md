# Upgrading a ZIP or separate live installation

Use this guide when the running directory has no `.git`, even if you also keep a
development clone such as `roborockPauseSchedulesSource`. A managed version-2
manifest tracks installation configuration; it does not make program files
Git-managed. Updating the development clone alone has no effect on Script2.

Do not run the fresh `install --yes` command against an existing installation.
It intentionally refuses to overwrite its configuration. Preserve the live root
and the Script2 command paths.

## 1. Download and inspect the new release

Download the attached `roborockPauseSchedules-1.0.2.zip` and matching
`.zip.sha256` from [Releases](https://github.com/pponce/roborockPauseSchedules/releases).
From the download directory, verify and extract into a separate, empty staging
directory, not over the running controller:

```bash
sha256sum --check roborockPauseSchedules-1.0.2.zip.sha256 &&
unzip -l roborockPauseSchedules-1.0.2.zip &&
unzip roborockPauseSchedules-1.0.2.zip -d ./roborock-upgrade-review
```

Review the release notes and compare program files with your installation.
The project ZIP contains programs, documentation, examples, and default systemd
templates. It contains no `.git` directory, developer tests, installed registry,
manifest, credentials, tokens, state, or recovery snapshots. Do not assume the
same contents for an arbitrary third-party archive. Preserve any local program
customizations separately and reconcile them before copying newer files.

## 2. Finish operations and make a private backup

Follow [upgrade preparation](UPGRADE.md#1-establish-an-inactive-recoverable-starting-point).
Let pending operations finish before stopping timers. With v1.0.1 and later, use
the local `status` command to check phases; v1.0.0 does not have that command.
Do not treat the optimistic Pause All OFF display as proof of restored schedules.
If any operation is unresolved, recover it before upgrading.

Stop the reconciliation timer if installed and the midnight timer if in use.
Wait for any running services and detached workers to finish, and do not operate
the pause switches during copying. An administrator may also temporarily stop
Homebridge to prevent new Script2 requests; record whether it was running and
restart it after validation if you do so.

Create a private backup outside the live root. These commands run from your
sudo-capable login and preserve ownership and permissions:

```bash
backup_directory="$(sudo mktemp -d /var/lib/homebridge/roborock-pause-backup.XXXXXX)" &&
sudo cp -a /var/lib/homebridge/roborockPauseSchedules \
  "$backup_directory/runtime" &&
printf 'Private backup: %s\n' "$backup_directory"
```

Also back up installed project unit files from `/etc/systemd/system` and record
which timers were enabled and active. The runtime backup contains credentials;
keep its parent directory private. Record the version and hashes of the old
programs, registry, manifest, and recovery files.

## 3. Deploy only reviewed package contents

Run the copy as the runtime owner, normally `homebridge`. The extracted staging
directory must be readable by that account. Replace the example source path
below with your actual reviewed directory:

```bash
sudo -H -u homebridge cp -a \
  /absolute/path/roborock-upgrade-review/roborockPauseSchedules-1.0.2/. \
  /var/lib/homebridge/roborockPauseSchedules/
```

This overlays the public package files while leaving private runtime files that
are absent from the archive in place. Do not delete the existing root, use
`rsync --delete`, replace `controller/vacuums.json` with the example, or copy an
entire development checkout containing runtime artifacts. Do not change the
existing registry, manifest, state, or snapshot schemas by hand.

If deploying from a separate Git checkout, export the release commit's public
package with `scripts/build-release-zip.py` and use the same reviewed ZIP process.
The ZIP builder belongs to the source checkout; it is not included in the runtime ZIP.

## 4. Verify before restarting background work

As the runtime owner, from the live root:

```bash
cd /var/lib/homebridge/roborockPauseSchedules
python3 -m py_compile controller/*.py *.py
for file in ./*.sh; do bash -n "$file"; done
python3 install-roborock-pause.py verify
python3 controller/all-vacuums-pause-controller.py status
bash all-vacuums-pause-state.sh
bash pause-until-tomorrow-state.sh
```

All commands must succeed before proceeding. The two state wrappers should each
print exactly `true` or `false`; the preference switch can legitimately be ON
while no schedules are paused. Compare protected file hashes and modes with the
backup. The runtime ZIP excludes tests, so run the full deterministic suite in
the source checkout when producing the release, not in the extracted runtime.

Run `verify --live` separately if you also want to check exposed Homebridge
accessories. This does not prove robot acknowledgement or schedule completion.

## 5. Install and start the timer

Copying the ZIP's `systemd/` templates does not install them into systemd.
Render fresh units using the live registry and follow the
[reconciliation setup](../README.md#background-reconciliation-timer). For an
upgrade from v1.0.0, this new timer is required for the documented background
settlement and retry behavior. Existing configuration does not need to be
reinstalled merely to add it.

After successful verification, enable the reconciliation timer, restore the
midnight timer if previously in use, and restart Homebridge if you stopped it.
The midnight timer can immediately run a missed expiration when restarted.
Re-enabling reconciliation resumes only a request still inside its original
ten-minute window. Expired requests do not restart automatically. v1.0.1 journals
without deadlines stop their old indefinite audits; incomplete journals need an
explicit retry. No snapshot is discarded. The v1.0.1 systemd unit contents are
unchanged, so an existing verified timer needs no replacement.

## If validation fails

Keep timers stopped and avoid switch operations. If no new live operation has
started, restore only the changed program files from the recorded backup and
repeat local verification. Preserve current state, credentials, and recovery
files. Once a new operation has started, an older controller may not understand
its new journal: do not blindly restore the whole old directory or discard its
snapshots. Resolve that operation or prepare a recovery plan first.
