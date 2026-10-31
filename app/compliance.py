"""Deterministic checks for measurable document constraints, without LLM calls."""

import re
from dataclasses import asdict, dataclass

from app.llm import InvalidModelOutput
from app.schemas import ExecutionStep, Plan, Section
from app.text import clean


class RequestConstraintError(Exception):
    pass


class DocumentComplianceError(InvalidModelOutput):
    pass


@dataclass
class ExplicitConstraints:
    exact_section_count: int | None = None
    required_headings: list[str] | None = None
    max_words_per_section: int | None = None
    no_tables: bool = False
    compact: bool = False

    def as_dict(self):
        return asdict(self)


def extract_constraints(request: str) -> ExplicitConstraints:
    """Recognize common English constraints; the planner handles other wording."""
    result = ExplicitConstraints()
    numbers = {word: i for i, word in enumerate(
        ("zero", "one", "two", "three", "four", "five", "six", "seven", "eight"))}
    match = re.search(r"\bexactly\s+(\d+|one|two|three|four|five|six|seven|eight)\s+sections?\b", request, re.I)
    if match:
        value = match[1].lower()
        result.exact_section_count = int(value) if value.isdigit() else numbers[value]
        if not 1 <= result.exact_section_count <= 8:
            raise RequestConstraintError("This service supports 1 to 8 document sections.")
        tail = request[match.end():]
        listed = re.match(r"\s*:\s*([^.;\n]+)", tail)
        if listed:
            headings = [part.strip(" \t\"'`") for part in re.split(r",|\band\b", listed[1], flags=re.I)]
            headings = [heading for heading in headings if heading]
            if len(headings) == result.exact_section_count:
                result.required_headings = headings
    limits = []
    for match in re.finditer(r"\b(under|fewer than|less than|at most|no more than)\s+(\d+)\s+words?\b", request, re.I):
        # These forms are interpreted per section only when the request says so.
        if re.search(r"\b(each|per)\s+section\b", request, re.I):
            limits.append(int(match[2]) - (match[1].lower() in {"under", "fewer than", "less than"}))
    if limits:
        result.max_words_per_section = min(limits)
        if result.max_words_per_section < 1:
            raise RequestConstraintError("Each section needs a positive word allowance.")
    result.no_tables = bool(re.search(r"\b(no tables?|without tables?|do not (?:include|use) tables?)\b", request, re.I))
    result.compact = bool(result.exact_section_count or re.search(r"\b(?:very brief|brief|concise)\b", request, re.I))
    return result


def normalized(text: str) -> str:
    return " ".join(clean(text).casefold().split()).strip(" .!?;:")


def normalize_section(section: Section) -> Section:
    """Keep the first occurrence of a block or complete sentence, paragraphs first."""
    seen = set()

    def unique(blocks):
        kept = []
        for block in blocks:
            block = clean(block).strip()
            sentences = re.split(r"(?<=[.!?])\s+", block)
            fresh = []
            for sentence in sentences:
                key = normalized(sentence)
                if key and key not in seen:
                    fresh.append(sentence)
                    seen.add(key)
            if fresh:
                kept.append(" ".join(fresh))
        return kept

    paragraphs = unique(section.paragraphs)
    bullets = unique(section.bullets)
    data = section.model_dump()
    data.update(heading=clean(section.heading).strip(), paragraphs=paragraphs, bullets=bullets)
    for table in data["tables"]:
        table["title"] = clean(table["title"]).strip()
        table["columns"] = [clean(cell).strip() for cell in table["columns"]]
        table["rows"] = [[clean(cell).strip() for cell in row] for row in table["rows"]]
    return Section.model_validate(data)


def section_word_count(section: Section) -> int:
    # Body words include table titles, headings and cells; the section heading is excluded.
    blocks = section.paragraphs + section.bullets
    for table in section.tables:
        blocks += [table.title, *table.columns, *(cell for row in table.rows for cell in row)]
    return sum(len(block.split()) for block in blocks)


def apply_explicit_constraints(plan: Plan, constraints: ExplicitConstraints) -> None:
    """User constraints override optional presentation choices from the model."""
    fmt = plan.document_format
    if constraints.no_tables:
        fmt.allow_tables = False
    if constraints.max_words_per_section is not None:
        fmt.max_words_per_section = min(
            constraints.max_words_per_section,
            fmt.max_words_per_section or constraints.max_words_per_section,
        )
    if constraints.required_headings:
        fmt.required_headings = constraints.required_headings
    if constraints.compact or fmt.required_headings:
        fmt.include_metadata = fmt.include_assumptions = fmt.include_decisions = False
        fmt.include_execution_appendix = False


def plan_errors(plan: Plan, constraints: ExplicitConstraints) -> list[str]:
    errors = []
    headings = [normalized(section.heading) for section in plan.outline]
    if constraints.exact_section_count and len(headings) != constraints.exact_section_count:
        errors.append(f"The outline must have exactly {constraints.exact_section_count} sections.")
    required = plan.document_format.required_headings
    if required and headings != [normalized(heading) for heading in required]:
        errors.append(f"The outline headings must be {required}, in that order, without additions.")
    return errors


def section_errors(section: Section, plan: Plan, section_id: str) -> list[str]:
    errors = []
    expected = next(item.heading for item in plan.outline if item.id == section_id)
    if normalized(section.heading) != normalized(expected):
        errors.append(f"Use the exact heading '{expected}'.")
    fmt = plan.document_format
    if not fmt.allow_tables and section.tables:
        errors.append("Tables are forbidden; express the content in paragraphs or bullets.")
    count = section_word_count(section)
    if fmt.max_words_per_section and count > fmt.max_words_per_section:
        errors.append(f"Section body has {count} words; maximum is {fmt.max_words_per_section}.")
    return errors


def document_sections(plan: Plan, execution: list[ExecutionStep]) -> list[Section]:
    """Assemble the final outline independently of internal task execution order."""
    completed = {step.task_id: step.result for step in execution}
    if len(completed) != len(execution) or set(completed) != {task.id for task in plan.tasks}:
        raise DocumentComplianceError("Execution record does not match the planned tasks.")
    by_section = {task.section_id: completed[task.id] for task in plan.tasks if task.section_id}
    return [by_section[item.id] for item in plan.outline]


def validate_document(plan: Plan, execution: list[ExecutionStep], constraints: ExplicitConstraints | None = None) -> None:
    errors = plan_errors(plan, constraints or ExplicitConstraints())
    for item, section in zip(plan.outline, document_sections(plan, execution)):
        errors.extend(section_errors(section, plan, item.id))
    if errors:
        raise DocumentComplianceError("Document validation failed: " + " ".join(errors))
