#!/usr/bin/env python3
"""Scan the current machine for the external tools this pipeline needs.

Never hardcodes a single machine's install path -- everything is discovered via PATH
first, with a small set of common install-location fallbacks on Windows. Reports a
structured result so SKILL.md's Stage 1 can relay accurate, actionable guidance
(what's missing, whether it's a hard requirement or an optional-feature gap) rather
than failing opaquely partway through a run.

Usage:
    python check_dependencies.py            # human-readable report, exit 0/1
    python check_dependencies.py --json      # machine-readable report on stdout

Exit code is 0 only if every HARD requirement is satisfied. Missing OPTIONAL tools
never fail the run -- they're reported so Claude can tell the user which fallback
path (e.g. OCR) is unavailable this session.
"""

from __future__ import annotations

import json
import platform
import shutil
import subprocess
import sys
import threading
from dataclasses import dataclass, field

# Hard ceiling on the whole scan, independent of any per-subprocess timeout below.
# Belt-and-suspenders: if some future check ever adds a subprocess call that doesn't
# go through _run() (or _run()'s own tree-kill somehow doesn't clear a hang), this is
# what actually stops the script from blocking a caller indefinitely.
OVERALL_TIMEOUT_SECONDS = 90


@dataclass
class CheckResult:
    name: str
    required: bool
    found: bool
    detail: str = ""
    install_hint: str = ""
    always_show_hint: bool = False  # for informational (non-pass/fail) notes, e.g. execution policy
    key: str = ""              # stable machine-readable id -- used by SKILL.md's Stage 1
                                # install-offer flow and config.local.json's
                                # setup.declined_optional_installs list. Empty for checks
                                # that flow never offers to act on (e.g. "OS support").
    installable: bool = False  # True only when install_hint is a real runnable command
                                # Claude could offer to execute -- not just an informational
                                # statement (e.g. Python's "3.8+ required" has no command).
    found_via_fallback: bool = False  # True when found, but only via a hardcoded
                                # install-location guess (e.g. Program Files), not a
                                # plain PATH hit. Explicit flag rather than making
                                # Stage 1 guess this from the `detail` string -- a
                                # real gap: Tesseract's check had no fallback lookup
                                # at all until this field existed, so it reported
                                # "missing" and offered to install something that was
                                # already on the machine, just not on PATH.
    resolved_path: str = ""    # the actual full path Stage 1 should persist into
                                # config.local.json when found_via_fallback is True
                                # (e.g. makemkv.path, tesseract.path). Empty when not
                                # applicable (found on PATH, or genuinely not found).


@dataclass
class Report:
    os_name: str
    supported: bool
    checks: list[CheckResult] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.supported and all(c.found for c in self.checks if c.required)


def _kill_tree(pid: int) -> None:
    """Kill pid and everything it spawned.

    Popen.kill() only signals the immediate child. On Windows, a launched process
    (e.g. powershell.exe) can spawn a grandchild that inherits the stdout/stderr pipe
    handles -- killing only the parent leaves those handles open, and a subsequent
    communicate() blocks past its own timeout waiting for EOF that never actually
    comes. A real run hung this way for 12 hours despite the 10s timeout below doing
    exactly what it was supposed to. `taskkill /T` kills the whole tree, which
    actually closes the handles. POSIX doesn't have this specific gap (a killed
    process's fds close with it), so a plain kill() is enough there.
    """
    if platform.system() == "Windows":
        subprocess.run(
            ["taskkill", "/F", "/T", "/PID", str(pid)],
            capture_output=True, timeout=10, check=False,
        )


def _run(cmd: list[str]) -> tuple[bool, str]:
    try:
        proc = subprocess.Popen(
            cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True
        )
    except FileNotFoundError:
        return False, ""
    try:
        stdout, stderr = proc.communicate(timeout=10)
    except subprocess.TimeoutExpired:
        proc.kill()
        _kill_tree(proc.pid)
        try:
            proc.communicate(timeout=10)
        except subprocess.TimeoutExpired:
            pass  # tree-kill above should have freed the pipes either way; give up
                  # cleanly rather than risk blocking on communicate() a third time.
        return False, ""
    output = (stdout or stderr or "").strip()
    first_line = output.splitlines()[0] if output else ""
    return True, first_line


