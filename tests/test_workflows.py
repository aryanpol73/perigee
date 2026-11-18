"""The GitHub Actions workflows parse (a YAML error disables a workflow silently:
GitHub only lists a failed run named after the file)."""

from pathlib import Path

import pytest
import yaml

WORKFLOWS = sorted((Path(__file__).resolve().parents[1] / ".github" / "workflows").glob("*.yml"))


@pytest.mark.parametrize("path", WORKFLOWS, ids=lambda p: p.name)
def test_workflow_parses(path):
    workflow = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert isinstance(workflow, dict) and workflow.get("jobs"), f"{path.name} has no jobs"
    for name, job in workflow["jobs"].items():
        for step in job.get("steps", []):
            assert isinstance(step.get("run", ""), str), f"{path.name}:{name}: a run step is not a string"
