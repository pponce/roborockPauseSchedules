# Roborock Pause Schedules v1.0.1

This point release adds background reconciliation for Homebridge plugins that
acknowledge schedule-switch writes optimistically. Pause All remains responsive
while the controller keeps a durable record of requested work and checks the
exposed schedule states afterward.

## What changed

- Pending pause and restore operations survive controller restarts. Snapshots
  remain available through incomplete operations and completed restores.
- Matching Homebridge observations must span at least 120 seconds and three
  samples before settlement. Mismatched schedules have a maximum of three write
  attempts per operation, with at least 120 seconds between attempts.
- Completed operations continue to be observed approximately every five minutes.
  Later disagreements update status and the pause display without automatically
  overwriting possible manual edits.
- Return to Dock is submitted once through Homebridge; the controller does not
  wait for dock arrival or claim that Homebridge acceptance proves robot acknowledgement.
- A new reconciliation systemd timer runs background checks independently of
  the optional Pause Until Tomorrow midnight timer.
- README now includes complete timer installation, verification, troubleshooting,
  account-ownership, and uninstall guidance. ZIP and separate-runtime deployments
  have their own upgrade guide.

## Installation and upgrade

Download the attached `roborockPauseSchedules-1.0.1.zip` and
`roborockPauseSchedules-1.0.1.zip.sha256`, then verify the checksum before extraction.
Use this project ZIP rather than GitHub's automatic source archive for a runtime
installation. It includes both timer/service pairs and the updated instructions;
it excludes credentials, registry, manifest, state, snapshots, Git metadata, and
developer tests.

**Existing users must deploy the updated runtime and install/enable
`roborock-pause-reconcile.timer`.** Updating a separate source checkout or merely
extracting the ZIP does not enable background checks. The existing midnight
timer does not replace reconciliation. No Homebridge plugin changes or separate
Roborock login are required.

Follow the README in the archive for a fresh install, `docs/ZIP_UPGRADE.md` for
a ZIP/separate-runtime update, or `docs/MANAGED_UPGRADE.md` for a live Git checkout.
Preserve configuration, snapshots, and Script2 paths. Do not rerun fresh installation
over an existing runtime. New six-artifact installer stages replace older stages;
regenerate a stage from the existing registry if an old stage is rejected.

## Verification and limits

The 132-test deterministic suite passes, including delayed rollback, retry-budget,
restart, manual-edit, and packaging coverage. An isolated test of the extracted
ZIP also passed installation, manifest migration, local verification, and rendering
of both timer/service pairs without API calls. The maintainer reported a
successful deployment, local installer verification, an idle reconciliation run,
and an enabled timer on a two-vacuum Homebridge installation. Recovery from an
actual delayed Homebridge/Roborock failure has not yet been observed in that live
installation.

This remains **best-effort verification through Homebridge**. A cached matching
value or a `complete` phase does not guarantee cloud-authoritative completion.
Exhausted retries retain the snapshot and report attention. A plugin rollback
after settlement cannot reliably be distinguished from a deliberate manual edit,
so passive monitoring reports the disagreement rather than forcing the old target.
