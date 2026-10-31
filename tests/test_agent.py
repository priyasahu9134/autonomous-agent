import asyncio

import pytest

from app.agent import DocumentAgent
from app.fixture_backend import DemoLLM


def test_executor_passes_actual_dependency_results(load_example):
    class RecordingLLM(DemoLLM):
        def __init__(self):
            self.payloads = []

        async def generate(self, schema, system, payload):
            self.payloads.append(payload)
            return await super().generate(schema, system, payload)

    llm = RecordingLLM()
    plan, execution = asyncio.run(DocumentAgent(llm).run(load_example()["request"]))
    assert len(llm.payloads) == 1 + len(plan.tasks)
    for task, payload in zip(plan.tasks, llm.payloads[1:]):
        assert set(payload["completed_dependencies"]) == set(task.depends_on)
        for dependency in task.depends_on:
            actual = next(step.result.model_dump() for step in execution if step.task_id == dependency)
            assert payload["completed_dependencies"][dependency] == actual


def test_section_repair_uses_errors_and_only_repeats_failing_task(agenda_case, scripted_llm):
    request, plan, sections = agenda_case
    bad = sections[0].model_copy(update={"paragraphs": [" ".join(["word"] * 40)], "bullets": []})
    llm = scripted_llm([plan, bad, sections[0], *sections[1:]])
    _, execution = asyncio.run(DocumentAgent(llm).run(request))
    assert len(llm.calls) == 5
    assert "maximum is 39" in llm.calls[2]["repair_errors"][0]
    assert "previous_result" in llm.calls[2]
    assert execution[0].repair_attempted
    assert not any(step.repair_attempted for step in execution[1:])
    assert llm.calls[3]["completed_dependencies"]["task_1"] == execution[0].result.model_dump()


def test_failed_section_repair_is_bounded(agenda_case, scripted_llm):
    from app.compliance import DocumentComplianceError

    request, plan, sections = agenda_case
    bad = sections[0].model_copy(update={"paragraphs": [" ".join(["word"] * 40)], "bullets": []})
    llm = scripted_llm([plan, bad, bad])
    with pytest.raises(DocumentComplianceError, match="after one repair"):
        asyncio.run(DocumentAgent(llm).run(request))
    assert len(llm.calls) == 3


def test_plan_repair_corrects_wrong_outline(agenda_case, scripted_llm):
    request, plan, sections = agenda_case
    bad = plan.model_copy(deep=True)
    bad.outline[0].heading = "Unrequested overview"
    llm = scripted_llm([bad, plan, *sections])
    result, _ = asyncio.run(DocumentAgent(llm).run(request))
    assert len(llm.calls) == 5
    assert "repair_errors" in llm.calls[1]
    assert result.outline[0].heading == "Updates"


def test_plan_repair_stops_after_one_failed_attempt(agenda_case, scripted_llm):
    from app.compliance import DocumentComplianceError

    request, plan, _ = agenda_case
    plan.outline[0].heading = "Unrequested overview"
    llm = scripted_llm([plan, plan])
    with pytest.raises(DocumentComplianceError, match="Plan validation failed after one repair"):
        asyncio.run(DocumentAgent(llm).run(request))
    assert len(llm.calls) == 2


def test_internal_tasks_do_not_add_sections_and_outline_controls_render_order(agenda_case, scripted_llm):
    from app.compliance import document_sections
    from app.schemas import Plan

    _, plan, sections = agenda_case
    data = plan.model_dump()
    data["outline"] = [{"id": "section_1", "heading": "Next Steps"}]
    for task in data["tasks"][:2]:
        task["section_id"] = None
    data["tasks"][2]["section_id"] = "section_1"
    one_section_plan = Plan.model_validate(data)
    llm = scripted_llm([one_section_plan, *sections])
    result, execution = asyncio.run(DocumentAgent(llm).run(
        "Create a brief action plan. Use exactly one section: Next Steps."
    ))
    assert len(execution) == 3
    assert [section.heading for section in document_sections(result, execution)] == ["Next Steps"]


def test_outline_order_is_independent_of_execution_order(agenda_case, scripted_llm):
    from app.compliance import document_sections

    _, plan, sections = agenda_case
    plan.outline.reverse()
    llm = scripted_llm([plan, *sections])
    result, execution = asyncio.run(DocumentAgent(llm).run("Create a weekly team meeting agenda."))
    assert [section.heading for section in document_sections(result, execution)] == ["Next Steps", "Blockers", "Updates"]


@pytest.mark.parametrize("violation", ["heading", "tables"])
def test_heading_and_table_violations_trigger_targeted_repair(agenda_case, scripted_llm, violation):
    from app.schemas import DocumentTable

    request, plan, sections = agenda_case
    bad = sections[0].model_copy(deep=True)
    if violation == "heading":
        bad.heading = "Unexpected heading"
    else:
        bad.tables = [DocumentTable(title="Not allowed", columns=["A", "B"], rows=[["1", "2"]])]
    llm = scripted_llm([plan, bad, *sections])
    _, execution = asyncio.run(DocumentAgent(llm).run(request))
    assert execution[0].repair_attempted
    assert llm.calls[2]["repair_errors"]
