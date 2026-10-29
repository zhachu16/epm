from __future__ import annotations

import shutil
from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest

from epm import EpmError
from epm.package import read_package_bytes
from service import config
from service.projects import EXAMPLE_DIR, EXAMPLE_ID, Projects
from service.store import MemoryBlobStore, MemoryMetaStore

TAX = {"op": "update", "kind": "metric", "name": "tax_rate", "set": {"value": 0.3}}


def irr(projects: Projects, project_id: str) -> float:
    return projects.run(project_id, outputs=["equity_irr"])["values"]["equity_irr"]["value"]


def tax(projects: Projects, project_id: str) -> float:
    return projects.show(project_id, "tax_rate")["value"]


# -- the example ----------------------------------------------------------------------------


def test_example_is_seeded_once(projects):
    example = projects.get(EXAMPLE_ID)
    assert example["is_example"] and example["current_version"] == 1
    projects.seed_example()  # unchanged files: no new version
    assert projects.get(EXAMPLE_ID)["current_version"] == 1


def test_example_follows_changes_to_its_files(tmp_path, stores):
    example = tmp_path / "lbo"
    shutil.copytree(EXAMPLE_DIR, example, ignore=shutil.ignore_patterns("cache"))
    projects = Projects(*stores, example=example)
    projects.seed_example()
    model = (example / "model.json").read_text().replace('"value": 0.25', '"value": 0.26', 1)
    (example / "model.json").write_text(model)
    projects.seed_example()
    versions = projects.list_versions(EXAMPLE_ID)
    assert [v["source"] for v in versions] == ["seed", "seed"]


@pytest.mark.parametrize(
    "write",
    [
        lambda p: p.apply(EXAMPLE_ID, [TAX]),
        lambda p: p.rename(EXAMPLE_ID, "Mine"),
        lambda p: p.delete(EXAMPLE_ID),
        lambda p: p.upload_file(EXAMPLE_ID, "x.csv", b"a,b\n"),
        lambda p: p.put_functions(EXAMPLE_ID, ""),
        lambda p: p.undo(EXAMPLE_ID),
        lambda p: p.run(EXAMPLE_ID, save=True),
    ],
)
def test_example_is_read_only(projects, write):
    with pytest.raises(EpmError) as e:
        write(projects)
    assert e.value.code == "read_only_project"
    assert projects.get(EXAMPLE_ID)["current_version"] == 1


def test_example_can_be_read_previewed_and_copied(projects):
    assert projects.apply(EXAMPLE_ID, [TAX], mode="dry_run")["ok"]
    assert projects.state(EXAMPLE_ID)["read_only"]
    assert 0 < irr(projects, EXAMPLE_ID) < 1
    copy = projects.create("Mine", from_project=EXAMPLE_ID)
    assert not copy["is_example"] and copy["description"] == "A copy of Example: LBO."
    assert projects.apply(copy["id"], [TAX])["committed"]


# -- projects -------------------------------------------------------------------------------


def test_create_rename_search_delete_and_undelete(projects):
    p = projects.create("Deal model", "for the Atlas deal")
    assert p["id"].startswith("p_") and p["current_version"] == 1
    assert projects.state(p["id"])["name"] == "Deal model"
    projects.rename(p["id"], name="Atlas")
    assert [x["name"] for x in projects.list_projects("atlas")] == ["Atlas"]
    with pytest.raises(EpmError, match="needs a name"):
        projects.rename(p["id"], name=" ")
    projects.delete(p["id"])
    assert projects.list_projects("atlas") == []
    with pytest.raises(EpmError) as e:
        projects.state(p["id"])
    assert e.value.code == "unknown_project"
    assert [x["id"] for x in projects.list_projects(deleted=True)] == [p["id"]]
    projects.restore_project(p["id"])
    assert projects.get(p["id"])["deleted_at"] is None


def test_duplicate_is_independent(projects, atlas):
    projects.apply(atlas, [TAX])
    copy = projects.duplicate(atlas)["id"]
    assert projects.get(copy)["name"] == "Atlas (copy)" and tax(projects, copy) == 0.3
    projects.apply(copy, [{**TAX, "set": {"value": 0.2}}])
    assert tax(projects, atlas) == 0.3


