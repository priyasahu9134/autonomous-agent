"""Shared sample data for offline tests."""

import json
from pathlib import Path

import pytest

from app.schemas import Plan, Section


@pytest.fixture
def load_example():
    """Return a fresh copy on each call so tests cannot alter other tests' data."""
    examples_dir = Path(__file__).resolve().parents[1] / "examples"

    def load(name="standard"):
        return json.loads((examples_dir / f"{name}.json").read_text(encoding="utf-8"))

    return load


@pytest.fixture
def agenda_case(load_example):
    request = (
        "Create a very brief meeting agenda for a 15-minute weekly team check-in. "
        "Use exactly three sections: updates, blockers, and next steps. "
        "Keep each section under 40 words. No tables."
    )
    plan = load_example()["plan"]
    plan.update(title="15-Minute Weekly Team Check-In", document_type="Meeting agenda")
    plan["outline"] = [
        {"id": f"section_{i}", "heading": heading}
        for i, heading in enumerate(("Updates", "Blockers", "Next Steps"), 1)
    ]
    plan["tasks"] = plan["tasks"][:3]
    for task, outline in zip(plan["tasks"], plan["outline"]):
        task["title"] = "Draft " + outline["heading"]
        task["instruction"] = f"Write the {outline['heading']} agenda section in fewer than 40 words."
        task["acceptance_criteria"] = "Use the requested heading, fewer than 40 words and no tables."
    bodies = [
        "Each member shares one key progress item from the past week. Focus on high-level status only to keep the meeting on schedule.",
        "Identify any issues preventing progress. List specific impediments requiring immediate attention or support from the team to unblock work.",
        "Define immediate actions for the coming week. Assign clear owners and deadlines to ensure accountability and maintain momentum toward project goals.",
    ]
    sections = [Section(heading=outline["heading"], paragraphs=[body], bullets=[body], tables=[])
                for outline, body in zip(plan["outline"], bodies)]
    return request, Plan.model_validate(plan), sections


@pytest.fixture
def scripted_llm():
    """Serve a known model-response sequence without network access or paid calls."""
    class ScriptedLLM:
        def __init__(self, responses):
            self.responses = iter(responses)
            self.calls = []

        async def generate(self, schema, system, payload):
            import copy
            self.calls.append(copy.deepcopy(payload))
            response = next(self.responses)
            if isinstance(response, Exception):
                raise response
            assert isinstance(response, schema)
            return response.model_copy(deep=True)

    return ScriptedLLM
