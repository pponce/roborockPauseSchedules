# Release checklist

## Source and tests

- [ ] Working tree is clean and the release commit is identified.
- [ ] `python3 -m py_compile controller/*.py *.py` passes.
- [ ] `python3 -m unittest discover -s tests -v` passes.
- [ ] `bash -n` passes for all root shell scripts.
- [ ] `git diff --check` passes against the release range.
- [ ] No runtime state, snapshot, registry, manifest, credential, token, or
      recovery artifact is tracked.

## Safety contracts

- [ ] State commands print exactly `true` or `false` on success.
- [ ] Active snapshots are persisted before schedule writes and retained after
      failures.
- [ ] Restore and reconcile paths continue to fail closed on ambiguous state.
- [ ] Manual recovery requires its exact confirmation phrase, performs no API
      request, and retains a mode-`0600` recovery snapshot.
- [ ] Automated tests and packaging contain no historical live-write tools.
- [ ] Installer staging hashes, refusal semantics, rollback, managed reconfiguration,
      ownership, and uninstall preservation remain covered by tests.

## Documentation and packaging

- [ ] `README.md`, `docs/UPGRADE.md`, `docs/RELEASING.md`, and this checklist
      agree with the CLI.
- [ ] The release ZIP and its SHA-256 checksum build and verify successfully.
- [ ] `config/vacuums.example.json` contains no deployment secrets.
- [ ] Rendered systemd units match committed packaged units.
- [ ] Both timer/service pairs are in the ZIP; README explains enabling
      reconciliation separately from optional midnight expiration.
- [ ] ZIP upgrade instructions preserve private runtime files and do not
      require Git metadata or developer tests in the runtime archive.
- [ ] Installer and renderer documentation states that neither invokes
      `systemctl` or edits Homebridge configuration.
- [ ] Release notes distinguish deterministic tests, read-only live checks,
      operator observations, and actual live-write validation.

## Deployment gate

- [ ] All configured pauses are inactive and no controller process is running.
- [ ] The external registry, manifest, runtime state, credentials, and recovery
      artifacts are backed up with modes and hashes.
- [ ] The current production commit and rollback commit are recorded.
- [ ] A live Git upgrade is reviewed as a fast-forward; a ZIP/separate-runtime
      deployment has a reviewed file set and private rollback backup.
- [ ] Timers are stopped after pending work finishes; no service or detached
      controller worker is active during file deployment.
- [ ] Post-upgrade local verification passes; deterministic tests pass in the
      source checkout. Any `verify --live` check is reported as Homebridge
      observation, not proof of robot command completion.
- [ ] State wrappers, systemd timer status, ownership, modes, and protected
      artifact hashes are unchanged unless the release explicitly requires a
      reviewed migration.

## Live validation

- [ ] No live write is performed merely to complete a release checklist.
- [ ] Any planned live lifecycle has an exact baseline, protected logs,
      recovery procedure, explicit operator approval, and authoritative cloud
      state.
- [ ] Known cloud throttling, stale Homebridge state, or incomplete recovery
      blocks live Pause All testing without blocking a source-only release.

- [ ] Pause and restore stop all background API work at their persisted deadline.
- [ ] Restart, initial-discovery failure, and restore planning cannot renew the window.
- [ ] Legacy completed operations stop indefinite auditing without network calls.
- [ ] Expired operations retain recovery data and can be retried explicitly.
