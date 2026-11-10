"""Download pinned third-party binaries into third_party/bin/ and verify SHA256.

Usage: python tools/fetch_third_party.py
Only components with kind = "bundled" and a sha256 in third_party/MANIFEST.toml are fetched.
Refuses to extract when the hash does not match.

A component with extract = "7z" is an installer that is unpacked with 7-Zip and never run. Only the files listed in
`files` are taken out, each checked against its own pinned sha256 before it is copied to third_party/bin/<dest>/.
"""
from __future__ import annotations

import hashlib
import os
import shutil
import subprocess
import sys
import tempfile
import tomllib
import urllib.request
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _find_7z() -> str | None:
    found = shutil.which("7z")
    if found:
        return found
    for base in (os.environ.get("ProgramFiles"), os.environ.get("ProgramFiles(x86)")):
        if base and (Path(base) / "7-Zip" / "7z.exe").is_file():
            return str(Path(base) / "7-Zip" / "7z.exe")
    return None


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _unpack_installer(name: str, c: dict, installer: Path, dest: Path) -> int:
    seven = _find_7z()
    if seven is None:
        print(f"[{name}] 7-Zip was not found (PATH or Program Files); it is needed to unpack the installer", file=sys.stderr)
        return 1
    out = dest / c["dest"]
    with tempfile.TemporaryDirectory() as tmp:
        subprocess.run([seven, "x", "-y", f"-o{tmp}", str(installer), *c["files"]], check=True, capture_output=True)
        for rel, want in c["files"].items():
            src = Path(tmp) / rel
            if not src.is_file() or _sha256(src) != want:
                print(f"[{name}] {rel} is missing or does not match its pinned sha256", file=sys.stderr)
                return 1
        if out.exists():
            shutil.rmtree(out)
        out.mkdir(parents=True)
        for rel in c["files"]:
            shutil.copy2(Path(tmp) / rel, out / Path(rel).name)
    return 0


def main() -> int:
    manifest = tomllib.loads((ROOT / "third_party" / "MANIFEST.toml").read_text(encoding="utf-8"))
    for name, c in manifest.items():
        if c.get("kind") != "bundled" or "sha256" not in c:
            continue
        tag = c.get("tag", f"v{c['version']}")
        url = f"https://github.com/{c['repo']}/releases/download/{tag}/{c['asset']}"
        dest = ROOT / "third_party" / "bin"
        dest.mkdir(parents=True, exist_ok=True)
        zpath = dest / c["asset"]
        print(f"[{name}] downloading {url}")
        urllib.request.urlretrieve(url, zpath)
        digest = _sha256(zpath)
        if digest != c["sha256"]:
            zpath.unlink()
            print(f"[{name}] SHA256 MISMATCH: expected {c['sha256']} got {digest}", file=sys.stderr)
            return 1
        if c.get("extract") == "7z":
            try:
                rc = _unpack_installer(name, c, zpath, dest)
            finally:
                zpath.unlink()
            if rc:
                return rc
        else:
            with zipfile.ZipFile(zpath) as z:
                z.extractall(dest)
            zpath.unlink()
        print(f"[{name}] verified and extracted to {dest}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
