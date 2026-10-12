"""Strip trailing whitespace and ensure a trailing newline (pre-commit hook).

Runs offline with the standard library only: remote hook repos cannot be
cloned on this machine.
"""

from __future__ import annotations

import sys
from pathlib import Path

TEXT_SUFFIXES = {
    ".py",
    ".toml",
    ".yaml",
    ".yml",
    ".json",
    ".md",
    ".txt",
    ".cfg",
    ".ini",
    ".ps1",
    ".js",
    ".vue",
    "",
}


def clean(path: Path) -> bool:
    try:
        original = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return False

    lines = [line.rstrip() for line in original.split("\n")]
    fixed = "\n".join(lines).rstrip("\n") + "\n"
    if fixed == original:
        return False
    path.write_text(fixed, encoding="utf-8")
    return True


def main(argv: list[str]) -> int:
    changed: list[str] = []
    for name in argv:
        path = Path(name)
        if not path.is_file() or path.suffix.lower() not in TEXT_SUFFIXES:
            continue
        if clean(path):
            changed.append(name)
    if changed:
        print(f"whitespace fixed in {len(changed)} file(s):")
        for name in changed:
            print(f"  {name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
