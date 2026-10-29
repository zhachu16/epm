"""The agent skills in skills/ must match the connector and work as written."""

from __future__ import annotations

import asyncio
import json
import re
from pathlib import Path

import pytest
from mcp import Client

from epm.formula.builtins import BUILTINS
from service.projects import EXAMPLE_ID, Projects
from service.store import MemoryBlobStore, MemoryMetaStore
from service.tools import build_mcp

SKILLS = Path(__file__).resolve().parents[2] / "skills"
NAMES = ["epm-create-model", "epm-edit-model"]
CONNECTOR = "https://epm-nm1g.onrender.com/mcp"


def text(name: str, file: str = "SKILL.md") -> str:
    return (SKILLS / name / file).read_text()


def batches(name: str) -> list[dict]:
    blocks = re.findall(r"```json\n(.*?)```", text(name), re.S)
    return [b for b in map(json.loads, blocks) if "ops" in b]


@pytest.fixture(scope="module")
def tools() -> dict[str, dict]:
    async def schemas():
        async with Client(build_mcp(Projects(MemoryMetaStore(), MemoryBlobStore()))) as c:
            return {t.name: t.input_schema for t in (await c.list_tools()).tools}

    return asyncio.run(schemas())


@pytest.mark.parametrize("name", NAMES)
def test_frontmatter(name):
    match = re.match(r"---\nname: (.+)\ndescription: (.+)\n---\n", text(name))
    assert match, "SKILL.md starts with name and description"
    assert match.group(1) == name and re.fullmatch(r"[a-z0-9-]{1,64}", name)
    assert len(match.group(2)) <= 1024 and CONNECTOR in match.group(2)


def test_both_skills_carry_the_same_reference():
    assert text(NAMES[0], "reference.md") == text(NAMES[1], "reference.md")


def test_reference_lists_every_tool(tools):
    reference = text(NAMES[0], "reference.md")
    assert [t for t in tools if f"`{t}`" not in reference] == []
    table = reference.split("## Tools")[1].split("\n## ")[0]
    rows = [line.split("|")[1] for line in table.splitlines() if line.startswith("| `")]
    listed = {name for row in rows for name in re.findall(r"`(\w+)`", row)}
    assert len(rows) > 10 and listed - set(tools) == set()


@pytest.mark.parametrize("name", NAMES)
def test_calls_name_real_tools_and_arguments(name, tools):
    calls = re.findall(r"`(\w+)\(([^`]*)\)`", text(name) + text(name, "reference.md"))
    assert calls
    for tool, args in calls:
        if tool in BUILTINS:
            continue  # a formula, not a tool call
        assert tool in tools, tool
        bare = re.sub(r'"[^"]*"', '""', args)
        for keyword in re.findall(r"(\w+)=", bare):
            assert keyword in tools[tool]["properties"], f"{tool}({keyword}=...)"


def test_create_model_example_builds_a_clean_model():
    projects = Projects(MemoryMetaStore(), MemoryBlobStore())
    project = projects.create("Acme DCF", "Five-year DCF of Acme")["id"]
    examples = batches("epm-create-model")
    assert len(examples) == 4
    for batch in examples:
        preview = projects.apply(project, batch["ops"], "dry_run", label=batch["label"])
        assert preview["ok"], (batch["label"], preview["errors"])
        assert projects.apply(project, batch["ops"], "commit", label=batch["label"])["committed"]
    assert projects.validate(project) == {"diagnostics": []}
    assert projects.show(project, "settings")["definition"]["name"] == "Acme DCF"  # one name
    value = projects.run(project, outputs=["enterprise_value"])["values"]["enterprise_value"]
    assert round(value["value"]) == 259  # the figure the skill quotes
    assert len(projects.run_analysis(project, "wacc_growth")["sweep"]["rows"]) == 9


def test_edit_model_examples_apply_to_a_copy_of_the_example():
    projects = Projects(MemoryMetaStore(), MemoryBlobStore())
    projects.seed_example()
    project = projects.duplicate(EXAMPLE_ID, "Atlas")["id"]
    examples = batches("epm-edit-model")
    assert len(examples) == 5
    first = projects.apply(project, examples[0]["ops"], "dry_run")
    irr = first["impact"]["outputs"]["equity_irr"]
    assert (round(irr["before"]["value"], 3), round(irr["after"]["value"], 3)) == (0.173, 0.170)
    for batch in examples:
        expected = projects.get(project)["current_version"]
        result = projects.apply(project, batch["ops"], "commit", expected, batch["label"])
        assert result["committed"], (batch["label"], result["errors"])
    assert projects.validate(project) == {"diagnostics": []}
