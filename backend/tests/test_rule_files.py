"""Reading, writing and deleting the operator's own rule files.

The path tests are the ones that matter. Every read, write and delete the API
performs goes through user_rule_path, so it is the only thing standing between
"save my rule" and "write anywhere on the host".
"""

from __future__ import annotations

import pytest

from app.config import get_settings
from app.rules.loader import RuleFileError, load_catalog, user_rule_path


@pytest.fixture
def rules_dir(tmp_path, monkeypatch):
    directory = tmp_path / "rules.d"
    directory.mkdir()
    monkeypatch.setenv("RULES_DIR", str(directory))
    get_settings.cache_clear()
    load_catalog.cache_clear()
    yield directory
    get_settings.cache_clear()
    load_catalog.cache_clear()


@pytest.mark.parametrize(
    "name",
    [
        "../escape.yaml",
        "../../etc/cron.d/evil.yaml",
        "sub/dir.yaml",
        "sub\\dir.yaml",
        "/etc/passwd.yaml",
        "..yaml",
        ".hidden.yaml",
        "_overrides.yaml",       # reserved: the loader reads it as data
        "_constants.yaml",
        "rules.yml",             # only .yaml
        "rules.yaml.bak",
        "rules",                 # no extension
        "a" * 80 + ".yaml",      # unbounded length
        "",
        "rules.yaml\x00.txt",    # null byte
        "rule s.yaml",           # space
    ],
)
def test_unsafe_filenames_are_refused(rules_dir, name):
    with pytest.raises(RuleFileError):
        user_rule_path(name)


@pytest.mark.parametrize("name", ["mine.yaml", "my-rules.yaml", "my_rules.yaml", "a1.yaml"])
def test_reasonable_filenames_are_accepted(rules_dir, name):
    assert user_rule_path(name).parent == rules_dir.resolve()


def test_a_symlink_out_of_the_directory_is_refused(rules_dir, tmp_path):
    """The name pattern allows this one; the resolved-parent check is what stops it."""
    outside = tmp_path / "outside"
    outside.mkdir()
    (rules_dir / "escape.yaml").symlink_to(outside / "target.yaml")
    with pytest.raises(RuleFileError):
        user_rule_path("escape.yaml")


def test_writing_needs_a_configured_directory(monkeypatch):
    monkeypatch.setenv("RULES_DIR", "")
    get_settings.cache_clear()
    load_catalog.cache_clear()
    try:
        with pytest.raises(RuleFileError, match="not configured"):
            user_rule_path("mine.yaml")
    finally:
        get_settings.cache_clear()
        load_catalog.cache_clear()


# --- local overrides -------------------------------------------------------


async def test_disabling_a_builtin_locally_stops_it_running(rules_dir, snapshot):
    """The point of overrides: switch a shipped check off without editing it."""
    from app.rules import run_rules
    from app.rules.loader import save_overrides

    findings, _ = run_rules(snapshot)
    assert "wifi.dfs_channel" in {f.rule_id for f in findings}

    save_overrides({"wifi.dfs_channel"})
    load_catalog.cache_clear()

    catalog = load_catalog()
    assert "wifi.dfs_channel" not in {r.id for r in catalog.rules}
    off = next(d for d in catalog.disabled if d.entry.id == "wifi.dfs_channel")
    # Distinguished from enabled:false so the UI knows this one is undoable.
    assert off.reason == "override"

    findings, _ = run_rules(snapshot)
    assert "wifi.dfs_channel" not in {f.rule_id for f in findings}


async def test_re_enabling_a_builtin_brings_it_back(rules_dir, snapshot):
    from app.rules import run_rules
    from app.rules.loader import save_overrides

    save_overrides({"wifi.dfs_channel"})
    load_catalog.cache_clear()
    assert "wifi.dfs_channel" not in {f.rule_id for f in run_rules(snapshot)[0]}

    save_overrides(set())
    load_catalog.cache_clear()
    assert "wifi.dfs_channel" in {f.rule_id for f in run_rules(snapshot)[0]}