def test_export_and_import_round_trip(projects, atlas):
    projects.apply(atlas, [TAX])
    name, data = projects.export_epm(atlas)
    assert name == "atlas.epm"
    assert read_package_bytes(data).model.metrics["tax_rate"].value == 0.3
    imported = projects.import_epm(data)
    assert imported["name"] == "Project Atlas LBO"  # from the model's settings
    assert tax(projects, imported["id"]) == 0.3
    old_name, old = projects.export_epm(atlas, version=1)
    assert old_name == "atlas-v1.epm"
    assert read_package_bytes(old).model.metrics["tax_rate"].value == 0.25
    with pytest.raises(EpmError) as e:
        projects.import_epm(b"not a zip")
    assert e.value.code == "invalid_package"


# -- edits and versions ----------------------------------------------------------------------


def test_commit_saves_a_version_and_dry_run_does_not(projects, atlas):
    before = irr(projects, atlas)
    preview = projects.apply(atlas, [TAX], mode="dry_run")
    assert preview["ok"] and not preview["committed"] and preview["version"] == 1
    assert projects.get(atlas)["current_version"] == 1
    result = projects.apply(atlas, [TAX], label="higher taxes")
    assert result["committed"] and result["version"] == 2
    assert irr(projects, atlas) < before
    latest = projects.list_versions(atlas)[0]
    assert latest["label"] == "higher taxes" and latest["current"] and latest["ops"] == 1


def test_failed_batch_saves_nothing(projects, atlas):
    bad = {"op": "update", "kind": "metric", "name": "sga", "set": {"formula": "sga_pct * 2"}}
    result = projects.apply(atlas, [bad])
    assert not result["ok"] and result["errors"][0]["code"] == "unknown_reference"
    assert projects.get(atlas)["current_version"] == 1


def test_expected_version_guards_against_lost_updates(projects, atlas):
    projects.apply(atlas, [TAX], expected_version=1)
    with pytest.raises(EpmError) as e:
        projects.apply(atlas, [{**TAX, "set": {"value": 0.2}}], expected_version=1)
    assert e.value.code == "version_conflict" and e.value.detail["current_version"] == 2
    assert tax(projects, atlas) == 0.3


def test_a_save_landing_in_between_is_retried_without_expected_version(projects, atlas, stores):
    meta, _ = stores
    real = meta.commit_version

    def another_replica_saves_first(version, expected):
        meta.commit_version = real
        current = meta.get_version(atlas, expected)
        assert real(replace(current, number=expected + 1, label="elsewhere"), expected)
        return False

    meta.commit_version = another_replica_saves_first
    result = projects.apply(atlas, [TAX])
    assert result["committed"] and result["version"] == 3
    labels = [v["label"] for v in projects.list_versions(atlas)]
    assert labels[:2] == ["update metric 'tax_rate'", "elsewhere"]


def test_undo_walks_back_and_restore_keeps_history(projects, atlas):
    projects.apply(atlas, [{**TAX, "set": {"value": 0.3}}])  # v2
    projects.apply(atlas, [{**TAX, "set": {"value": 0.35}}])  # v3
    assert projects.undo(atlas)["restored"] == 2  # v4 = v2
    assert tax(projects, atlas) == 0.3
    assert projects.undo(atlas)["restored"] == 1  # keeps going back: v5 = v1
    assert tax(projects, atlas) == 0.25
    with pytest.raises(EpmError) as e:
        projects.undo(atlas)
    assert e.value.code == "nothing_to_undo"
    projects.restore(atlas, 3)  # forward again: v6 = v3
    assert tax(projects, atlas) == 0.35
    versions = projects.list_versions(atlas)
    assert [v["number"] for v in versions] == [6, 5, 4, 3, 2, 1]
    assert [v["restored_from"] for v in versions[:3]] == [3, 1, 2]
    with pytest.raises(EpmError, match="already current"):
        projects.restore(atlas, 6)
    with pytest.raises(EpmError) as e:
        projects.restore(atlas, 99)
    assert e.value.code == "unknown_version"


def test_state_of_an_earlier_version_is_read_only(projects, atlas):
    projects.apply(atlas, [TAX])
    old = projects.state(atlas, version=1)
    assert old["version"] == 1 and old["read_only"]
    assert old["model"]["metrics"]["tax_rate"]["value"] == 0.25
    current = projects.state(atlas)
    assert current["version"] == 2 and not current["read_only"]
    assert current["project"]["id"] == atlas


