"""Validated request, response, planning and document data contracts."""

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class AgentRequest(StrictModel):
    request: str = Field(min_length=10, max_length=12000)


class Task(StrictModel):
    id: str = Field(pattern=r"^task_[1-8]$")
    title: str = Field(min_length=3, max_length=120)
    instruction: str = Field(min_length=10, max_length=1500)
    depends_on: list[str] = Field(max_length=7)
    acceptance_criteria: str = Field(min_length=10, max_length=800)
    section_id: str | None = Field(description="Outline section produced by this task, or null for internal analysis")


class OutlineSection(StrictModel):
    id: str = Field(pattern=r"^section_[1-8]$")
    heading: str = Field(min_length=1, max_length=150)


class DocumentFormat(StrictModel):
    required_headings: list[str] = Field(max_length=8)
    max_words_per_section: int | None = Field(ge=1, le=10000)
    allow_tables: bool
    include_metadata: bool
    include_assumptions: bool
    include_decisions: bool
    include_execution_appendix: bool


class Plan(StrictModel):
    title: str = Field(min_length=3, max_length=160)
    document_type: str = Field(min_length=3, max_length=80)
    audience: str = Field(min_length=3, max_length=150)
    objective: str = Field(min_length=10, max_length=1000)
    assumptions: list[str] = Field(max_length=12)
    decisions: list[str] = Field(max_length=12)
    document_format: DocumentFormat
    outline: list[OutlineSection] = Field(min_length=1, max_length=8)
    tasks: list[Task] = Field(min_length=1, max_length=8)

    @model_validator(mode="after")
    def ordered_dependencies(self):
        seen: set[str] = set()
        for task in self.tasks:
            if task.id in seen:
                raise ValueError("Task IDs must be unique")
            if len(task.depends_on) != len(set(task.depends_on)):
                raise ValueError("Duplicate dependencies are not allowed")
            if not set(task.depends_on).issubset(seen):
                raise ValueError("Dependencies must reference earlier tasks; cycles are forbidden")
            seen.add(task.id)
        section_ids = [section.id for section in self.outline]
        if len(set(section_ids)) != len(section_ids):
            raise ValueError("Outline section IDs must be unique")
        headings = [section.heading.casefold() for section in self.outline]
        if len(set(headings)) != len(headings):
            raise ValueError("Outline headings must be unique")
        assigned = [task.section_id for task in self.tasks if task.section_id is not None]
        if sorted(assigned) != sorted(section_ids):
            raise ValueError("Each outline section must have exactly one producing task")
        return self


class DocumentTable(StrictModel):
    title: str = Field(min_length=1, max_length=150)
    columns: list[str] = Field(min_length=2, max_length=6)
    rows: list[list[str]] = Field(min_length=1, max_length=20)

    @model_validator(mode="after")
    def rectangular(self):
        if any(len(row) != len(self.columns) for row in self.rows):
            raise ValueError("Each table row must match the column count")
        return self


class Section(StrictModel):
    heading: str = Field(min_length=1, max_length=150)
    paragraphs: list[Annotated[str, Field(min_length=1)]] = Field(max_length=6)
    bullets: list[Annotated[str, Field(min_length=1)]] = Field(max_length=12)
    tables: list[DocumentTable] = Field(max_length=2)

    @model_validator(mode="after")
    def meaningful_content(self):
        if not (self.paragraphs or self.bullets or self.tables):
            raise ValueError("A section must contain paragraphs, bullets or a table")
        return self


class ExecutionStep(StrictModel):
    task_id: str
    title: str
    status: Literal["completed"] = "completed"
    result: Section
    repair_attempted: bool = False


class DocumentInfo(StrictModel):
    filename: str
    download_url: str
    media_type: str = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    size_bytes: int


class AgentResponse(StrictModel):
    run_id: str
    status: Literal["completed"] = "completed"
    message: str
    backend: Literal["groq", "demo"]
    model: str
    warnings: list[str]
    plan: Plan
    execution: list[ExecutionStep]
    document: DocumentInfo
