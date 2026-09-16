#!/usr/bin/env python3
"""Thin Jellyfin API wrapper: bakes in the auth-header fallback documented in
gotchas.md's "Jellyfin API auth header can change across server versions" so
Claude doesn't have to re-derive that fallback logic by hand at unpredictable
points mid-session -- the incident that motivated this had every call succeed
for hours on `X-Emby-Token`, then every call (including a bare /System/Info)
start 401ing with no config change on this pipeline's side; switching to
`Authorization: MediaBrowser Token=...` fixed it immediately, without even
regenerating the key. This script always tries the new header first and falls
back to the old one on a 401, on every call, not just Stage 1's reachability
check -- so the fallback is available exactly where the incident happened
(mid-session), without Claude needing to remember it applies there too.

Also hard-stops on `/Items/RemoteSearch/Episode` with a clear, documented
error instead of letting Claude discover the real 404 live -- see SKILL.md
Stage 9 and gotchas.md's Jellyfin API gotchas ("POST /Items/RemoteSearch/
Episode does not exist -- confirmed via a live 404").

Usage:
    python jellyfin_api.py <config.local.json path> GET System/Info
    python jellyfin_api.py <config path> POST Items/RemoteSearch/Movie --data '{"SearchInfo": {...}}'
    python jellyfin_api.py <config path> POST Library/Refresh

**Write the REST path without a leading `/`** (`System/Info`, not `/System/Info`) --
this script normalizes either form internally, but a leading `/` is exactly the
pattern Git Bash's MSYS layer auto-converts into a Windows path before Python ever
sees the argument, which is what makes the `MSYS_NO_PATHCONV=1` workaround seem
necessary. Don't reach for that workaround instead: if directory-scoping protection
(`permissions.blockReadsOutsideWorkingDirectories`) is enabled, an env-var-prefixed
command can never be approved, on any tool, in any permission mode -- confirmed
directly, see gotchas.md's "MSYS_NO_PATHCONV=1 collides with directory-scoping
protection". Writing the path without a leading `/` avoids the mangling outright.

Takes the config path explicitly as its first argument, same convention as
scripts/setup/validate_config.py -- deliberately not defaulting to a guessed
location, since a config file written to the wrong place
(${CLAUDE_SKILL_DIR}/config/ instead of the plugin root) is a real,
previously-encountered mistake (see SKILL.md Stage 1). Reads
jellyfin.base_url / jellyfin.api_key from that file -- never pass the API key
directly on the command line, and never echo it back.

Outputs JSON to stdout:
  success: {"ok": true, "status": 200, "auth_header_used": "...", "body": <parsed response or raw text>}
  failure: {"ok": false, "error_type": "auth"|"connection"|"http"|"config"|"blocked", "detail": "..."}
Exit code 0 iff "ok" is true.
"""
from __future__ import annotations

import argparse
import json
import sys
import urllib.error
import urllib.request

# Order matters: try the modern header first (confirmed working against
# Jellyfin 12.0.0+), fall back to the legacy one an older server may still
# require -- see this file's docstring and gotchas.md for the incident.
AUTH_HEADERS = [
    ("Authorization", "MediaBrowser Token={key}"),
    ("X-Emby-Token", "{key}"),
]

BLOCKED_PATHS = {
    "/items/remotesearch/episode": (
        "POST /Items/RemoteSearch/Episode does not exist in Jellyfin's API "
        "(confirmed via a live 404, not just missing docs -- see gotchas.md's "
        "Jellyfin API gotchas). There is no per-episode RemoteSearch/Apply pair. "
        "See SKILL.md Stage 9 for the actual episode-correction path: rely on "
        "correct season/episode numbering plus a full POST /Library/Refresh, or "
        "force re-derivation with POST /Items/{episodeId}/Refresh?Recursive=true"
        "&ReplaceAllMetadata=true&ImageRefreshMode=FullRefresh&MetadataRefreshMode=FullRefresh."
    ),
}


def load_config(path: str) -> dict:
    try:
        with open(path, "r", encoding="utf-8") as f:
            cfg = json.load(f)
    except FileNotFoundError:
        print(json.dumps({"ok": False, "error_type": "config", "detail": f"config file not found: {path}"}))
        sys.exit(1)
    except json.JSONDecodeError as e:
        print(json.dumps({"ok": False, "error_type": "config", "detail": f"config file is not valid JSON: {e}"}))
        sys.exit(1)
    jellyfin = cfg.get("jellyfin", {})
    base_url = jellyfin.get("base_url")
    api_key = jellyfin.get("api_key")
    if not base_url or not api_key:
        print(json.dumps({"ok": False, "error_type": "config", "detail": "config missing jellyfin.base_url or jellyfin.api_key"}))
        sys.exit(1)
    return {"base_url": base_url.rstrip("/"), "api_key": api_key}


def do_request(base_url: str, api_key: str, method: str, path: str, data, timeout: int = 30) -> dict:
    url = base_url + (path if path.startswith("/") else "/" + path)
    body_bytes = json.dumps(data).encode("utf-8") if data is not None else None
    for header_name, header_template in AUTH_HEADERS:
        headers = {header_name: header_template.format(key=api_key)}
        if body_bytes is not None:
            headers["Content-Type"] = "application/json"
        req = urllib.request.Request(url, data=body_bytes, headers=headers, method=method)
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                raw = resp.read().decode("utf-8", errors="replace")
                try:
                    parsed = json.loads(raw) if raw else None
                except json.JSONDecodeError:
                    parsed = raw
                return {"ok": True, "status": resp.status, "auth_header_used": header_name, "body": parsed}
        except urllib.error.HTTPError as e:
            if e.code == 401:
                continue  # try the next header form before giving up
            raw = e.read().decode("utf-8", errors="replace")
            return {"ok": False, "error_type": "http", "status": e.code, "detail": raw[:2000], "auth_header_used": header_name}
        except urllib.error.URLError as e:
            return {"ok": False, "error_type": "connection", "detail": str(e.reason)}
    return {
        "ok": False,
        "error_type": "auth",
        "detail": (
            f"401 with both auth header forms ({', '.join(h for h, _ in AUTH_HEADERS)}) -- "
            "server was reachable but the key was rejected under either form. Don't assume "
            "the key itself is bad without also confirming the server is healthy via an "
            "unauthenticated check first (see gotchas.md's Jellyfin auth-header incident, "
            "where regenerating the key did NOT fix a 401 that a header switch alone did) "
            "-- tell the user plainly and offer to re-enter the key."
        ),
    }


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("config_path")
    ap.add_argument("method", choices=["GET", "POST", "DELETE", "PUT"])
    ap.add_argument("path")
    ap.add_argument("--data", help="JSON string request body")
    args = ap.parse_args()
    if not args.path.startswith("/"):
        args.path = "/" + args.path

    if args.path.lower() in BLOCKED_PATHS:
        print(json.dumps({"ok": False, "error_type": "blocked", "detail": BLOCKED_PATHS[args.path.lower()]}))
        sys.exit(1)

    cfg = load_config(args.config_path)
    try:
        data = json.loads(args.data) if args.data else None
    except json.JSONDecodeError as e:
        print(json.dumps({"ok": False, "error_type": "config", "detail": f"--data is not valid JSON: {e}"}))
        sys.exit(1)

    result = do_request(cfg["base_url"], cfg["api_key"], args.method, args.path, data)
    print(json.dumps(result, indent=2))
    sys.exit(0 if result.get("ok") else 1)


if __name__ == "__main__":
    main()
