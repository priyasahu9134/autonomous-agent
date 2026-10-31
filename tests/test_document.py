import io

import pytest
from docx import Document
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app


@pytest.mark.parametrize("name", ["standard", "complex"])
def test_end_to_end_download_contains_executed_sections_and_assumptions(load_example, tmp_path, name):
    data = load_example(name)
    client = TestClient(create_app(Settings(agent_backend="demo", artifact_dir=tmp_path)))
    response = client.post("/agent?download=false", json={"request": data["request"]})
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["backend"] == "demo"
    assert "no LLM was called" in body["warnings"][0]
    assert len(body["execution"]) == len(data["plan"]["tasks"])
    download = client.get(body["document"]["download_url"])
    assert download.status_code == 200
    assert "attachment" in download.headers["content-disposition"]
    assert len(download.content) == body["document"]["size_bytes"]
    doc = Document(io.BytesIO(download.content))
    text = "\n".join(p.text for p in doc.paragraphs)
    assert data["plan"]["title"] in text
    assert "DEMO FIXTURE" in text
    for assumption in data["plan"]["assumptions"]:
        assert assumption in text
    for step in body["execution"]:
        assert step["result"]["heading"] in text
        assert step["result"]["paragraphs"][0] in text
    assert len(doc.tables) >= 2
    if name == "complex":
        assert "100% accuracy" in text
        assert "leadership approval" in text
        assert "no-go" in text


def test_agenda_regression_exact_outline_no_duplicates_no_tables(tmp_path, agenda_case, scripted_llm):
    from app.compliance import section_word_count

    request, plan, sections = agenda_case
    llm = scripted_llm([plan, *sections])
    client = TestClient(create_app(Settings(agent_backend="groq", artifact_dir=tmp_path), llm=llm))
    response = client.post("/agent?download=false", json={"request": request})
    assert response.status_code == 200, response.text
    body = response.json()
    doc = Document(io.BytesIO(client.get(body["document"]["download_url"]).content))
    headings = [p.text for p in doc.paragraphs if p.style.name.startswith("Heading")]
    assert headings == ["Updates", "Blockers", "Next Steps"]
    assert not doc.tables
    assert len(llm.calls) == 4  # No extra call for cleanup or checking.
    text = "\n".join(p.text for p in doc.paragraphs)
    assert "execution record" not in text
    assert "Assumptions" not in text
    assert "Audience:" not in text
    for original, result in zip(sections, body["execution"]):
        assert text.count(original.paragraphs[0]) == 1
        from app.schemas import Section
        assert section_word_count(Section.model_validate(result["result"])) < 40
    counts = {}
    heading = None
    for paragraph in doc.paragraphs:
        if paragraph.style.name == "Heading 1":
            heading = paragraph.text
            counts[heading] = 0
        elif heading:
            counts[heading] += len(paragraph.text.split())
    assert all(0 < count < 40 for count in counts.values())


def test_renderer_rejects_invalid_document_before_writing(tmp_path, agenda_case):
    from app.compliance import DocumentComplianceError, apply_explicit_constraints, extract_constraints
    from app.document import render_document
    from app.schemas import ExecutionStep

    request, plan, sections = agenda_case
    apply_explicit_constraints(plan, extract_constraints(request))
    # Reproduce the original 42-word Next Steps section without the cleanup stage.
    execution = [ExecutionStep(task_id=task.id, title=task.title, result=result)
                 for task, result in zip(plan.tasks, sections)]
    with pytest.raises(DocumentComplianceError, match="maximum is 39"):
        render_document(tmp_path, "invalid-agenda", plan, execution, False)
    assert not list(tmp_path.iterdir())
