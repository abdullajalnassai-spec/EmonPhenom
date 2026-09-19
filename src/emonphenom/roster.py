"""The specialist roster shipped in `.claude/agents/`.

Two consumers, one set of files:

* **Claude Code** discovers `.claude/agents/*.md` automatically, so anyone who
  clones this repository can delegate to any of these specialists by name.
* **This module** reads the same files, so Python code can list, search, and
  recommend them -- e.g. to suggest who should look at a quote that tripped the
  margin floor.

The roster is advisory. Nothing here prices, reserves, or ships anything; the
domain core in `pricing`, `inventory`, `fulfillment` and `ledger` remains the
only thing that decides money or stock.
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

_FRONTMATTER = re.compile(r"\A---\r?\n(.*?)\r?\n---", re.S)


def agents_dir() -> Path:
    """Where the roster lives. `EMONPHENOM_AGENTS_DIR` overrides for testing."""
    override = os.environ.get("EMONPHENOM_AGENTS_DIR")
    if override:
        return Path(override)
    # src/emonphenom/roster.py -> repository root
    return Path(__file__).resolve().parents[2] / ".claude" / "agents"


@dataclass(frozen=True)
class RosterAgent:
    slug: str
    name: str
    division: str
    description: str
    emoji: str
    vibe: str
    path: Path

    def matches(self, term: str) -> bool:
        term = term.strip().lower()
        if not term:
            return False
        return any(
            term in field.lower()
            for field in (self.slug, self.name, self.division, self.description, self.vibe)
        )

    def brief(self) -> str:
        head = f"{self.emoji + ' ' if self.emoji else ''}{self.name}"
        return f"{head} [{self.division}] -- {self.description[:96]}"


def _field(frontmatter: str, key: str) -> str:
    match = re.search(rf"^{re.escape(key)}:\s*(.+)$", frontmatter, re.M)
    return match.group(1).strip().strip("\"'") if match else ""


def _parse(path: Path, divisions: dict[str, dict[str, str]]) -> RosterAgent | None:
    text = path.read_text(encoding="utf-8")
    match = _FRONTMATTER.match(text)
    if not match:
        return None
    fm = match.group(1)
    name = _field(fm, "name")
    if not name:
        return None
    slug = path.stem
    return RosterAgent(
        slug=slug,
        name=name,
        division=divisions.get(slug, {}).get("division", "unfiled"),
        description=_field(fm, "description"),
        emoji=_field(fm, "emoji"),
        vibe=_field(fm, "vibe"),
        path=path,
    )


@lru_cache(maxsize=8)
def _load_cached(directory: str) -> tuple[RosterAgent, ...]:
    root = Path(directory)
    if not root.is_dir():
        return ()
    index_file = root / "index.json"
    divisions: dict[str, dict[str, str]] = {}
    if index_file.is_file():
        try:
            payload = json.loads(index_file.read_text(encoding="utf-8"))
            # The synced index nests under "agents" beside a "_meta" block;
            # a hand-written flat mapping is still accepted.
            divisions = payload.get("agents", payload) if isinstance(payload, dict) else {}
        except json.JSONDecodeError:
            divisions = {}
    found = (_parse(p, divisions) for p in sorted(root.glob("*.md")))
    return tuple(a for a in found if a is not None)


def load(directory: Path | str | None = None) -> tuple[RosterAgent, ...]:
    """Every specialist on the roster, sorted by slug. Missing directory -> ()."""
    return _load_cached(str(Path(directory) if directory else agents_dir()))


def divisions(directory: Path | str | None = None) -> dict[str, int]:
    counts: dict[str, int] = {}
    for agent in load(directory):
        counts[agent.division] = counts.get(agent.division, 0) + 1
    return dict(sorted(counts.items()))


def search(term: str, directory: Path | str | None = None) -> list[RosterAgent]:
    """Roster search, name matches ahead of description matches."""
    term_l = term.strip().lower()
    hits = [a for a in load(directory) if a.matches(term_l)]
    hits.sort(key=lambda a: (term_l not in a.name.lower(), a.name))
    return hits


def by_division(division: str, directory: Path | str | None = None) -> list[RosterAgent]:
    wanted = division.strip().lower()
    return [a for a in load(directory) if a.division.lower() == wanted]


def get(slug: str, directory: Path | str | None = None) -> RosterAgent:
    for agent in load(directory):
        if agent.slug == slug:
            return agent
    raise KeyError(f"no roster agent {slug!r}")


# Which divisions are worth consulting about a given kind of business question.
# Used to suggest a second opinion, never to make the decision.
ADVISORY_DIVISIONS: dict[str, tuple[str, ...]] = {
    "quote": ("sales", "finance"),
    "order": ("sales", "support"),
    "stock": ("product", "strategy"),
    "finance": ("finance", "strategy"),
    "catalog": ("marketing", "product"),
}


def advisors_for(intent: str, limit: int = 3,
                 directory: Path | str | None = None) -> list[RosterAgent]:
    """Roster specialists worth consulting about this kind of request."""
    out: list[RosterAgent] = []
    for division in ADVISORY_DIVISIONS.get(intent, ()):
        out.extend(by_division(division, directory))
    return out[:limit]
