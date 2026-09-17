#!/usr/bin/env python3
"""Stage 1's directory-scoping security check -- offers to scope Claude's file
reads to this plugin's own directory tree for the life of a dvd-autorip
session, via a project-local `permissions.blockReadsOutsideWorkingDirectories`
setting (Claude Code's own mechanism, not something this skill invents).

Real incident this is built from: a live run wrote evidence-gathering output
outside its staging tree (see gotchas.md's "Stage 7 evidence output defaulting
outside the sandbox"). The user asked Claude Code to sandbox itself against a
recurrence -- but answered that prompt with the GLOBAL form
(`~/.claude/settings.json`), which then applied to every Claude Code session
on the machine, for every project, forever -- not just this skill. That was
too strict for general use and had to be walked back. The project-scoped form
here (`<plugin root>/.claude/settings.local.json`, gitignored, never shipped
to other users who install this plugin) gets the same real protection without
that blast radius.

**Always the plugin root, deliberately, not the shell's launch directory or
any other candidate.** every OTHER skill dependency (scripts, references) stays
inside the skill's own folder tree, nowhere else, so nothing about this feature
depends on how or from where a given user happens to invoke `claude`. Confirmed
working in practice against a real session launched via `--plugin-dir <plugin
root>`.

`config.local.json` is the one deliberate exception -- it lives at a fixed
`~/.claude/dvd-autorip-skill/config.local.json`, permanently OUTSIDE the plugin's
own directory tree, moved there specifically because a marketplace install/update
copies the plugin root into a new version-numbered cache folder every time
(confirmed empirically), which would otherwise mean a config living inside that
tree either goes stale (edits not seen until the next update) or gets orphaned
entirely (a marketplace source like GitHub never contains a user's real config to
carry forward across a version bump). See config-schema.md for the full
reasoning. This script accounts for that: unlike `staging.path` (which is only
SOMETIMES outside the plugin root, a user choice), the config directory is
ALWAYS outside it, unconditionally, by construction -- so `enable` below always
adds it to `additionalDirectories` automatically, with no decision point needed,
rather than asking the user to remember a `--add-dir` flag forever for a location
they never chose in the first place.

THE TRAP THIS SCRIPT EXISTS TO CATCH: enabling this setting only helps if
every directory the pipeline actually touches is in scope. `staging.path` is
commonly configured OUTSIDE the plugin root (a sibling directory, an entirely
different drive) -- config-schema.md's own default only nests it inside when
staging.path is left unset. Turning this protection on without also covering
staging.path (and the fixed config directory, handled automatically) recreates
the exact failure this whole feature is meant to prevent: every read/write to an
out-of-scope path starts failing the "is this inside the working directory"
check, in every permission mode, unconditionally (Claude Code's own documented
behavior for this setting) -- indistinguishable, from the inside, from the
pipeline being broken. Hence the `staging_path_in_scope` check below: Stage 1
must know this BEFORE offering to enable the protection, not discover it later
during a run. **Fix for an out-of-scope staging.path is `--add-dir
<staging.path>` at launch, not moving this settings file somewhere else** -- the
settings file's location is fixed by design (see above); `--add-dir` is Claude
Code's own documented mechanism for widening what's in scope for a session
without touching where any config lives. The fixed config directory doesn't need
this manual fallback since `enable` handles it automatically via
`additionalDirectories` instead.

Usage:
    python check_directory_scoping.py check --plugin-root <path> --staging-path <resolved path>
        Reports current state: is the protection already on, is staging.path
        actually inside plugin-root (or otherwise in scope), and is the fixed
        config directory in scope. Check all fields every time, regardless of
        "protection_enabled" -- a real, tested case had protection on but
        staging out of scope at the same time (see gotchas.md). Makes no
        changes.

    python check_directory_scoping.py enable --plugin-root <path>
        Writes (merging, never clobbering existing content)
        permissions.blockReadsOutsideWorkingDirectories: true, and adds the
        fixed config directory to permissions.additionalDirectories (deduped,
        existing entries preserved), into <plugin-root>/.claude/settings.local.json,
        creating the directory and file if needed.

Outputs JSON to stdout in both cases.
"""
from __future__ import annotations

