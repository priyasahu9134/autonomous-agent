from pydantic import ValidationError

from app.compliance import (
    DocumentComplianceError, apply_explicit_constraints, extract_constraints,
    normalize_section, plan_errors, section_errors, validate_document,
)
from app.llm import InvalidModelOutput, StructuredLLM
from app.prompts import PLANNER_PROMPT, WRITER_PROMPT
from app.schemas import ExecutionStep, Plan, Section


class DocumentAgent:
    def __init__(self, llm: StructuredLLM):
        self.llm = llm

    async def run(self, request: str) -> tuple[Plan, list[ExecutionStep]]:
        constraints = extract_constraints(request)
        planning_payload = {"request": request, "explicit_constraints": constraints.as_dict()}
        for attempt in range(2):
            try:
                plan = await self.llm.generate(Plan, PLANNER_PROMPT, planning_payload)
                apply_explicit_constraints(plan, constraints)
                errors = plan_errors(plan, constraints)
                if not errors:
                    break
            except InvalidModelOutput as exc:
                errors = [str(exc)]
            if attempt:
                raise DocumentComplianceError("Plan validation failed after one repair: " + " ".join(errors))
            planning_payload["repair_errors"] = errors

        completed: dict[str, Section] = {}
        execution: list[ExecutionStep] = []
        for task in plan.tasks:
            payload = {
                "request": request,
                "document": plan.model_dump(exclude={"tasks"}),
                "task": task.model_dump(),
                "completed_dependencies": {
                    task_id: completed[task_id].model_dump() for task_id in task.depends_on
                },
            }
            for attempt in range(2):
                try:
                    section = normalize_section(await self.llm.generate(Section, WRITER_PROMPT, payload))
                    errors = section_errors(section, plan, task.section_id) if task.section_id else []
                    if not errors:
                        break
                    payload["previous_result"] = section.model_dump()
                except (InvalidModelOutput, ValidationError) as exc:
                    errors = [str(exc)]
                if attempt:
                    raise DocumentComplianceError(
                        f"Task {task.id} failed document checks after one repair: " + " ".join(errors)
                    )
                payload["repair_errors"] = errors
            completed[task.id] = section
            execution.append(ExecutionStep(
                task_id=task.id, title=task.title, result=section, repair_attempted=bool(attempt),
            ))
        validate_document(plan, execution, constraints)
        return plan, execution
