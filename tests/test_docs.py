"""The documentation's examples must work as written."""

import json
import re
import shlex
import shutil
from pathlib import Path

import pytest
from typer.testing import CliRunner

from epm import Workspace
from epm.cli import app
from epm.formula import BUILTINS

ROOT = Path(__file__).resolve().parents[1]
GUIDE = ROOT / "docs" / "agent-guide.md"
README = ROOT / "README.md"


def blocks(path: Path, lang: str) -> list[str]:
    return re.findall(rf"```{lang}\n(.*?)```", path.read_text(), re.S)


def batches(path: Path) -> list[dict]:
    parsed = [json.loads(b) for b in blocks(path, "json")]
    return [b for b in parsed if "ops" in b]


def lbo_commands(path: Path) -> list[list[str]]:
    """``fm`` commands against the sample LBO, except ``serve``, which never returns."""
    commands = []
    for block in blocks(path, "bash"):
        argv = shlex.split(block)
        if argv[:3] == ["fm", "-m", "examples/lbo"] and "serve" not in argv:
            commands.append(argv[1:])
    return commands


@pytest.fixture
def lbo(tmp_path: Path) -> Path:
    target = tmp_path / "lbo"
    ignore = shutil.ignore_patterns("cache", ".epm-session.lock")
    shutil.copytree(ROOT / "examples" / "lbo", target, ignore=ignore)
    return target


@pytest.mark.parametrize("path", [GUIDE, README], ids=["guide", "readme"])
def test_example_batches_apply_cleanly(path, lbo):
    examples = batches(path)
    assert examples
    ws = Workspace.open(lbo)
    try:
        for batch in examples:
            result = ws.apply(batch, mode="dry_run")
            assert result.ok, (batch.get("label"), result.to_dict()["errors"])
    finally:
        ws.close()


@pytest.mark.parametrize("path", [GUIDE, README], ids=["guide", "readme"])
def test_example_commands_run(path, lbo, monkeypatch):
    commands = lbo_commands(path)
    assert commands
    monkeypatch.chdir(lbo.parent)
    (lbo.parent / "changes.json").write_text(json.dumps(batches(GUIDE)[0]))
    runner = CliRunner()
    for argv in commands:
        argv = [str(lbo) if a == "examples/lbo" else a for a in argv]
        result = runner.invoke(app, argv)
        assert result.exit_code == 0, (" ".join(argv), result.output)


def test_guide_documents_every_builtin():
    text = GUIDE.read_text()
    assert [name for name in BUILTINS if f"`{name}(" not in text] == []


def test_guide_diagnostic_codes_exist():
    source = "\n".join(p.read_text() for p in (ROOT / "epm").rglob("*.py"))
    section = GUIDE.read_text().split("## Diagnostics")[1].split("\n## ")[0]
    codes = {
        code
        for line in section.splitlines()
        if line.startswith("| `")
        for code in re.findall(r"`([a-z_]+)`", line.split("|")[1])
    }
    assert len(codes) > 20
    assert sorted(c for c in codes if f'"{c}"' not in source) == []


@pytest.mark.parametrize("path", [GUIDE, README], ids=["guide", "readme"])
def test_python_examples_run(path, lbo, monkeypatch):
    examples = [b for b in blocks(path, "python") if "Workspace.open" in b]
    assert examples
    monkeypatch.chdir(lbo.parent)
    for code in examples:
        exec(code.replace('"examples/lbo"', repr(str(lbo))), {})
