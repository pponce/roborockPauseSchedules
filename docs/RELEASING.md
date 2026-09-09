# Publishing a release ZIP

This page is for the project maintainer. Users do not need Git to install a
release ZIP.

## Build and inspect

Start from a clean, tested commit. Use a three-part version without a leading
`v`:

```bash
python3 scripts/build-release-zip.py --version 1.0.1
unzip -l dist/roborockPauseSchedules-1.0.1.zip
(cd dist && sha256sum --check roborockPauseSchedules-1.0.1.zip.sha256)
```

The builder includes runtime programs, examples, systemd files, and user
documentation. It excludes Git metadata, developer tests, historical tools,
generated state, credentials, the installed registry, and the manifest.
Check that all four systemd unit files are present, including the reconciliation
pair, and that the ZIP README describes enabling that timer. ZIP installs must
not be directed to the Git-only upgrade command. Extraction with `unzip` preserves
the packaged executable permissions; confirm the shell entry points remain executable.

## Publish on GitHub

1. Push the tested commit to the main branch.
2. In GitHub, choose **Releases**, then **Draft a new release**.
3. Create a tag such as `v1.0.1` from that exact commit.
4. Give the release a plain-language title and describe important changes.
5. Attach the ZIP and `.zip.sha256` files created in `dist/`.
6. Publish the release.
7. Download the attachments once and repeat the checksum and archive-list
   checks before announcing the release.

GitHub also creates automatic source-code ZIP and tar archives for a release.
The attached project ZIP is preferable for non-programmers because it omits
developer-only files and has a separately published checksum.

An authenticated GitHub CLI can create a draft with both attachments:

```bash
release_commit="$(git rev-parse HEAD)"
gh release create v1.0.1 --repo pponce/roborockPauseSchedules \
  --target "$release_commit" --draft \
  --title "Roborock Pause Schedules v1.0.1" \
  --notes-file docs/RELEASE_1.0.1.md \
  dist/roborockPauseSchedules-1.0.1.zip \
  dist/roborockPauseSchedules-1.0.1.zip.sha256
```

Confirm the target commit and attachments before publishing the draft. Git SSH
access and GitHub CLI authentication are separate: run `gh` as your publishing
account, not automatically as the Linux `homebridge` service account. Never put
an access token in a script, command transcript, or release ZIP.

For v1.0.1 use the checked-in [release notes](RELEASE_1.0.1.md). They describe the
required timer installation and distinguish passing deterministic tests and a
successful idle service run from live delayed-failure recovery validation.
