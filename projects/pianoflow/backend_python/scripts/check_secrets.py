"""Fail the pre-commit gate when a secret is about to be committed.

Deliberately pattern-based rather than clever: the goal is to catch the obvious
mistakes (a pasted PAT, an API key in a config file) before they reach git
history, where they are expensive to remove.

Exit codes: 0 clean, 1 findings, 2 the check itself is misconfigured.
"""

from __future__ import annotations

import re
import shutil
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]

#: Git is not always on PATH inside hooks; resolve an absolute path so bandit
#: does not flag a partial executable name (B607).
GIT_CANDIDATES = (r"C:\Program Files\Git\cmd\git.exe", r"C:\Program Files\Git\bin\git.exe")


def git_executable() -> str | None:
    found = shutil.which("git")
    if found:
        return found
    for candidate in GIT_CANDIDATES:
        if Path(candidate).is_file():
            return candidate
    return None


#: (pattern, why it matters). Keep patterns tight to avoid crying wolf.
RULES: list[tuple[str, str, str]] = [
    (
        r"QODER_PERSONAL_ACCESS_TOKEN\s*=\s*[\"'][^\"']{8,}[\"']",
        "QODER_PERSONAL_ACCESS_TOKEN",
        "Qoder PAT must live in your environment, not in the repo",
    ),
    (
        r"sk-[A-Za-z0-9]{20,}",
        "OpenAI-style key",
        "looks like a live API key",
    ),
    (
        r"hf_[A-Za-z0-9]{30,}",
        "HuggingFace token",
        "HuggingFace tokens must not be committed",
    ),
    (
        r"gh[pousr]_[A-Za-z0-9]{30,}",
        "GitHub token",
        "GitHub tokens must not be committed",
    ),
    (
        r"AKIA[0-9A-Z]{16}",
        "AWS access key id",
        "AWS credentials must not be committed",
    ),
    (
        r"(?i)cookie:\s*[^\n]{40,}",
        "long cookie string",
        "platform cookies are personal; do not commit them",
    ),
]

#: Paths that may legitimately contain the patterns above.
ALLOWLIST = {
    "scripts/check_secrets.py",
    ".pre-commit-config.yaml",
    "docs/spike-2026-10-04-source-availability.md",
}

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
    ".env",
}


def staged_files() -> list[str]:
    git = git_executable()
    if git is None:
        print("check-secrets: git not found, skipping staged-file scan")
        return []
    try:
        out = subprocess.run(
            [git, "diff", "--cached", "--name-only", "--diff-filter=ACM"],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            check=False,
            timeout=30,
        ).stdout
    except (OSError, subprocess.SubprocessError) as exc:
        print(f"cannot list staged files: {exc}", file=sys.stderr)
        return []
    return [line.strip() for line in out.splitlines() if line.strip()]


def main() -> int:
    candidates = [
        name
        for name in staged_files()
        if Path(name).suffix.lower() in TEXT_SUFFIXES and name.replace("\\", "/") not in ALLOWLIST
    ]
    if not candidates:
        print("check-secrets: no staged text files to inspect")
        return 0

    findings: list[str] = []
    for name in candidates:
        path = REPO_ROOT / name
        try:
            content = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        for pattern, label, why in RULES:
            for match in re.finditer(pattern, content):
                line = content[: match.start()].count("\n") + 1
                findings.append(f"{name}:{line}  {label}: {why}")

    if findings:
        print("check-secrets: BLOCKED", file=sys.stderr)
        for finding in findings:
            print(f"  {finding}", file=sys.stderr)
        print(
            "\nIf this is a false positive, allow it in ALLOWLIST in "
            "backend_python/scripts/check_secrets.py",
            file=sys.stderr,
        )
        return 1

    print(f"check-secrets: {len(candidates)} file(s) clean")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
