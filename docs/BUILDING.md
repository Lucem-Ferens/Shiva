# Building Shiva

## Run from source

Requires Windows and Python 3.12 or newer.

```
pip install -e .[dev]                  # or install the dependencies listed in pyproject.toml
python tools/fetch_third_party.py      # download the pinned age and jpegtran binaries and verify their SHA256 (needs 7-Zip)
python tools/compile_translations.py   # compile the Traditional Chinese catalogue
set PYTHONPATH=src && python -m shiva
```

## Build the portable folder

```
python tools/build.py
```

This uses Nuitka (standalone) and takes a few minutes. The build accepts only the package versions pinned in
`requirements/lock.txt` and stops on any difference. Set up that environment with:

```
python -m venv .venv
.venv\Scripts\python -m pip install --require-hashes -r requirements/lock.txt
```

Use the newest Python 3.13 or later release that has a Windows installer for release builds (3.12 releases after 3.12.10
are source-only). The hashes cover every package in the lock; pip installs the compiler's own build tools (setuptools,
wheel) without hash checking. `requirements/lock.in` lists the direct requirements; regenerate the lock with
`tools/make_lock.py` (needs `pip-tools`).

Output (git-ignored): `dist/Shiva-<version>-win64/`, the zip, `SHA256SUMS.txt`, and a CycloneDX list of the Python
packages and age.

## Tests

```
set PYTHONPATH=src && python -m pytest -q    # tests that need the age binaries skip if they are absent
python tools/check_upstream.py               # compare pinned versions with upstream releases
```

Bundled components are pinned with versions and SHA256 in [`third_party/MANIFEST.toml`](../third_party/MANIFEST.toml).
