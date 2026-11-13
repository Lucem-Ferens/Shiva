"""Build the portable Shiva folder with Nuitka (standalone), then verify and zip it.

Usage (from the repository root, after `python tools/fetch_third_party.py`):
    python tools/build.py [--skip-compile]    # --skip-compile reuses build/ (packaging step only)

Output (git-ignored): dist/Shiva-<version>-win64/ , dist/Shiva-<version>-win64.zip, dist/SHA256SUMS.txt

Steps: check the environment against requirements/lock.txt (stops on any difference) -> compile -> check every bundled file is really there (Nuitka's --include-data-dir silently skips
.exe files, so each one is listed explicitly) -> add licence texts -> run `Shiva.exe --selftest` with a
stripped PATH -> zip -> SHA-256 -> software bill of materials. Fails loudly on any problem.

Set up the environment the build accepts:
    python -m venv .venv && .venv/Scripts/python -m pip install --require-hashes -r requirements/lock.txt
"""
from __future__ import annotations

import hashlib
import importlib.metadata as md
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import tomllib
import zipfile
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
AGE_DIR = ROOT / "third_party" / "bin" / "age"
LOCK = ROOT / "requirements" / "lock.txt"
MIN_PYTHON = (3, 13, 0)         # 3.12 gets source-only security releases; the last Windows installer is 3.12.10
LICENSE_FILES = [  # (source, name inside the portable folder)
    (ROOT / "LICENSE", "LICENSE.txt"),
    (ROOT / "THIRD_PARTY_NOTICES.md", "THIRD_PARTY_NOTICES.md"),
    (ROOT / "third_party" / "licenses" / "LGPL-3.0.txt", "licenses/LGPL-3.0.txt"),
]


def pdfium_files() -> list[tuple[Path, str]]:
    """pypdfium2 (PDF flattening) loads pdfium.dll and reads version.json from its package folders at run time.
    Nuitka copies them itself (explicit --include-data-files conflicts with its DLL handling); asserted after the build."""
    import pypdfium2
    import pypdfium2_raw
    raw, pkg = Path(pypdfium2_raw.__file__).parent, Path(pypdfium2.__file__).parent
    files = [(raw / "pdfium.dll", "pypdfium2_raw/pdfium.dll"), (raw / "version.json", "pypdfium2_raw/version.json"),
             (pkg / "version.json", "pypdfium2/version.json")]
    for src, _rel in files:
        if not src.is_file():
            die(f"pypdfium2 file missing: {src.name}")
    return files


def die(msg: str) -> None:
    print(f"BUILD FAILED: {msg}", file=sys.stderr)
    raise SystemExit(1)


def source_commit() -> str:
    """Short id of the commit being built, marked when the working tree has uncommitted changes (public: no names)."""
    try:
        head = subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=ROOT, capture_output=True, text=True, check=True).stdout.strip()
        dirty = subprocess.run(["git", "status", "--porcelain", "--untracked-files=no"], cwd=ROOT, capture_output=True,
                               text=True, check=True).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return "unknown"
    return head + (" + uncommitted changes" if dirty else "")


def dependency_versions(roots: list[str]) -> dict[str, str]:
    """Installed version of every runtime package the app needs: the direct dependencies and what they pull in."""
    import re
    seen: dict[str, str] = {}
    todo = [re.split(r"[<>=!~\[; (]", r.strip())[0] for r in roots]
    while todo:
        name = todo.pop()
        key = name.lower().replace("_", "-")
        if key in seen:
            continue
        try:
            seen[key] = md.version(name)
        except md.PackageNotFoundError:
            continue        # a requirement for another platform
        for req in md.requires(name) or []:
            if "extra ==" not in req:
                todo.append(re.split(r"[<>=!~\[; (]", req.strip())[0])
    return seen


def locked_versions() -> dict[str, str]:
    """name -> version of every package pinned in requirements/lock.txt (names normalised like pip does)."""
    if not LOCK.is_file():
        die("requirements/lock.txt missing")
    pins = re.findall(r"^([A-Za-z0-9][A-Za-z0-9._-]*)==([^\s\\]+)", LOCK.read_text(encoding="utf-8"), re.M)
    return {re.sub(r"[-_.]+", "-", n).lower(): v for n, v in pins}


