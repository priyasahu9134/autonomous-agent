import pytest
from pydantic import ValidationError

from app.schemas import Plan


@pytest.mark.parametrize("dependencies", [["task_1"], ["task_8"]])
def test_plan_rejects_self_reference_and_missing_dependencies(load_example, dependencies):
    plan = load_example()["plan"]
    plan["tasks"][0]["depends_on"] = dependencies
    with pytest.raises(ValidationError):
        Plan.model_validate(plan)


@pytest.mark.parametrize("assignment", [None, "section_8", "section_2"])
def test_each_outline_section_requires_exactly_one_producing_task(load_example, assignment):
    plan = load_example()["plan"]
    plan["tasks"][0]["section_id"] = assignment
    with pytest.raises(ValidationError, match="exactly one producing task"):
        Plan.model_validate(plan)
