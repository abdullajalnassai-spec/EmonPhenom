"""Install the specialist roster from the Agents repository.

The roster is a tool for working on this project, not a dependency of its code:
nothing under `pricing`, `inventory`, `fulfillment` or `simulation` imports it,
and the test suite does not need it. So it is not vendored here. This module
fetches it on demand and installs the flat `.claude/agents/*.md` files Claude
Code reads, recording which commit they came from.

`.claude/agents/` is gitignored -- the roster never enters this repository's
history.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

DEFAULT_SOURCE = "https://github.com/abdullajalnassai-spec/Agents"

# Top-level directories in the source repository that hold no agent definitions.
NON_DIVISION_DIRS = frozenset(
    {"scripts", "integrations", "examples", ".git", ".github", ".claude"}
)

_FRONTMATTER = re.compile(r"\A---\r?\n(.*?)\r?\n---", re.S)
_NAME = re.compile(r"^name:\s*(.+)$", re.M)


class SyncError(RuntimeError):
    """Anything that stops the roster being installed."""


@dataclass(frozen=True)
class SyncReport:
    installed: int
    divisions: int
    removed: int
    source: str
    commit: str
    ref: str
    dest: Path

    def describe(self) -> str:
        lines = [
            f"installed {self.installed} agents across {self.divisions} divisions",
            f"  from {self.source} @ {self.ref} ({self.commit[:8]})",
            f"  into {self.dest}",
        ]
        if self.removed:
            lines.append(f"  removed {self.removed} stale file(s)")
        return "\n".join(lines)


@dataclass(frozen=True)
class Installed:
    count: int
    divisions: int
    source: str
    commit: str
    ref: str
    synced_at: str
    dest: Path

    def describe(self) -> str:
        return (
            f"{self.count} agents across {self.divisions} divisions\n"
            f"  source  {self.source}\n"
            f"  ref     {self.ref} ({self.commit[:8]})\n"
            f"  synced  {self.synced_at}\n"
            f"  path    {self.dest}"
        )


def default_dest() -> Path:
    """Where Claude Code looks for project agents."""
    return Path(__file__).resolve().parents[2] / ".claude" / "agents"


def cache_dir() -> Path:
    """Where the source repository is kept between syncs."""
    override = os.environ.get("EMONPHENOM_AGENT_CACHE")
    if override:
        return Path(override)
    base = os.environ.get("XDG_CACHE_HOME") or (Path.home() / ".cache")
    return Path(base) / "emonphenom" / "agents-src"


def _git(*args: str, cwd: Path | None = None) -> str:
    try:
        done = subprocess.run(
            ["git", *args],
            cwd=str(cwd) if cwd else None,
            capture_output=True,
            text=True,
            check=False,
        )
    except FileNotFoundError:
        raise SyncError("git is not installed, so the roster cannot be fetched") from None
    if done.returncode != 0:
        detail = (done.stderr or done.stdout).strip().splitlines()
        raise SyncError(
            f"git {' '.join(args)} failed: {detail[-1] if detail else 'unknown error'}"
        )
    return done.stdout.strip()


def fetch_source(
    source: str = DEFAULT_SOURCE, ref: str | None = None, cache: Path | None = None
) -> tuple[Path, str]:
    """Clone or update the source repository. Returns (checkout path, commit)."""
    root = cache or cache_dir()
    root.parent.mkdir(parents=True, exist_ok=True)

    if not (root / ".git").is_dir():
        if root.exists() and any(root.iterdir()):
            raise SyncError(f"{root} exists and is not a clone; remove it or use --cache")
        _git("clone", "--depth", "1", source, str(root))

    _git("remote", "set-url", "origin", source, cwd=root)
    _git("fetch", "--depth", "1", "origin", ref or "HEAD", cwd=root)
    _git("checkout", "-q", "--detach", "FETCH_HEAD", cwd=root)
    return root, _git("rev-parse", "HEAD", cwd=root)


def collect(tree: Path) -> dict[str, tuple[Path, str, str]]:
    """Find every agent definition in a source tree.

    Returns slug -> (path, division, display name). A markdown file only counts
    as an agent if it opens with frontmatter carrying a `name`.
    """
    found: dict[str, tuple[Path, str, str]] = {}
    if not tree.is_dir():
        raise SyncError(f"{tree} is not a directory")

    for division_dir in sorted(p for p in tree.iterdir() if p.is_dir()):
        if division_dir.name in NON_DIVISION_DIRS or division_dir.name.startswith("."):
            continue
        for path in sorted(division_dir.rglob("*.md")):
            try:
                text = path.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                continue
            head = _FRONTMATTER.match(text)
            if not head:
                continue
            name = _NAME.search(head.group(1))
            if not name:
                continue
            slug = path.stem
            # First division wins, so a repeated slug cannot silently reassign.
            found.setdefault(
                slug, (path, division_dir.name, name.group(1).strip().strip("\"'"))
            )
    return found


def install(
    tree: Path,
    dest: Path,
    *,
    source: str = DEFAULT_SOURCE,
    commit: str = "unknown",
    ref: str = "HEAD",
    prune: bool = True,
) -> SyncReport:
    """Flatten a source tree into the flat layout Claude Code reads."""
    agents = collect(tree)
    if not agents:
        raise SyncError(f"no agent definitions found under {tree}")

    dest.mkdir(parents=True, exist_ok=True)
    wanted = {f"{slug}.md" for slug in agents}

    removed = 0
    if prune:
        for stale in dest.glob("*.md"):
            if stale.name not in wanted:
                stale.unlink()
                removed += 1

    index: dict[str, dict[str, str]] = {}
    for slug, (path, division, name) in sorted(agents.items()):
        shutil.copy2(path, dest / f"{slug}.md")
        index[slug] = {"division": division, "name": name}

    payload = {
        "_meta": {
            "source": source,
            "commit": commit,
            "ref": ref,
            "synced_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "count": len(index),
        },
        "agents": index,
    }
    (dest / "index.json").write_text(
        json.dumps(payload, indent=1, ensure_ascii=False), encoding="utf-8"
    )

    return SyncReport(
        installed=len(index),
        divisions=len({v["division"] for v in index.values()}),
        removed=removed,
        source=source,
        commit=commit,
        ref=ref,
        dest=dest,
    )


def sync(
    *,
    source: str = DEFAULT_SOURCE,
    ref: str | None = None,
    dest: Path | None = None,
    cache: Path | None = None,
) -> SyncReport:
    """Fetch the roster and install it. The one call the CLI wraps."""
    tree, commit = fetch_source(source, ref, cache)
    return install(
        tree, dest or default_dest(), source=source, commit=commit, ref=ref or "HEAD"
    )


def status(dest: Path | None = None) -> Installed | None:
    """What is installed, and where it came from. None if nothing is."""
    target = dest or default_dest()
    index_file = target / "index.json"
    if not index_file.is_file():
        return None
    try:
        payload = json.loads(index_file.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None

    agents = payload.get("agents", payload)
    meta = payload.get("_meta", {})
    return Installed(
        count=len(agents),
        divisions=len({a.get("division", "unfiled") for a in agents.values()}),
        source=meta.get("source", "unknown"),
        commit=meta.get("commit", "unknown"),
        ref=meta.get("ref", "unknown"),
        synced_at=meta.get("synced_at", "unknown"),
        dest=target,
    )
