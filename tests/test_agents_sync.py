import json
import subprocess

import pytest

from emonphenom import agents_sync, roster

AGENT = "---\nname: {name}\ndescription: {desc}\n---\n\nBody for {name}.\n"


def make_tree(root):
    """A miniature Agents repository."""
    (root / "sales").mkdir(parents=True)
    (root / "engineering" / "nested").mkdir(parents=True)
    (root / "scripts").mkdir()
    (root / "sales" / "deal-closer.md").write_text(
        AGENT.format(name="Deal Closer", desc="Closes deals"), encoding="utf-8")
    (root / "engineering" / "nested" / "api-builder.md").write_text(
        AGENT.format(name="API Builder", desc="Builds APIs"), encoding="utf-8")
    (root / "engineering" / "no-frontmatter.md").write_text("just prose\n", encoding="utf-8")
    (root / "engineering" / "no-name.md").write_text(
        "---\ndescription: nameless\n---\n", encoding="utf-8")
    (root / "scripts" / "install.md").write_text(
        AGENT.format(name="Not An Agent", desc="tooling"), encoding="utf-8")
    (root / "README.md").write_text("# root\n", encoding="utf-8")
    return root


def test_collect_finds_agents_and_skips_everything_else(tmp_path):
    found = agents_sync.collect(make_tree(tmp_path))
    assert set(found) == {"deal-closer", "api-builder"}
    assert found["deal-closer"][1] == "sales"
    assert found["api-builder"][1] == "engineering"   # nested dirs keep the division
    assert found["deal-closer"][2] == "Deal Closer"


def test_collect_rejects_a_non_directory(tmp_path):
    with pytest.raises(agents_sync.SyncError):
        agents_sync.collect(tmp_path / "nope")


def test_install_flattens_and_writes_provenance(tmp_path):
    dest = tmp_path / "out"
    report = agents_sync.install(
        make_tree(tmp_path / "src"), dest, source="somewhere", commit="abc123", ref="main")

    assert report.installed == 2
    assert report.divisions == 2
    assert (dest / "deal-closer.md").is_file()
    assert (dest / "api-builder.md").is_file()
    assert not (dest / "install.md").exists()

    payload = json.loads((dest / "index.json").read_text(encoding="utf-8"))
    assert payload["_meta"]["commit"] == "abc123"
    assert payload["_meta"]["source"] == "somewhere"
    assert payload["_meta"]["count"] == 2
    assert payload["agents"]["deal-closer"]["division"] == "sales"


def test_install_prunes_agents_that_left_the_source(tmp_path):
    dest = tmp_path / "out"
    dest.mkdir()
    (dest / "retired-agent.md").write_text("---\nname: Retired\n---\n", encoding="utf-8")
    report = agents_sync.install(make_tree(tmp_path / "src"), dest)
    assert report.removed == 1
    assert not (dest / "retired-agent.md").exists()


def test_install_can_keep_local_extras(tmp_path):
    dest = tmp_path / "out"
    dest.mkdir()
    (dest / "my-own-agent.md").write_text("---\nname: Mine\n---\n", encoding="utf-8")
    agents_sync.install(make_tree(tmp_path / "src"), dest, prune=False)
    assert (dest / "my-own-agent.md").exists()


def test_install_refuses_an_empty_source(tmp_path):
    (tmp_path / "empty").mkdir()
    with pytest.raises(agents_sync.SyncError, match="no agent definitions"):
        agents_sync.install(tmp_path / "empty", tmp_path / "out")


def test_status_is_none_before_a_sync(tmp_path):
    assert agents_sync.status(tmp_path / "missing") is None


def test_status_survives_a_corrupt_index(tmp_path):
    tmp_path.joinpath("index.json").write_text("{not json", encoding="utf-8")
    assert agents_sync.status(tmp_path) is None


def test_status_reports_what_was_installed(tmp_path):
    dest = tmp_path / "out"
    agents_sync.install(make_tree(tmp_path / "src"), dest, commit="deadbeef", ref="v1")
    installed = agents_sync.status(dest)
    assert installed.count == 2
    assert installed.commit == "deadbeef"
    assert installed.ref == "v1"
    assert "deadbeef"[:8] in installed.describe()


def test_roster_reads_the_synced_index(tmp_path):
    dest = tmp_path / "out"
    agents_sync.install(make_tree(tmp_path / "src"), dest)
    loaded = roster.load(dest)
    assert {a.slug for a in loaded} == {"api-builder", "deal-closer"}
    assert roster.get("deal-closer", dest).division == "sales"


def test_sync_end_to_end_against_a_local_repository(tmp_path):
    """No network: clone a git repo created on disk."""
    origin = make_tree(tmp_path / "origin")
    git = ["git", "-c", "user.email=t@example.com", "-c", "user.name=Test"]
    subprocess.run(["git", "init", "-q", str(origin)], check=True)
    subprocess.run([*git, "-C", str(origin), "add", "-A"], check=True)
    subprocess.run([*git, "-C", str(origin), "commit", "-q", "-m", "seed"], check=True)

    report = agents_sync.sync(
        source=str(origin), dest=tmp_path / "dest", cache=tmp_path / "cache")

    assert report.installed == 2
    assert len(report.commit) == 40
    assert (tmp_path / "dest" / "deal-closer.md").is_file()
    assert agents_sync.status(tmp_path / "dest").commit == report.commit

    # a second sync is a fetch, not a fresh clone, and stays correct
    again = agents_sync.sync(
        source=str(origin), dest=tmp_path / "dest", cache=tmp_path / "cache")
    assert again.installed == 2
    assert again.commit == report.commit


def test_sync_reports_a_bad_source_clearly(tmp_path):
    with pytest.raises(agents_sync.SyncError):
        agents_sync.sync(source=str(tmp_path / "not-a-repo"),
                         dest=tmp_path / "d", cache=tmp_path / "c")


def test_cache_dir_is_overridable(monkeypatch, tmp_path):
    monkeypatch.setenv("EMONPHENOM_AGENT_CACHE", str(tmp_path / "somewhere"))
    assert agents_sync.cache_dir() == tmp_path / "somewhere"
