# Version numbers

Shiva uses `X.Y.Z`. Releases up to 0.0.9 changed only the last digit, so those numbers say nothing; the scheme below starts with
0.1.0. The number tells a user whether files, settings or safety guarantees may behave differently from the last release.

## What each digit means

- **X (major)** changes when something stops working with what you already have: encrypted files that stock `age` or an older Shiva
  cannot decrypt, saved settings that change meaning, a different output naming scheme, or a weaker safety guarantee (shredding, the
  trial decrypt, never overwriting a file, age containment, or cleaning that removes less than before).
- **Y (minor)** changes when a user will notice something that breaks nothing: a new format, option, page or pipeline step, a changed
  default, cleaning that removes more, or a change in what the results report.
- **Z (patch)** changes for fixes (data-loss and security fixes are named as such in the release notes), wording, layout and visual-only
  changes, speed, and dependency updates that change no behaviour. Changes to documentation, tests, CI or the build alone get no release.
- While X is 0 (initial development, Semantic Versioning rule 4), a break and a Y-level change both raise Y; Z stays for fixes. When a
  higher digit is raised, the lower ones go back to 0.

## Pre-releases

Before a release that raises Y or X, a release candidate is published first: tag `v0.2.0-rc.1`, spelled `0.2.0rc1` in `pyproject.toml`.
The final release follows with no code change beyond the version. A Z release, and any release that fixes a data-loss or security
defect, may skip the candidate. The first time a candidate is built, check that `tools/build.py` and the zip name handle the suffix.

## Where the number is set

`pyproject.toml`, `src/shiva/__init__.py` and the version in the README warning line and badge. The Git tag points at the commit the
release was built from, and the release notes name that commit.

## Version 1.0.0

Candidate conditions, to be revised when it is near: the list of what counts as a break (above) is stable; no open data-loss or leak
defect, and the safety paths are reviewed; a candidate, or two releases in a row, without a break; bundled components are reviewed; the
early-version warnings are removed; the upgrade path from pre-release files and settings is documented.
