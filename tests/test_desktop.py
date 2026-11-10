"""The single-file program (epm.exe), run unfrozen: finding the model, its fm passthrough,
the function worker it starts in itself, and its self-test."""

import json
import os
import shutil
import sys
from pathlib import Path

import pytest

from epm import EpmError
from epm.desktop import main, resolve_model
from epm.functions import FunctionRuntime, runtime
from epm.server.lockfile import _pid_alive

LBO = Path(__file__).parents[1] / "examples" / "lbo"


@pytest.fixture
def lbo(tmp_path: Path) -> Path:
    target = tmp_path / "lbo"
    shutil.copytree(LBO, target, ignore=shutil.ignore_patterns("cache", "*.lock"))
    return target


def test_resolve_model(lbo, tmp_path):
    assert resolve_model(lbo / "model.json", []) == lbo
    assert resolve_model(lbo, []) == lbo
    archive = tmp_path / "deal.epm"
    archive.write_bytes(b"")
    assert resolve_model(archive, []) == archive
    with pytest.raises(EpmError, match="does not exist"):
        resolve_model(tmp_path / "nope.json", [])
    (tmp_path / "model (1).json").write_text("{}")  # how browsers name a second download
    with pytest.raises(EpmError, match="rename it to model.json"):
        resolve_model(tmp_path / "model (1).json", [])
    (tmp_path / "empty").mkdir()
    with pytest.raises(EpmError, match="has no model.json"):
        resolve_model(tmp_path / "empty", [])


def test_resolve_model_by_looking_around(lbo, tmp_path):
    empty = tmp_path / "empty"
    empty.mkdir()
    assert resolve_model(None, [empty]) is None
    assert resolve_model(None, [empty, lbo]) == lbo  # next to the program, then elsewhere
    (empty / "deal.epm").write_bytes(b"")
    assert resolve_model(None, [empty, lbo]) == empty / "deal.epm"
    (empty / "other.epm").write_bytes(b"")
    assert resolve_model(None, [empty, lbo]) == lbo  # two archives: ambiguous, look further


def test_version(capsys):
    assert main(["--version"]) == 0
    assert "engine" in capsys.readouterr().out


def test_fm_passthrough_takes_a_model_json(lbo, capsys):
    with pytest.raises(SystemExit) as exit_:
        main(["fm", "-m", str(lbo / "model.json"), "--json", "run", "--output", "equity_irr"])
    assert exit_.value.code in (0, None)
    data = json.loads(capsys.readouterr().out)["data"]
    assert 0.17 < data["values"]["equity_irr"]["value"] < 0.18


def test_frozen_program_starts_its_worker_in_itself(monkeypatch):
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    assert runtime.worker_command() == [sys.executable, runtime.WORKER_FLAG]
    monkeypatch.delattr(sys, "frozen")
    # what the frozen program runs, unfrozen: the dispatch in epm.desktop serves as worker
    command = [sys.executable, "-m", "epm.desktop", runtime.WORKER_FLAG]
    monkeypatch.setattr(runtime, "worker_command", lambda: command)
    source = "from epm import function\n\n@function\ndef doubled(x):\n    return x * 2\n"
    rt = FunctionRuntime(source)
    try:
        assert rt.call("doubled", [21], {}) == 42
    finally:
        rt.close()


def test_pid_probe():
    assert _pid_alive(os.getpid())
    assert not _pid_alive(0) and not _pid_alive(-1)
    assert not _pid_alive(2**22 + 12345)  # above Linux's largest pid; never valid on Windows


def test_self_test(capsys):
    assert main(["--self-test"]) == 0, capsys.readouterr().out
    out = capsys.readouterr().out
    assert "self-test passed" in out and "FAIL" not in out
