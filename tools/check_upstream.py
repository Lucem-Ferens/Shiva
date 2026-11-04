"""Report whether upstream projects listed in third_party/MANIFEST.toml have newer releases.

Usage: python tools/check_upstream.py [--json]
Exit code: 0 = all up to date, 10 = at least one newer upstream release, 2 = lookup error.
Read-only: never changes any file. Uses the GitHub API (set GITHUB_TOKEN to avoid rate limits).
Newer releases are reported only; a human decides whether to adopt them.

"""
from __future__ import annotations

import json
import os
import sys
import tomllib
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _get(url: str):
    req = urllib.request.Request(url, headers={"Accept": "application/vnd.github+json",
                                               "User-Agent": "shiva-upstream-check"})
    tok = os.environ.get("GITHUB_TOKEN")
    if tok:
        req.add_header("Authorization", f"Bearer {tok}")
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.load(r)


def _vtuple(v: str):
    parts = []
    for p in v.lstrip("vV").split("."):
        digits = "".join(ch for ch in p if ch.isdigit())
        parts.append(int(digits) if digits else 0)
    return tuple(parts)


def main() -> int:
    as_json = "--json" in sys.argv
    manifest = tomllib.loads((ROOT / "third_party" / "MANIFEST.toml").read_text(encoding="utf-8"))
    rows, newer, errors = [], False, False
    for name, c in manifest.items():
        if "repo" not in c:
            continue
        row = {"name": name, "kind": c["kind"], "ours": c["version"], "last_reviewed": c.get("last_reviewed")}
        try:
            rel = _get(f"https://api.github.com/repos/{c['repo']}/releases/latest")
            row["latest"] = rel["tag_name"].lstrip("vV")
            row["published"] = rel.get("published_at")
            row["url"] = rel.get("html_url")
            row["newer"] = _vtuple(row["latest"]) > _vtuple(c["version"])
            newer |= row["newer"]
        except (urllib.error.URLError, KeyError, ValueError) as e:
            row["error"] = str(e)
            errors = True
        rows.append(row)
    if as_json:
        print(json.dumps(rows, indent=2))
    else:
        for r in rows:
            if "error" in r:
                print(f"{r['name']:<10} ERROR {r['error']}")
            else:
                flag = "NEWER " if r["newer"] else "ok    "
                print(f"{flag}{r['name']:<10} ours={r['ours']:<8} upstream={r['latest']:<8} "
                      f"[{r['kind']}] published={r['published']} {r['url']}")
    return 2 if errors else (10 if newer else 0)


if __name__ == "__main__":
    sys.exit(main())