def test_model_and_functions_editors(projects, atlas):
    model = projects.state(atlas)["model"]
    model["metrics"]["tax_rate"]["value"] = 0.3
    assert projects.put_model(atlas, model)["committed"]
    assert projects.put_model(atlas, model)["label"] == "no changes"
    code = projects.state(atlas)["functions_source"]
    result = projects.put_functions(atlas, code + "\n# a comment\n")
    assert result["committed"] and result["version"] == 3


# -- files and outputs -----------------------------------------------------------------------


def test_input_files(projects, atlas):
    names = [f["name"] for f in projects.list_files(atlas)]
    assert "inputs/historicals.xlsx" in names
    data = projects.download_file(atlas, "historicals.xlsx")
    assert data[:2] == b"PK"
    projects.upload_file(atlas, "extra.csv", b"item,2024\nx,1\n")
    assert projects.download_file(atlas, "inputs/extra.csv") == b"item,2024\nx,1\n"
    assert projects.get(atlas)["current_version"] == 2
    with pytest.raises(EpmError) as e:
        projects.download_file(atlas, "missing.csv")
    assert e.value.code == "unknown_file"
    in_use = projects.apply(atlas, [{"op": "delete", "kind": "file", "name": names[0]}])
    assert in_use["errors"][0]["code"] == "in_use"


def test_outputs(projects, atlas):
    run = projects.run(atlas, overrides={"tax_rate": 0.3}, outputs=["equity_irr"], save=True)
    analysis = projects.run_analysis(atlas, "entry_exit_irr", save=True, plot=True)
    sweep = projects.sweep(atlas, {"tax_rate": [0.2, 0.3]}, ["equity_irr"], save=True)
    kinds = sorted(o["kind"] for o in projects.list_outputs(atlas))
    assert kinds == ["analysis", "plot", "run", "sweep"]
    record, png = projects.download_output(atlas, analysis["outputs"][1]["id"])
    assert record["content_type"] == "image/png" and png[:4] == b"\x89PNG"
    _, csv = projects.download_output(atlas, sweep["output"]["id"])
    assert csv.decode().splitlines()[0] == "tax_rate,equity_irr,status,error"
    projects.delete_output(atlas, run["output"]["id"])
    assert len(projects.list_outputs(atlas)) == 3
    assert projects.plot(atlas, "entry_exit_irr")[:4] == b"\x89PNG"


# -- maintenance ------------------------------------------------------------------------------


def test_retention_keeps_recent_and_daily_versions(projects, atlas, stores, monkeypatch):
    meta, blobs = stores
    monkeypatch.setattr(config, "KEEP_VERSIONS", 3)
    now = datetime.now(UTC)
    for i in range(6):
        projects.apply(atlas, [{**TAX, "set": {"value": 0.2 + i / 100}}])
    # spread versions 1-7 over days: 1 and 2 long ago, 3 and 4 on one recent day
    ages = {1: 200, 2: 100, 3: 10, 4: 10, 5: 0, 6: 0, 7: 0}
    for n, days in ages.items():
        meta.versions[atlas][n].created_at = now - timedelta(days=days, minutes=n)
    summary = projects.maintain(now)
    kept = [v["number"] for v in projects.list_versions(atlas)]
    # newest 3, plus the newest of day -10 (4); 1, 2 are past 90 days, 3 is not its day's newest
    assert kept == [7, 6, 5, 4]
    assert summary["pruned_versions"] == 3
    live = set(blobs.keys(f"projects/{atlas}/versions/"))
    assert len(live) == 4
    assert summary["backup"] in {k.split("/")[-1][:10] for k in blobs.keys("backups/meta/")}


def test_deleted_projects_are_purged_after_the_grace_period(projects, atlas, stores):
    meta, blobs = stores
    projects.run(atlas, save=True)
    projects.delete(atlas)
    projects.maintain()
    assert projects.list_projects(deleted=True)  # still restorable
    later = datetime.now(UTC) + timedelta(days=config.KEEP_DELETED_DAYS + 1)
    assert projects.maintain(later)["purged_projects"] == [atlas]
    assert meta.get_project(atlas) is None and blobs.keys(f"projects/{atlas}/") == []


def test_blob_cache_is_bounded(stores):
    meta, blobs = MemoryMetaStore(), MemoryBlobStore()
    projects = Projects(meta, blobs, example=EXAMPLE_DIR, blob_cache_bytes=1)
    projects.seed_example()
    copy = projects.duplicate(EXAMPLE_ID)["id"]
    assert irr(projects, copy) > 0
    assert len(projects._blob_cache) == 1