def test_the_overrides_file_is_not_read_as_a_rule_file(rules_dir):
    """It lives in RULES_DIR but is data, not rules -- the underscore excludes it."""
    from app.rules.loader import save_overrides

    save_overrides({"wifi.dfs_channel"})
    load_catalog.cache_clear()
    # It parsed as overrides, and did not show up as an unloadable rule file.
    assert load_catalog().problems == []


def test_an_unreadable_overrides_file_disables_nothing(rules_dir):
    """A broken overrides file must not take the scan down with it."""
    from app.rules.loader import OVERRIDES_FILE, load_overrides

    (rules_dir / OVERRIDES_FILE).write_text("disabled: [ broken")
    assert load_overrides() == set()


def test_overrides_round_trip_through_yaml(rules_dir):
    from app.rules.loader import load_overrides, save_overrides

    save_overrides({"wifi.dfs_channel", "device.offline"})
    assert load_overrides() == {"wifi.dfs_channel", "device.offline"}


# --- the API surface -------------------------------------------------------

RULE = """rules:
  - id: custom.spare_port
    kind: declarative
    category: wired
    emits:
      - source: device_ports
        where: [port_state, eq, DOWN]
        severity: info
        title: "{device_name} port {port_idx} is down"
        summary: "No link."
        recommendation: "Fine if unused."
"""


@pytest.fixture
async def api(db, rules_dir):
    import httpx

    from app.main import create_app

    transport = httpx.ASGITransport(app=create_app())
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        yield client


async def test_saving_a_rule_makes_it_run(api, rules_dir):
    body = (await api.put("/api/rules/files/mine.yaml", json={"content": RULE})).json()
    assert body["saved"] is True
    assert (rules_dir / "mine.yaml").read_text() == RULE
    assert "custom.spare_port" in {r["id"] for r in body["catalog"]["rules"]}


async def test_an_invalid_rule_is_never_written(api, rules_dir):
    """The file on disk must not be left in a state the next scan trips over."""
    await api.put("/api/rules/files/mine.yaml", json={"content": RULE})
    bad = RULE.replace("port_state", "prot_state")

    body = (await api.put("/api/rules/files/mine.yaml", json={"content": bad})).json()
    assert body["saved"] is False
    assert body["errors"]
    assert (rules_dir / "mine.yaml").read_text() == RULE


async def test_editing_a_file_does_not_collide_with_itself(api, rules_dir):
    """Overwriting a file must not report its own ids as duplicates."""
    await api.put("/api/rules/files/mine.yaml", json={"content": RULE})
    edited = RULE.replace("severity: info", "severity: low")

    body = (await api.put("/api/rules/files/mine.yaml", json={"content": edited})).json()
    assert body["saved"] is True, body["errors"]


async def test_a_second_file_reusing_an_id_is_rejected(api, rules_dir):
    await api.put("/api/rules/files/one.yaml", json={"content": RULE})
    body = (await api.put("/api/rules/files/two.yaml", json={"content": RULE})).json()
    assert body["saved"] is False
    assert "duplicate" in body["errors"][0]["message"]
    assert not (rules_dir / "two.yaml").exists()


@pytest.mark.parametrize("name", ["_overrides.yaml", "notes.txt"])
async def test_the_api_refuses_names_it_will_not_write(api, name):
    """Names that reach the handler are rejected by the path resolver."""
    resp = await api.put(f"/api/rules/files/{name}", json={"content": RULE})
    assert resp.status_code == 400


@pytest.mark.parametrize("name", ["sub%2Fdir.yaml", "..%2F..%2Fevil.yaml", "%2Fetc%2Fx.yaml"])
async def test_encoded_separators_never_reach_the_handler(api, name):
    """Routing rejects these before the resolver sees them, which is fine --
    what matters is that no request shape reaches the filesystem."""
    resp = await api.put(f"/api/rules/files/{name}", json={"content": RULE})
    assert resp.status_code in (400, 404)