def check_environment(project_dependencies: list[str]) -> None:
    """Stop when the running environment differs from the lock: a missing, differently versioned, extra or duplicate package."""
    lock = locked_versions()
    problems = []
    for name, want in sorted(lock.items()):
        try:
            have = md.version(name)
        except md.PackageNotFoundError:
            problems.append(f"{name} {want} is in the lock but not installed")
            continue
        if have != want:
            problems.append(f"{name}: installed {have}, lock says {want}")
    installed: dict[str, list[str]] = {}
    for dist in md.distributions():         # everything importable, including copies found through PYTHONPATH
        dist_name = dist.metadata["Name"]
        if dist_name:
            installed.setdefault(re.sub(r"[-_.]+", "-", dist_name).lower(), []).append(dist.version)
    for name, versions in sorted(installed.items()):
        if name not in lock and name != "pip":
            problems.append(f"{name} is installed but is not in the lock")
        elif len(versions) > 1:
            problems.append(f"{name} is installed more than once ({', '.join(versions)})")
    for name in sorted(dependency_versions(project_dependencies)):
        if re.sub(r"[-_.]+", "-", name).lower() not in lock:
            problems.append(f"{name} is needed at run time but is not in the lock")
    if problems:
        die("the environment does not match requirements/lock.txt:\n  " + "\n  ".join(problems)
            + "\nInstall with: python -m pip install --require-hashes -r requirements/lock.txt")


def write_sbom(path: Path, version: str, packages: dict[str, str], age_version: str, commit: str) -> None:
    """CycloneDX 1.5 list of the Python packages and the bundled age release. It does not list CPython, OpenSSL, libffi,
    the C runtime or the libraries embedded in wheels (Qt, qpdf, PDFium); THIRD_PARTY_NOTICES.md names those."""
    components = [{"type": "library", "name": n, "version": v, "purl": f"pkg:pypi/{n}@{v}"} for n, v in sorted(packages.items())]
    components.append({"type": "application", "name": "age", "version": age_version,
                       "purl": f"pkg:github/FiloSottile/age@v{age_version.lstrip('v')}"})
    doc = {"bomFormat": "CycloneDX", "specVersion": "1.5", "version": 1,
           "metadata": {"timestamp": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                        "component": {"type": "application", "name": "Shiva", "version": version,
                                      "description": f"built from commit {commit}"}},
           "components": components}
    path.write_text(json.dumps(doc, indent=2) + "\n", encoding="utf-8", newline="\n")


def compile_app(build_dir: Path) -> Path:
    exes = sorted(AGE_DIR.glob("*.exe"))
    if not (AGE_DIR / "age.exe").is_file() or not (AGE_DIR / "age-plugin-batchpass.exe").is_file():
        die("age binaries missing; run tools/fetch_third_party.py first")
    version = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]["version"]
    numeric = ".".join((re.match(r"\d+(?:\.\d+)*", version).group(0).split(".") + ["0"] * 4)[:4])   # four numbers; a pre-release suffix such as rc1 is dropped
    cmd = [sys.executable, "-m", "nuitka", "--standalone", "--assume-yes-for-downloads",
           "--enable-plugin=pyside6", "--windows-console-mode=disable", "--include-package=shiva",
           "--include-package=pypdfium2", "--include-package=pypdfium2_raw",
           "--product-name=Shiva", "--file-description=Shiva: clean metadata, encrypt and shred files",
           f"--file-version={numeric}", f"--product-version={numeric}", "--copyright=GPL-3.0-or-later",
           "--output-filename=Shiva.exe", f"--output-dir={build_dir}"]
    if os.environ.get("GITHUB_ACTIONS"):
        cmd.append("--msvc=latest")   # without it Nuitka may pip-install an unpinned compiler package when none is found
    pdfium_files()   # Nuitka's dll-files plugin and package-data support copy pdfium.dll and version.json; checked after the build
    cmd += [f"--include-data-files={e}=age/{e.name}" for e in exes]
    cmd += [f"--include-data-files={AGE_DIR / 'LICENSE'}=age/LICENSE", str(ROOT / "src" / "shiva_launcher.py")]
    env = dict(os.environ, PYTHONPATH=str(ROOT / "src"))
    if subprocess.run(cmd, env=env, cwd=ROOT).returncode != 0:
        die("nuitka failed")
    return build_dir / "shiva_launcher.dist"


def selftest(folder: Path) -> None:
    sysroot = os.environ.get("SystemRoot", r"C:\Windows")
    scratch = tempfile.mkdtemp(prefix="shiva-selftest-")   # the self-test shreds a file: keep it out of C:\Windows\Temp (protected)
    env = {"SystemRoot": sysroot, "PATH": str(Path(sysroot) / "System32"),   # no Python, no age on PATH
           "TEMP": scratch, "TMP": scratch}
    try:
        p = subprocess.run([str(folder / "Shiva.exe"), "--selftest"], env=env, capture_output=True,
                           text=True, encoding="utf-8", errors="replace", timeout=180)
    finally:
        shutil.rmtree(scratch, ignore_errors=True)
    if p.returncode != 0 or "SELFTEST_OK" not in p.stdout:
        die(f"selftest failed (exit {p.returncode}): {p.stdout.strip()} {p.stderr.strip()}")


