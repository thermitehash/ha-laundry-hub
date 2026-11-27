# Release notes

One file per version: `docs/releases/v<version>.md`, where `<version>` is
`version` in `custom_components/laundry_hub/manifest.json`. CI fails if the file
for the current version is missing. When Validate passes on `main`, the Release
workflow uses line 1 as the GitHub release title and the rest as the notes.

## Format

- Line 1: `# v<version>: <short title>` (for example `# v0.1.1: Drying clothes stay drying`).
- Then plain-language bullets for people using Laundry Hub, not for developers.
  Start each change with **New:**, **Change:** or **Fix:**.
- Mention anything people must do after updating.
- End with "Not yet tested on real hardware." until the hardware checklist is done.

## Reflashing the tap stations

The firmware is not released separately; its version (`firmware_version` in
`esphome/tap-station/packages/tap-station.yaml`) follows the integration's.
Only tell people to **reflash the stations** if files under `esphome/` changed
since the previous release tag. A change to the version number alone
(`firmware_version`, or `project.version` reading it) doesn't count, and
neither do comments or blank lines.

Check before writing the notes (replace `v0.1.1` with the previous tag):

```bash
git diff v0.1.1..HEAD -- esphome/ \
  | grep -E '^[+-]' | grep -vE '^(\+\+\+|---)' \
  | grep -vE 'firmware_version|^[+-]\s*version: |^[+-]\s*(#|//|$)'
```

- **No output:** the firmware didn't change. Don't mention reflashing (at most,
  say there's no need to reflash).
- **Any output:** add a bullet such as "Reflash the tap stations from the ESPHome
  Device Builder to get this change."

If the previous version was never tagged, compare against the commit that
merged it instead.

Never change the version in a docs-only commit after a release: the tag and
the manifest would disagree.