async def test_files_round_trip_for_editing(api, rules_dir):
    await api.put("/api/rules/files/mine.yaml", json={"content": RULE})
    body = (await api.get("/api/rules/files")).json()
    assert [f["name"] for f in body["files"]] == ["mine.yaml"]
    assert body["files"][0]["content"] == RULE


async def test_the_overrides_file_is_not_listed_as_editable(api, rules_dir):
    """It lives in the same directory but is settings, not rules."""
    await api.post("/api/rules/overrides",
                   json={"rule_id": "wifi.dfs_channel", "disabled": True})
    body = (await api.get("/api/rules/files")).json()
    assert body["files"] == []


async def test_deleting_a_rule_file_stops_its_rules(api, rules_dir):
    await api.put("/api/rules/files/mine.yaml", json={"content": RULE})
    body = (await api.delete("/api/rules/files/mine.yaml")).json()
    assert "custom.spare_port" not in {r["id"] for r in body["catalog"]["rules"]}
    assert not (rules_dir / "mine.yaml").exists()


async def test_deleting_something_that_is_not_there_is_404(api, rules_dir):
    assert (await api.delete("/api/rules/files/nope.yaml")).status_code == 404


async def test_toggling_a_builtin_off_and_on(api, rules_dir):
    off = (await api.post("/api/rules/overrides",
                          json={"rule_id": "wifi.dfs_channel", "disabled": True})).json()
    rule = next(r for r in off["catalog"]["rules"] if r["id"] == "wifi.dfs_channel")
    assert rule["status"] == "disabled"

    on = (await api.post("/api/rules/overrides",
                         json={"rule_id": "wifi.dfs_channel", "disabled": False})).json()
    rule = next(r for r in on["catalog"]["rules"] if r["id"] == "wifi.dfs_channel")
    assert rule["status"] == "active"


async def test_overriding_an_unknown_rule_is_404(api, rules_dir):
    resp = await api.post("/api/rules/overrides",
                          json={"rule_id": "no.such.rule", "disabled": True})
    assert resp.status_code == 404


async def test_the_api_reports_no_repo_links(api):
    """Rules are configured on this machine; a repo URL is not an identity."""
    import json

    assert "github" not in json.dumps((await api.get("/api/rules")).json()).lower()


async def test_paths_are_relative_so_they_mean_the_same_everywhere(api, rules_dir):
    """An absolute path is specific to one install -- a different checkout, a
    different RULES_DIR, or a container where it names a file with no host
    counterpart. The identity of a rule file has to be portable."""
    await api.put("/api/rules/files/mine.yaml", json={"content": RULE})
    body = (await api.get("/api/rules")).json()

    for rule in body["rules"]:
        path = rule["source_file"]["path"]
        assert not path.startswith("/"), f"{rule['id']} reports an absolute path"
        assert str(rules_dir) not in path
        assert rule["source_file"]["base"] in ("app", "rules_dir")

    builtin = next(r for r in body["rules"] if r["id"] == "wifi.dfs_channel")
    assert builtin["source_file"]["path"] == "app/rules/catalog/02-wifi.yaml"
    assert builtin["source_file"]["base"] == "app"
    assert builtin["source_file"]["editable"] is False

    mine = next(r for r in body["rules"] if r["id"] == "custom.spare_port")
    assert mine["source_file"]["path"] == "mine.yaml"
    assert mine["source_file"]["base"] == "rules_dir"
    assert mine["source_file"]["editable"] is True


async def test_python_impl_paths_are_relative_too(api):
    body = (await api.get("/api/rules")).json()
    impl = next(r["impl"] for r in body["rules"] if r.get("impl"))
    assert not impl["path"].startswith("/")
    assert impl["path"].startswith("app/rules/")
