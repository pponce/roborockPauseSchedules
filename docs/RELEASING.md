# Publishing a release ZIP

This page is for the project maintainer. Users do not need Git to install a
release ZIP.

## Build and inspect

Start from a clean, tested commit. Use a three-part version without a leading
`v`:

```bash
python3 scripts/build-release-zip.py --version 1.0.0
unzip -l dist/roborockPauseSchedules-1.0.0.zip
(cd dist && sha256sum --check roborockPauseSchedules-1.0.0.zip.sha256)
```

The builder includes runtime programs, examples, systemd files, and user
documentation. It excludes Git metadata, developer tests, historical tools,
generated state, credentials, the installed registry, and the manifest.

## Publish on GitHub

1. Push the tested commit to the main branch.
2. In GitHub, choose **Releases**, then **Draft a new release**.
3. Create a tag such as `v1.0.0` from that exact commit.
4. Give the release a plain-language title and describe important changes.
5. Attach the ZIP and `.zip.sha256` files created in `dist/`.
6. Publish the release.
7. Download the attachments once and repeat the checksum and archive-list
   checks before announcing the release.

GitHub also creates automatic source-code ZIP and tar archives for a release.
The attached project ZIP is preferable for non-programmers because it omits
developer-only files and has a separately published checksum.