import argparse
import json
import os
import sys

CONFIG_DIR = os.path.expanduser(os.path.join("~", ".claude", "dvd-autorip-skill"))


def settings_path_for(plugin_root: str) -> str:
    return os.path.join(plugin_root, ".claude", "settings.local.json")


def is_nested(parent: str, child: str) -> bool:
    parent = os.path.abspath(parent)
    child = os.path.abspath(child)
    try:
        common = os.path.commonpath([parent, child])
    except ValueError:
        # Different drives on Windows -- definitely not nested.
        return False
    return os.path.normcase(common) == os.path.normcase(parent)


def _additional_directories(data: dict) -> list[str]:
    dirs = data.get("permissions", {}).get("additionalDirectories", [])
    return dirs if isinstance(dirs, list) else []


def _path_in_scope(plugin_root: str, target: str, additional_dirs: list[str]) -> bool:
    if is_nested(plugin_root, target):
        return True
    target_abs = os.path.normcase(os.path.abspath(target))
    return any(os.path.normcase(os.path.abspath(d)) == target_abs for d in additional_dirs)


def cmd_check(plugin_root: str, staging_path: str) -> dict:
    plugin_root = os.path.abspath(plugin_root)
    staging_path = os.path.abspath(staging_path)
    settings_file = settings_path_for(plugin_root)

    enabled = False
    additional_dirs: list[str] = []
    if os.path.exists(settings_file):
        try:
            with open(settings_file, "r", encoding="utf-8") as f:
                data = json.load(f)
            enabled = data.get("permissions", {}).get("blockReadsOutsideWorkingDirectories") is True
            additional_dirs = _additional_directories(data)
        except (json.JSONDecodeError, OSError):
            pass  # treat an unreadable/corrupt file as "not enabled" -- Stage 1 will offer to fix it

    return {
        "protection_enabled": enabled,
        "staging_path_in_scope": _path_in_scope(plugin_root, staging_path, additional_dirs),
        "config_path_in_scope": _path_in_scope(plugin_root, CONFIG_DIR, additional_dirs),
        "config_path": CONFIG_DIR,
        "plugin_root": plugin_root,
        "staging_path_resolved": staging_path,
        "settings_file": settings_file,
    }


def cmd_enable(plugin_root: str) -> dict:
    plugin_root = os.path.abspath(plugin_root)
    settings_file = settings_path_for(plugin_root)
    os.makedirs(os.path.dirname(settings_file), exist_ok=True)

    data = {}
    if os.path.exists(settings_file):
        try:
            with open(settings_file, "r", encoding="utf-8") as f:
                data = json.load(f)
        except (json.JSONDecodeError, OSError):
            data = {}  # corrupt file -- rewrite cleanly rather than fail Stage 1 over it

    permissions = data.setdefault("permissions", {})
    permissions["blockReadsOutsideWorkingDirectories"] = True

    existing_dirs = permissions.get("additionalDirectories", [])
    if not isinstance(existing_dirs, list):
        existing_dirs = []
    config_dir_abs = os.path.normcase(os.path.abspath(CONFIG_DIR))
    already_present = any(os.path.normcase(os.path.abspath(d)) == config_dir_abs for d in existing_dirs)
    if not already_present and not is_nested(plugin_root, CONFIG_DIR):
        existing_dirs.append(CONFIG_DIR)
    permissions["additionalDirectories"] = existing_dirs

    with open(settings_file, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)
        f.write("\n")

    return {"ok": True, "settings_file": settings_file, "config_dir_added": CONFIG_DIR}


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("action", choices=["check", "enable"])
    ap.add_argument("--plugin-root", required=True)
    ap.add_argument("--staging-path", help="required for 'check' -- the resolved (not default-relative) staging.path")
    args = ap.parse_args()

    if args.action == "check":
        if not args.staging_path:
            print(json.dumps({"error": "--staging-path is required for 'check'"}))
            sys.exit(2)
        result = cmd_check(args.plugin_root, args.staging_path)
    else:
        result = cmd_enable(args.plugin_root)

    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
