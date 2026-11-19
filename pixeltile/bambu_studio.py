"""Find Bambu Studio on this computer and open PixelTile files in it.

Bambu Studio loads any files given on its command line; with its "single instance" setting on,
they go to the window that is already open. On macOS, `open -a` does the same.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

FLATPAK_ID = "com.bambulab.BambuStudio"
# Bambu Studio writes this ProgID when it takes over .3mf files (its source has the leading space).
_PROG_IDS = (" Bambu.Studio.1", "Bambu.Studio.1")


@dataclass
class Launcher:
    label: str           # shown to the user: the program path, or the Flatpak id
    command: list[str]   # program and fixed arguments; file paths are appended


def looks_like_bambu(path: str | Path) -> bool:
    """Only ever launch something that is named like Bambu Studio."""
    return "bambu" in Path(path).name.lower()


def _exe_from_command(cmd: str) -> str | None:
    """'"C:\\...\\bambu-studio.exe" "%1"' -> 'C:\\...\\bambu-studio.exe'."""
    cmd = cmd.strip()
    if cmd.startswith('"'):
        end = cmd.find('"', 1)
        return cmd[1:end] if end > 1 else None
    return cmd.split(" ", 1)[0] or None


def _from_windows_registry() -> str | None:
    try:
        import winreg
    except ImportError:
        return None
    for prog_id in _PROG_IDS:
        for hive in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
            try:
                with winreg.OpenKey(hive, rf"Software\Classes\{prog_id}\Shell\Open\Command") as key:
                    cmd, _ = winreg.QueryValueEx(key, "")
            except OSError:
                continue
            exe = _exe_from_command(str(cmd))
            if exe and Path(exe).is_file():
                return exe
    return None


def _launcher_for(path: Path) -> Launcher | None:
    if not looks_like_bambu(path):
        return None
    if path.suffix.lower() == ".app" and path.is_dir():
        return Launcher(str(path), ["open", "-a", str(path)])
    if path.is_file():
        return Launcher(str(path), [str(path)])
    return None


def find_bambu_studio(configured: str | None = None) -> Launcher | None:
    """Locate Bambu Studio: a path the user set, then the usual install locations."""
    for explicit in (configured, os.environ.get("PIXELTILE_BAMBU_STUDIO")):
        if explicit and explicit.strip():
            found = _launcher_for(Path(explicit.strip()).expanduser())
            if found:
                return found
    if sys.platform == "win32":
        candidates = [_from_windows_registry()]
        for var in ("ProgramFiles", "ProgramW6432", "ProgramFiles(x86)"):
            if os.environ.get(var):
                candidates.append(str(Path(os.environ[var]) / "Bambu Studio" / "bambu-studio.exe"))
        if os.environ.get("LOCALAPPDATA"):
            candidates.append(str(Path(os.environ["LOCALAPPDATA"]) / "Programs" / "Bambu Studio" / "bambu-studio.exe"))
        for c in candidates:
            if c and Path(c).is_file():
                return Launcher(c, [c])
    elif sys.platform == "darwin":
        for app in (Path("/Applications/BambuStudio.app"), Path.home() / "Applications" / "BambuStudio.app"):
            if app.is_dir():
                return Launcher(str(app), ["open", "-a", str(app)])
    else:
        for name in ("bambu-studio", "BambuStudio"):
            exe = shutil.which(name)
            if exe:
                return Launcher(exe, [exe])
        flatpak = shutil.which("flatpak")
        if flatpak:
            try:
                ok = subprocess.run([flatpak, "info", FLATPAK_ID], capture_output=True, timeout=10).returncode == 0
            except (OSError, subprocess.TimeoutExpired):
                ok = False
            if ok:
                return Launcher(f"Flatpak {FLATPAK_ID}", [flatpak, "run", FLATPAK_ID])
    return None


def _spawn(command: list[str]) -> None:
    """Start the program without waiting for it and without tying it to this process."""
    kwargs: dict = {"stdin": subprocess.DEVNULL, "stdout": subprocess.DEVNULL, "stderr": subprocess.DEVNULL,
                    "close_fds": True}
    if sys.platform == "win32":
        kwargs["creationflags"] = subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP
    else:
        kwargs["start_new_session"] = True
    subprocess.Popen(command, **kwargs)  # noqa: S603 - list form, no shell; program checked by looks_like_bambu


def open_in_bambu_studio(launcher: Launcher, files: list[Path]) -> None:
    _spawn(launcher.command + [str(f) for f in files])
