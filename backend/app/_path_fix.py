"""Inject common Windows tool locations into ``os.environ['PATH']`` at startup.

When uvicorn is launched from a terminal that was opened *before* the user added entries to their
PATH (via System Settings or the registry), those entries are missing from the inherited
environment, and ``shutil.which()`` then fails to find exiftool / ffmpeg / ollama even though they
are installed. This module re-adds the usual install locations so startup works regardless of how
the server was launched.

Locations are derived from the current user's home directory and standard install roots — never
hard-coded to one machine. Anything that doesn't exist is silently skipped, and ``PC_TOOL_DIRS``
lets you add your own (``os.pathsep``-separated) without touching code.
"""
from __future__ import annotations

import logging
import os
from pathlib import Path

log = logging.getLogger(__name__)


def candidate_tool_dirs() -> list[Path]:
    """Directories that commonly hold exiftool, ffmpeg and ollama on Windows."""
    home = Path.home()
    local_appdata = Path(os.environ.get("LOCALAPPDATA", home / "AppData" / "Local"))
    program_files = Path(os.environ.get("ProgramFiles", r"C:\Program Files"))
    program_files_x86 = Path(os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)"))

    dirs = [
        home / "Documents" / "tools",              # exiftool, unzipped by hand
        home / "bin",
        home / "tools",
        local_appdata / "Programs" / "Ollama",     # default Ollama install
        program_files / "ffmpeg" / "bin",
        program_files_x86 / "ffmpeg" / "bin",
        Path(r"C:\ffmpeg\bin"),
        Path(r"C:\tools"),
    ]

    # Escape hatch: PC_TOOL_DIRS=D:\apps\ffmpeg\bin;E:\exiftool
    extra = os.environ.get("PC_TOOL_DIRS", "")
    dirs += [Path(p.strip()) for p in extra.split(os.pathsep) if p.strip()]
    return dirs


def ensure_tool_paths() -> None:
    """Append any missing known-tool directories to ``os.environ['PATH']``."""
    current = os.environ.get("PATH", "")
    parts = [p.strip() for p in current.split(os.pathsep) if p.strip()]
    existing = {p.lower() for p in parts}
    added: list[str] = []

    for d in candidate_tool_dirs():
        try:
            if not d.exists():
                continue
        except OSError:      # unreadable drive / permission quirk — just skip it
            continue
        s = str(d)
        if s.lower() in existing:
            continue
        parts.append(s)
        existing.add(s.lower())
        added.append(s)

    if added:
        os.environ["PATH"] = os.pathsep.join(parts)
        log.info("PATH fix: added %d dir(s): %s", len(added), ", ".join(added))
    else:
        log.debug("PATH fix: no new dirs needed")
