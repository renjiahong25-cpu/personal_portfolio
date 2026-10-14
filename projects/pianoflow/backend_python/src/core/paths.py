"""Project path resolution.

Every path in PianoFlow is derived from the repository root so that the
``D:\\Program Files\\pianoflow`` layout (note the space) never has to be
hard-coded or string-concatenated anywhere else.
"""

from __future__ import annotations

from pathlib import Path

# src/core/paths.py -> src/core -> src -> backend_python -> <repo root>
PROJECT_ROOT: Path = Path(__file__).resolve().parents[3]
BACKEND_ROOT: Path = PROJECT_ROOT / "backend_python"
SRC_ROOT: Path = BACKEND_ROOT / "src"
FRONTEND_ROOT: Path = PROJECT_ROOT / "frontend"
FRONTEND_DIST: Path = FRONTEND_ROOT / "dist"

__all__ = [
    "BACKEND_ROOT",
    "FRONTEND_DIST",
    "FRONTEND_ROOT",
    "PROJECT_ROOT",
    "SRC_ROOT",
]