def sha256(p: Path) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def main() -> int:
    if os.name != "nt":
        die("the portable build targets Windows")
    version = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]["version"]
    project_deps = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]["dependencies"]
    check_environment(project_deps)
    if sys.version_info < MIN_PYTHON:
        print(f"WARNING: Python {sys.version.split()[0]} is older than {'.'.join(map(str, MIN_PYTHON))}; "
              "install Python 3.13 or newer before building a release.", file=sys.stderr)
    build_dir = ROOT / "build"
    dist = ROOT / "dist"
    name = f"Shiva-{version}-win64"
    src_dist = build_dir / "shiva_launcher.dist"
    if "--skip-compile" not in sys.argv:
        shutil.rmtree(build_dir, ignore_errors=True)
        src_dist = compile_app(build_dir)
    if not (src_dist / "Shiva.exe").is_file():
        die(f"{src_dist / 'Shiva.exe'} not found")

    out = dist / name
    shutil.rmtree(out, ignore_errors=True)
    dist.mkdir(exist_ok=True)
    shutil.copytree(src_dist, out)

    for e in AGE_DIR.glob("*.exe"):  # the silent-skip pitfall: assert instead of trusting
        if not (out / "age" / e.name).is_file():
            die(f"age/{e.name} missing from the build")
    for _src, rel in pdfium_files():
        if not (out / rel).is_file():
            die(f"{rel} missing from the build")
    for src, rel in LICENSE_FILES:
        if not src.is_file():
            die(f"licence file missing: {src.relative_to(ROOT)}")
        dest = out / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(src, dest)

    # interface translations: the app looks for translations/ next to Shiva.exe (src/shiva/i18n.py);
    # shiva_zh_TW.qm is compiled from the catalog table, qtbase_zh_TW.qm is Qt's own text (file dialog buttons)
    import compile_translations
    qm_dir = build_dir / "translations"
    qm = compile_translations.build(qm_dir)
    (out / "translations").mkdir(exist_ok=True)
    shutil.copyfile(qm, out / "translations" / qm.name)
    qt_qm = compile_translations.qt_base_qm()
    if qt_qm is None:
        die("qtbase_zh_TW.qm not found in the PySide6 wheel")
    shutil.copyfile(qt_qm, out / "translations" / qt_qm.name)

    # wheels that bundle compiled libraries carry their licence texts in dist-info/licenses; ship them
    for pkg, minimum in (("pypdfium2", 3), ("pillow", 1), ("pikepdf", 3), ("lxml", 2), ("nuitka", 3)):
        lic_src = Path(md.distribution(pkg)._path) / "licenses"
        n_lic = 0
        for f in sorted(p for p in lic_src.rglob("*") if p.is_file()):
            dest = out / "licenses" / pkg / f.relative_to(lic_src)
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(f, dest)
            n_lic += 1
        if n_lic < minimum:
            die(f"{pkg} licence files not found in the wheel metadata")

    # CPython's licence file covers the PSF licence, OpenSSL (Apache-2.0), libffi, bzip2 and the Microsoft runtime terms
    py_license = Path(sys.base_prefix) / "LICENSE.txt"
    if not py_license.is_file():
        die(f"CPython licence file not found: {py_license}")
    (out / "licenses" / "python").mkdir(parents=True, exist_ok=True)
    shutil.copyfile(py_license, out / "licenses" / "python" / "LICENSE.txt")

    manifest = tomllib.loads((ROOT / "third_party" / "MANIFEST.toml").read_text(encoding="utf-8"))
    info = [f"Shiva {version}", f"commit: {source_commit()}", f"built (UTC): {datetime.now(timezone.utc):%Y-%m-%d %H:%M}",
            f"python: {sys.version.split()[0]}" + ("  (older than the recommended minimum)" if sys.version_info < MIN_PYTHON else ""),
            f"lock: requirements/lock.txt sha256 {sha256(LOCK)[:16]}...", f"nuitka: {md.version('nuitka')}",
            f"age: {manifest['age']['version']}", "", "Python packages (direct and pulled in):",
            *[f"  {k}=={v}" for k, v in sorted(dependency_versions(
                tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]["dependencies"]).items())],
            "", "Reproducibility: same source + the versions above + pinned age (third_party/MANIFEST.toml)",
            "should give the same file list; Nuitka output is not guaranteed to be byte-identical."]
    (out / "BUILD-INFO.txt").write_text("\n".join(info) + "\n", encoding="utf-8", newline="\n")

    selftest(out)

    zip_path = dist / f"{name}.zip"
    zip_path.unlink(missing_ok=True)
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as z:
        for f in sorted(out.rglob("*")):
            if f.is_file():
                z.write(f, Path(name) / f.relative_to(out))
    digest = sha256(zip_path)
    (dist / "SHA256SUMS.txt").write_text(f"{digest} *{zip_path.name}\n", encoding="utf-8", newline="\n")
    packages = dependency_versions(project_deps)
    write_sbom(dist / f"{name}-sbom.cdx.json", version, packages, manifest["age"]["version"], source_commit())
    print(f"OK {zip_path.name}  {zip_path.stat().st_size / 1e6:.1f} MB  sha256={digest}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