def _which_any(names: list[str]) -> str | None:
    for name in names:
        found = shutil.which(name)
        if found:
            return found
    return None


def check_makemkv() -> CheckResult:
    path = _which_any(["makemkvcon", "makemkvcon64"])
    via_fallback = False
    if platform.system() == "Windows":
        fallback_dirs = [
            r"C:\Program Files (x86)\MakeMKV\makemkvcon64.exe",
            r"C:\Program Files\MakeMKV\makemkvcon64.exe",
        ]
    else:
        fallback_dirs = [
            "/usr/bin/makemkvcon",
            "/usr/local/bin/makemkvcon",
            "/opt/makemkv/bin/makemkvcon",
            "/Applications/MakeMKV.app/Contents/MacOS/makemkvcon",
        ]
    if not path:
        import os

        for candidate in fallback_dirs:
            if os.path.isfile(candidate):
                path = candidate
                via_fallback = True
                break
    if path:
        ok, detail = _run([path, "-r", "info"])
        return CheckResult(
            "makemkvcon (MakeMKV)", True, True, detail=f"found at {path}", key="makemkv",
            found_via_fallback=via_fallback, resolved_path=path if via_fallback else "",
        )
    if platform.system() == "Windows":
        hint = (
            "Install MakeMKV (https://www.makemkv.com/) and ensure makemkvcon.exe is on "
            "PATH, or set config.local.json's makemkv.path to its full path."
        )
        installable = False  # no single runnable command -- MakeMKV has no winget package
    else:
        hint = (
            "Install MakeMKV (https://www.makemkv.com/) -- Linux: build from their "
            "source tarball or use a distro package if one exists; Mac: the .dmg "
            "download -- and ensure makemkvcon is on PATH, or set config.local.json's "
            "makemkv.path to its full path."
        )
        installable = False  # same -- no single runnable command on any OS
    return CheckResult("makemkvcon (MakeMKV)", True, False, install_hint=hint, key="makemkv", installable=installable)


def check_ffmpeg() -> CheckResult:
    ffmpeg_path = shutil.which("ffmpeg")
    ffprobe_path = shutil.which("ffprobe")
    if ffmpeg_path and ffprobe_path:
        return CheckResult("ffmpeg + ffprobe", True, True, detail=f"found at {ffmpeg_path}", key="ffmpeg")
    system = platform.system()
    if system == "Windows":
        hint = (
            "winget install --id Gyan.FFmpeg --exact  (Windows; use the 'full build', "
            "needed for the menu-frame reads and OCR fallback this pipeline relies on)"
        )
    elif system == "Darwin":
        hint = "brew install ffmpeg  (Mac; needed for the menu-frame reads and OCR fallback this pipeline relies on)"
    else:
        hint = "apt install ffmpeg  (or your distro's equivalent; needed for the menu-frame reads and OCR fallback this pipeline relies on)"
    return CheckResult("ffmpeg + ffprobe", True, False, install_hint=hint, key="ffmpeg", installable=True)


def check_powershell() -> CheckResult:
    if platform.system() != "Windows":
        return CheckResult("PowerShell", True, False, detail="not applicable on this OS", key="powershell")
    path = _which_any(["pwsh", "powershell"])
    if not path:
        return CheckResult(
            "PowerShell", True, False,
            install_hint="Windows PowerShell should ship with Windows; if missing, install PowerShell 7+ (winget install Microsoft.PowerShell).",
            key="powershell", installable=True,
        )
    ok, detail = _run([path, "-NoProfile", "-Command", "$PSVersionTable.PSVersion.ToString()"])
    return CheckResult("PowerShell", True, True, detail=f"version {detail}" if detail else f"found at {path}", key="powershell")


def check_python() -> CheckResult:
    version = sys.version_info
    ok = version >= (3, 8)
    return CheckResult(
        "Python", True, ok,
        detail=f"{platform.python_version()}",
        install_hint="Python 3.8+ required." if not ok else "",
        key="python", installable=False,  # no single runnable command -- this is the
                                           # interpreter check_dependencies.py itself is
                                           # already running under
    )


