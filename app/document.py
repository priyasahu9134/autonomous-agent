import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from docx import Document
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Inches, Pt, RGBColor

from app.compliance import document_sections, validate_document
from app.schemas import ExecutionStep, Plan
from app.text import clean


def render_document(
    output_dir: Path, run_id: str, plan: Plan, execution: list[ExecutionStep], demo: bool
) -> Path:
    # Defense in depth: invalid content is never published, including direct renderer calls.
    validate_document(plan, execution)
    output_dir.mkdir(parents=True, exist_ok=True)
    doc = Document()
    section = doc.sections[0]
    section.top_margin = section.bottom_margin = Inches(0.75)
    section.left_margin = section.right_margin = Inches(0.8)
    normal = doc.styles["Normal"]
    normal.font.name = "Calibri"
    normal.font.size = Pt(11)
    normal.paragraph_format.space_after = Pt(8)
    for name in ("Title", "Heading 1", "Heading 2"):
        doc.styles[name].font.color.rgb = RGBColor.from_string("17365D")
    fmt = plan.document_format
    if fmt.include_metadata:
        header = section.header.paragraphs[0]
        header.text = clean(plan.document_type.upper())
        header.style = doc.styles["Caption"]
    doc.add_heading(clean(plan.title), 0)
    if fmt.include_metadata:
        doc.add_paragraph(clean(plan.objective), style="Subtitle")
        doc.add_paragraph("Audience: " + clean(plan.audience))
        doc.add_paragraph("Prepared: " + datetime.now(timezone.utc).strftime("%d %B %Y (UTC)"))
        doc.add_paragraph("Status: Draft for review")
    if demo:
        doc.add_paragraph(
            "DEMO FIXTURE: This document uses authored sample content. No LLM was called. "
            "All estimates and proposed outcomes are illustrative."
        )
    if fmt.include_assumptions and plan.assumptions:
        doc.add_heading("Assumptions and information to confirm", 1)
        for item in plan.assumptions:
            doc.add_paragraph(clean(item), style="List Bullet")
    if fmt.include_decisions and plan.decisions:
        doc.add_heading("Planning decisions and tradeoffs", 1)
        for item in plan.decisions:
            doc.add_paragraph(clean(item), style="List Bullet")
    for result in document_sections(plan, execution):
        doc.add_heading(clean(result.heading), 1)
        for paragraph in result.paragraphs:
            doc.add_paragraph(clean(paragraph))
        for bullet in result.bullets:
            doc.add_paragraph(clean(bullet), style="List Bullet")
        for data in result.tables:
            doc.add_heading(clean(data.title), 2)
            table = doc.add_table(rows=1, cols=len(data.columns))
            table.style = "Light Shading Accent 1"
            for cell, label in zip(table.rows[0].cells, data.columns):
                cell.text = clean(label)
            repeat_header = OxmlElement("w:tblHeader")
            table.rows[0]._tr.get_or_add_trPr().append(repeat_header)
            for values in data.rows:
                for cell, value in zip(table.add_row().cells, values):
                    cell.text = clean(value)
            doc.add_paragraph()
    if fmt.include_execution_appendix:
        doc.add_page_break()
        doc.add_heading("Appendix: execution record", 1)
        doc.add_paragraph("Completed tasks passed structural and measurable format checks; factual accuracy requires review.")
        for task in plan.tasks:
            doc.add_heading(clean(f"{task.id}: {task.title}"), 2)
            doc.add_paragraph("Status: Completed | Dependencies: " + (", ".join(task.depends_on) or "None"))
            doc.add_paragraph("Task: " + clean(task.instruction))
            doc.add_paragraph("Acceptance criteria: " + clean(task.acceptance_criteria))
    footer = section.footer.paragraphs[0]
    footer.alignment = WD_ALIGN_PARAGRAPH.RIGHT
    footer.add_run("Page ")
    page = OxmlElement("w:fldSimple")
    page.set(qn("w:instr"), "PAGE")
    footer._p.append(page)
    doc.core_properties.title = clean(plan.title)
    doc.core_properties.subject = clean(plan.document_type)
    doc.core_properties.author = "Document Agent"
    path = output_dir / f"{run_id}.docx"
    # Publish only complete ZIP packages; failed runs never expose partial downloads.
    fd, temporary = tempfile.mkstemp(suffix=".tmp", dir=output_dir)
    os.close(fd)
    try:
        doc.save(temporary)
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)
    return path
