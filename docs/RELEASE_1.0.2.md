# Roborock Pause Schedules v1.0.2

This point release stops continuous schedule reads after pause and restore work.
v1.0.1 kept auditing completed operations indefinitely, which could refresh the
Homebridge plugin's schedule cache throughout the night.

## Changes

- Each pause or restore request gets a fixed ten-minute verification window.
  Pause All covers affected vacuums; individual requests open only their own window.
- The existing one-minute timer checks local files when idle. Outside a window,
  it makes no Homebridge requests, schedule writes, or docking requests.
- Matching observations cannot settle before six minutes, allowing time beyond
  the plugin's default five-minute cache. Three matching observations spanning at
  least two minutes are still required. This remains best-effort verification.
- Mismatched schedules retain the three-attempt budget and two-minute retry
  spacing. Once settled, further checks within the window are read-only.
- Expiry preserves snapshots and marks unfinished work `needs-attention`.
  Restarting the controller or timer cannot extend the deadline. An explicit
  retry can open a new window, preserving the original restore targets.
- Initial discovery and interrupted restore planning share the same deadline.
  Existing v1.0.1 journals stop indefinite audits without sending vacuum commands;
  unfinished old requests need an explicit retry.
- Responsive Pause All switches and single docking submission remain unchanged.
  The Homebridge plugin requires no changes or special API.

## Installation and upgrade

Download both `roborockPauseSchedules-1.0.2.zip` and its `.zip.sha256` attachment.
Verify the checksum before extraction. Use the [README](../README.md) for a fresh
installation or [ZIP upgrade guide](ZIP_UPGRADE.md) for a separate live directory.
Updating a development clone alone does not update Script2's runtime.

The systemd units are unchanged from v1.0.1. Keep the verified reconciliation timer
enabled after deploying the controller; no replacement or daemon reload is needed.
The optional midnight timer remains independent. v1.0.0 installations still need
the reconciliation timer installed using the README instructions.

## Verification limits

The deterministic suite covers deadlines, late rollback after the cache interval,
initial discovery outages, restore planning, explicit retries, restart, old journal
migration, and requests crossing a deadline. ZIP packaging and checksums are also
validated. These tests do not establish robot-side confirmation or a live household
soak test for this release.

After ten minutes, status reflects the last observation. Later cloud rollbacks or
manual edits are not detected automatically. Recovery files remain available;
restore or explicitly retry if the displayed result disagrees with the robot.