def check_execution_policy() -> CheckResult:
    path = _which_any(["pwsh", "powershell"])
    if not path:
        return CheckResult("PowerShell execution policy", False, False, detail="skipped -- no PowerShell found", key="execution_policy")
    ok, detail = _run([path, "-NoProfile", "-Command", "Get-ExecutionPolicy"])
    restrictive = ok and detail.strip() in ("Restricted", "AllSigned")
    if not restrictive:
        return CheckResult("PowerShell execution policy", False, True, detail=f"{detail or 'unknown'} (not a blocker)", key="execution_policy")
    return CheckResult(
        "PowerShell execution policy",
        False,
        True,  # informational only -- not a hard requirement, see detail
        detail=f"{detail} -- this blocks running .ps1 files directly by default",
        install_hint=(
            "Not something to change machine-wide for this skill. Every script "
            "invocation in SKILL.md already includes -ExecutionPolicy Bypass, which "
            "works around this per-invocation with no persistent change -- nothing "
            "further to do unless you're running a script manually outside the skill."
        ),
        always_show_hint=True,
        key="execution_policy", installable=False,  # informational, no command to offer
    )


def check_tesseract() -> CheckResult:
    path = shutil.which("tesseract")
    via_fallback = False
    if not path:
        # PATH-only lookup used to be the whole check -- a real gap: Tesseract can be
        # (and was, on a real machine) installed correctly but not on PATH, which
        # made this report "missing" and made Stage 1 offer to install something
        # already present. Mirrors check_makemkv()'s fallback-location pattern.
        import os

        if platform.system() == "Windows":
            fallback_dirs = [
                r"C:\Program Files\Tesseract-OCR\tesseract.exe",
                r"C:\Program Files (x86)\Tesseract-OCR\tesseract.exe",
            ]
        elif platform.system() == "Darwin":
            fallback_dirs = [
                "/opt/homebrew/bin/tesseract",  # Apple Silicon brew prefix
                "/usr/local/bin/tesseract",      # Intel brew prefix
            ]
        else:
            fallback_dirs = ["/usr/bin/tesseract", "/usr/local/bin/tesseract"]

        for candidate in fallback_dirs:
            if os.path.isfile(candidate):
                path = candidate
                via_fallback = True
                break

    if path:
        return CheckResult(
            "Tesseract OCR (optional)", False, True, detail=f"found at {path}", key="tesseract",
            found_via_fallback=via_fallback, resolved_path=path if via_fallback else "",
        )
    system = platform.system()
    if system == "Windows":
        install_cmd = "winget install --id UB-Mannheim.TesseractOCR"
    elif system == "Darwin":
        install_cmd = "brew install tesseract"
    else:
        install_cmd = "apt install tesseract-ocr"
    return CheckResult(
        "Tesseract OCR (optional)",
        False,
        False,
        install_hint=(
            f"{install_cmd}. Without it, discs with only image-based subtitles fall "
            "back to frame-based visual identification instead of OCR'd dialogue text "
            "-- weaker but still usable."
        ),
        key="tesseract", installable=True,
    )


def check_bash() -> CheckResult:
    """Required only on Linux/Mac, where scripts/platform/linux/*.sh does the real
    work check_dependencies.py's Windows path hands off to PowerShell for instead."""
    if platform.system() == "Windows":
        return CheckResult("bash", False, False, detail="not applicable on this OS", key="bash")
    path = shutil.which("bash")
    if not path:
        return CheckResult(
            "bash", True, False,
            install_hint="bash should ship with every Linux distro and macOS; if genuinely missing, install it via your package manager.",
            key="bash", installable=False,  # no single command -- package name varies
                                             # by distro on Linux; brew case below is
                                             # the one with a real runnable command
        )
    ok, detail = _run([path, "-c", "echo ${BASH_VERSINFO[0]}"])
    major = int(detail) if detail.isdigit() else 0
    if major < 4:
        return CheckResult(
            "bash", True, False,
            detail=f"found at {path}, version {major or 'unknown'} -- need 4+ (scripts/platform/linux/*.sh use associative arrays)",
            install_hint=(
                "macOS ships bash 3.2 by default (Apple stopped updating it for "
                "licensing reasons) -- install a modern one with: brew install bash. "
                "Make sure it's first on PATH, or invoke scripts/platform/linux/*.sh "
                "with that bash explicitly rather than /bin/bash."
            ),
            key="bash", installable=(platform.system() == "Darwin"),
        )
    return CheckResult("bash", True, True, detail=f"found at {path}, version {major}", key="bash")


