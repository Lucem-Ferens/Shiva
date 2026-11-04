"""Download pinned third-party binaries into third_party/bin/ and verify SHA256.

Usage: python tools/fetch_third_party.py
Only components with kind = "bundled" and a sha256 in third_party/MANIFEST.toml are fetched.
Refuses to extract when the hash does not match.
"""
from __future__ import annotations

import hashlib
import sys
import tomllib
import urllib.request
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main() -> int:
    manifest = tomllib.loads((ROOT / "third_party" / "MANIFEST.toml").read_text(encoding="utf-8"))
    for name, c in manifest.items():
        if c.get("kind") != "bundled" or "sha256" not in c:
            continue
        url = f"https://github.com/{c['repo']}/releases/download/v{c['version']}/{c['asset']}"
        dest = ROOT / "third_party" / "bin"
        dest.mkdir(parents=True, exist_ok=True)
        zpath = dest / c["asset"]
        print(f"[{name}] downloading {url}")
        urllib.request.urlretrieve(url, zpath)
        digest = hashlib.sha256(zpath.read_bytes()).hexdigest()
        if digest != c["sha256"]:
            zpath.unlink()
            print(f"[{name}] SHA256 MISMATCH: expected {c['sha256']} got {digest}", file=sys.stderr)
            return 1
        with zipfile.ZipFile(zpath) as z:
            z.extractall(dest)
        zpath.unlink()
        print(f"[{name}] verified and extracted to {dest}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
