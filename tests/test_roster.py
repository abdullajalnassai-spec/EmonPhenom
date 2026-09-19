import json

import pytest

from emonphenom import roster


@pytest.fixture
def fake_roster(tmp_path):
    (tmp_path / "sales-deal-closer.md").write_text(
        "---\nname: Deal Closer\ndescription: Closes complex deals\n"
        "emoji: 🤝\nvibe: Never leaves margin on the table.\n---\n\nBody.\n",
        encoding="utf-8",
    )
    (tmp_path / "finance-controller.md").write_text(
        "---\nname: Controller\ndescription: Owns the month-end close\n---\n\nBody.\n",
        encoding="utf-8",
    )
    (tmp_path / "not-an-agent.md").write_text("no frontmatter here\n", encoding="utf-8")
    (tmp_path / "index.json").write_text(
        json.dumps({
            "sales-deal-closer": {"division": "sales", "name": "Deal Closer"},
            "finance-controller": {"division": "finance", "name": "Controller"},
        }),
        encoding="utf-8",
    )
    return tmp_path


def test_loads_only_files_with_frontmatter(fake_roster):
    agents = roster.load(fake_roster)
    assert [a.slug for a in agents] == ["finance-controller", "sales-deal-closer"]


def test_reads_frontmatter_fields(fake_roster):
    agent = roster.get("sales-deal-closer", fake_roster)
    assert agent.name == "Deal Closer"
    assert agent.division == "sales"
    assert agent.emoji == "🤝"
    assert "margin" in agent.vibe
    assert "Deal Closer" in agent.brief()


def test_division_falls_back_when_unindexed(tmp_path):
    (tmp_path / "loose.md").write_text("---\nname: Loose\n---\n", encoding="utf-8")
    assert roster.load(tmp_path)[0].division == "unfiled"


def test_search_puts_name_matches_first(fake_roster):
    hits = roster.search("closes", fake_roster)
    assert [a.slug for a in hits] == ["sales-deal-closer"]
    assert roster.search("controller", fake_roster)[0].name == "Controller"
    assert roster.search("nothing here", fake_roster) == []


def test_division_helpers(fake_roster):
    assert roster.divisions(fake_roster) == {"finance": 1, "sales": 1}
    assert [a.name for a in roster.by_division("SALES", fake_roster)] == ["Deal Closer"]


def test_missing_directory_is_not_an_error(tmp_path):
    assert roster.load(tmp_path / "nope") == ()


def test_unknown_slug_raises(fake_roster):
    with pytest.raises(KeyError):
        roster.get("no-such-agent", fake_roster)


def test_advisors_map_an_intent_to_divisions(fake_roster):
    assert [a.name for a in roster.advisors_for("quote", directory=fake_roster)] == [
        "Deal Closer", "Controller",
    ]
    assert roster.advisors_for("unknown-intent", directory=fake_roster) == []


def test_the_shipped_roster_loads():
    """The real .claude/agents/ directory in this repository."""
    agents = roster.load()
    assert len(agents) == 279
    assert all(a.name for a in agents)
    assert "engineering" in roster.divisions()