def check_eject_tool() -> CheckResult:
    """Linux only -- scripts/platform/linux/eject.sh shells out to the `eject` binary
    there; Mac uses the always-present drutil/diskutil instead, so this doesn't apply."""
    if platform.system() != "Linux":
        return CheckResult("eject (Linux util-linux)", False, False, detail="not applicable on this OS", key="eject")
    path = shutil.which("eject")
    if path:
        return CheckResult("eject (Linux util-linux)", True, True, detail=f"found at {path}", key="eject")
    return CheckResult(
        "eject (Linux util-linux)", True, False,
        install_hint="apt install eject  (or your distro's util-linux/eject package; needed by scripts/platform/linux/eject.sh)",
        key="eject", installable=True,
    )


def build_report() -> Report:
    os_name = platform.system()
    supported = os_name in ("Windows", "Linux", "Darwin")
    report = Report(os_name=os_name, supported=supported)
    if not supported:
        report.checks.append(
            CheckResult(
                "OS support", True, False,
                detail=f"detected {os_name}",
                install_hint="This skill supports Windows, Linux, and Mac. Nothing is implemented for this OS.",
            )
        )
        return report

    common = [check_makemkv(), check_ffmpeg()]
    if os_name == "Windows":
        report.checks = common + [
            check_powershell(),
            check_execution_policy(),
            check_python(),
            check_tesseract(),
        ]
    else:
        report.checks = common + [
            check_bash(),
            check_eject_tool(),
            check_python(),
            check_tesseract(),
        ]
    return report


def print_human(report: Report) -> None:
    print(f"OS: {report.os_name}")
    for c in report.checks:
        status = "OK" if c.found else ("MISSING (required)" if c.required else "MISSING (optional)")
        line = f"  [{status}] {c.name}"
        if c.detail:
            line += f" -- {c.detail}"
        print(line)
        if c.install_hint and (not c.found or c.always_show_hint):
            print(f"      -> {c.install_hint}")
    print()
    print("Result: READY" if report.ok else "Result: NOT READY -- see required items above")


def _build_report_with_watchdog() -> Report | None:
    """Run build_report() on a daemon thread with a hard overall deadline.

    _run()'s own per-subprocess timeout + tree-kill (above) is the real fix for the
    hang this is guarding against, but a daemon thread costs nothing and means this
    script can never again block a caller indefinitely even if some future check adds
    a subprocess call that bypasses _run(), or the tree-kill itself doesn't fully
    clear a hang on some machine. `daemon=True` matters: it's what lets the process
    actually exit on timeout instead of hanging at interpreter shutdown waiting to
    join a thread that never finishes.
    """
    result: list[Report] = []
    thread = threading.Thread(target=lambda: result.append(build_report()), daemon=True)
    thread.start()
    thread.join(timeout=OVERALL_TIMEOUT_SECONDS)
    return result[0] if result else None


def main() -> int:
    report = _build_report_with_watchdog()
    if report is None:
        timeout_payload = {
            "os": platform.system(),
            "supported": True,
            "ok": False,
            "timed_out": True,
            "checks": [],
        }
        if "--json" in sys.argv:
            print(json.dumps(timeout_payload, indent=2))
        else:
            print(
                f"Result: NOT READY -- dependency scan did not finish within "
                f"{OVERALL_TIMEOUT_SECONDS}s (real hang, not just a slow check -- "
                f"treat this as a problem to investigate, not something to keep "
                f"waiting on)."
            )
        return 1
    if "--json" in sys.argv:
        print(json.dumps(
            {
                "os": report.os_name,
                "supported": report.supported,
                "ok": report.ok,
                "checks": [c.__dict__ for c in report.checks],
            },
            indent=2,
        ))
    else:
        print_human(report)
    return 0 if report.ok else 1


if __name__ == "__main__":
    sys.exit(main())
