"""Dokan driver detection and installation guidance for Windows."""

from __future__ import annotations

import ctypes
import ctypes.util
import logging
import os
import shutil
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)

DOKAN_INSTALL_URL = "https://dokan-dev.github.io/"
DOKAN_WINGET_ID = "Dokan-Dev.Dokany"


@dataclass(frozen=True)
class DokanDriverStatus:
    installed: bool
    version: str | None
    dll_path: str | None
    dokanctl_path: str | None
    fusepy_available: bool
    install_hint: str | None  # None if installed


def check_dokan_driver() -> DokanDriverStatus:
    """Check if Dokan driver is installed and usable."""
    dll_path = _find_dokan_dll()
    dokanctl = _find_dokanctl()
    version = _get_dokan_version(dokanctl) if dokanctl else None
    fusepy = _check_fusepy()

    installed = dll_path is not None
    if not installed:
        hint = (
            f"Dokan driver is not installed.\n"
            f"Install with: winget install {DOKAN_WINGET_ID}\n"
            f"Or download from: {DOKAN_INSTALL_URL}"
        )
    elif not fusepy:
        hint = "Dokan driver found but fusepy is not installed.\nInstall with: pip install fusepy"
    else:
        hint = None

    status = DokanDriverStatus(
        installed=installed,
        version=version,
        dll_path=dll_path,
        dokanctl_path=dokanctl,
        fusepy_available=fusepy,
        install_hint=hint,
    )

    logger.info(
        "dokan.driver_check installed=%s version=%s fusepy=%s",
        installed,
        version,
        fusepy,
    )
    return status


def _find_dokan_dll() -> str | None:
    dll_name = ctypes.util.find_library("dokan2") or "dokan2.dll"
    try:
        lib = ctypes.WinDLL(dll_name)
        return dll_name
    except OSError:
        pass

    # Search common paths
    for base in [
        Path(os.environ.get("ProgramFiles", r"C:\Program Files")),
        Path(os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")),
    ]:
        for dll in base.glob("Dokan*/**/dokan2.dll"):
            try:
                ctypes.WinDLL(str(dll))
                return str(dll)
            except OSError:
                continue
    return None


def _find_dokanctl() -> str | None:
    override = os.environ.get("AI_IDE_DOKANCTL", "").strip()
    if override:
        return override
    for candidate in [shutil.which("dokanctl.exe"), shutil.which("dokanctl")]:
        if candidate:
            return candidate
    for base in [
        Path(os.environ.get("ProgramFiles", r"C:\Program Files")),
        Path(os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")),
    ]:
        for path in base.glob("Dokan*/**/dokanctl.exe"):
            if path.is_file():
                return str(path)
    return None


def _get_dokan_version(dokanctl_path: str) -> str | None:
    try:
        import subprocess
        result = subprocess.run(
            [dokanctl_path, "/v"],
            capture_output=True, text=True, timeout=5,
        )
        for line in result.stdout.splitlines():
            if "version" in line.lower() or "dokan" in line.lower():
                return line.strip()
        return result.stdout.strip()[:100] if result.stdout.strip() else None
    except (OSError, subprocess.TimeoutExpired):
        return None


def _check_fusepy() -> bool:
    try:
        import fuse  # noqa: F401
        return True
    except ImportError:
        return False
