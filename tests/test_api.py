import io
import json
from uuid import uuid4

import httpx
import pytest
from docx import Document
from fastapi.testclient import TestClient

from app.config import Settings
from app.llm import GroqRateLimited, InvalidModelOutput, ModelUnavailable
from app.main import create_app


@pytest.mark.parametrize("payload", [{}, {"request":" " * 30}, {"request":123}, {"request":"x" * 12001}])
def test_invalid_request_is_rejected(tmp_path, payload):
    client = TestClient(create_app(Settings(agent_backend="demo", artifact_dir=tmp_path)))
    assert client.post("/agent", json=payload).status_code == 422
    assert not list(tmp_path.glob("*.docx"))


@pytest.mark.parametrize("error,status", [(ModelUnavailable("offline"),503), (InvalidModelOutput("invalid"),502)])
def test_failed_run_does_not_publish_document(load_example, tmp_path, error, status):
    class FailingLLM:
        async def generate(self, *args):
            raise error

    client = TestClient(create_app(Settings(artifact_dir=tmp_path), llm=FailingLLM()))
    response = client.post("/agent", json={"request":load_example()["request"]})
    assert response.status_code == status
    assert not list(tmp_path.iterdir())


def test_demo_rejects_arbitrary_requests_and_download_is_scoped(tmp_path):
    client = TestClient(create_app(Settings(agent_backend="demo", artifact_dir=tmp_path)))
    assert client.post("/agent", json={"request":"Write a completely unrelated proposal."}).status_code == 422
    assert client.get(f"/documents/{uuid4()}").status_code == 404
    assert client.get("/documents/not-a-uuid").status_code == 422


def test_groq_missing_key_returns_actionable_error_without_network(load_example, tmp_path, monkeypatch):
    def unexpected_network(**kwargs):
        pytest.fail("Missing key must be detected before opening an HTTP client")

    monkeypatch.setattr(httpx, "AsyncClient", unexpected_network)
    client = TestClient(create_app(Settings(agent_backend="groq", groq_api_key="", artifact_dir=tmp_path)))
    response = client.post("/agent", json={"request":load_example()["request"]})
    assert response.status_code == 503
    assert "GROQ_API_KEY is missing" in response.json()["detail"]
    assert not list(tmp_path.iterdir())


def test_groq_backend_end_to_end_with_mocked_provider(load_example, tmp_path, monkeypatch):
    data = load_example()
    calls = []
    original_client = httpx.AsyncClient

    def respond(request):
        payload = json.loads(json.loads(request.content)["messages"][1]["content"])
        calls.append(payload)
        result = data["sections"][payload["task"]["id"]] if "task" in payload else data["plan"]
        return httpx.Response(200, json={"choices":[{"finish_reason":"stop", "message":{"content":json.dumps(result)}}]})

    monkeypatch.setattr(httpx, "AsyncClient", lambda **kwargs: original_client(transport=httpx.MockTransport(respond), **kwargs))
    settings = Settings(agent_backend="groq", groq_api_key="test-key", artifact_dir=tmp_path)
    client = TestClient(create_app(settings))
    response = client.post("/agent?download=false", json={"request":data["request"]})
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["backend"] == "groq"
    assert body["model"] == settings.groq_model
    assert len(calls) == 1 + len(data["plan"]["tasks"])
    download = client.get(body["document"]["download_url"])
    doc = Document(io.BytesIO(download.content))
    assert doc.paragraphs
    assert "DEMO FIXTURE" not in "\n".join(p.text for p in doc.paragraphs)


def test_failed_compliance_repair_returns_502_without_a_document(tmp_path, agenda_case, scripted_llm):
    request, plan, sections = agenda_case
    bad = sections[0].model_copy(update={"paragraphs": [" ".join(["word"] * 40)], "bullets": []})
    client = TestClient(create_app(
        Settings(agent_backend="groq", artifact_dir=tmp_path), llm=scripted_llm([plan, bad, bad]),
    ))
    response = client.post("/agent", json={"request": request})
    assert response.status_code == 502
    assert "after one repair" in response.json()["detail"]
    assert not list(tmp_path.iterdir())


@pytest.mark.parametrize("retry_after", [90,None])
def test_rate_limit_returns_429_and_provider_wait_time(tmp_path, retry_after):
    class RateLimitedLLM:
        async def generate(self, *args):
            raise GroqRateLimited(retry_after)

    client = TestClient(create_app(Settings(artifact_dir=tmp_path), llm=RateLimitedLLM()))
    response = client.post("/agent", json={"request":"Create a short meeting agenda."})
    assert response.status_code == 429
    assert response.json()["retry_after_seconds"] == retry_after
    assert response.json()["limits_url"] == "https://console.groq.com/settings/limits"
    if retry_after:
        assert response.headers["retry-after"] == str(retry_after)
    else:
        assert "retry-after" not in response.headers
    assert not list(tmp_path.iterdir())


@pytest.mark.parametrize("url", ["/agent", "/agent?download=true"])
def test_post_can_return_word_attachment_without_separate_download(tmp_path, agenda_case, scripted_llm, url):
    request, plan, sections = agenda_case
    llm = scripted_llm([plan, *sections])
    client = TestClient(create_app(Settings(agent_backend="groq", artifact_dir=tmp_path), llm=llm))
    response = client.post(url, json={"request":request})
    assert response.status_code == 200
    assert response.headers["content-type"] == "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    assert "attachment" in response.headers["content-disposition"]
    assert response.headers["x-agent-message"] == "Document generated successfully"
    assert response.headers["x-agent-task-count"] == "3"
    doc = Document(io.BytesIO(response.content))
    assert [p.text for p in doc.paragraphs if p.style.name == "Heading 1"] == ["Updates", "Blockers", "Next Steps"]
    assert len(llm.calls) == 4
    run_id = response.headers["x-agent-run-id"]
    assert (tmp_path / f"{run_id}.docx").read_bytes() == response.content
    # The existing retrieval route remains available without repeating generation.
    assert client.get(f"/documents/{run_id}").content == response.content
    assert len(llm.calls) == 4


def test_openapi_documents_json_and_direct_word_download(tmp_path):
    client = TestClient(create_app(Settings(agent_backend="demo", artifact_dir=tmp_path)))
    paths = client.get("/openapi.json").json()["paths"]
    assert "/documents/{run_id}" not in paths
    operation = paths["/agent"]["post"]
    parameter = next(p for p in operation["parameters"] if p["name"] == "download")
    assert parameter["in"] == "query"
    assert parameter["schema"]["default"] is True
    content = operation["responses"]["200"]["content"]
    assert "application/json" in content
    assert content["application/vnd.openxmlformats-officedocument.wordprocessingml.document"]["schema"]["format"] == "binary"


def test_direct_download_errors_still_return_json(tmp_path):
    client = TestClient(create_app(Settings(agent_backend="groq", groq_api_key="", artifact_dir=tmp_path)))
    response = client.post("/agent?download=true", json={"request":"Write a simple meeting agenda."})
    assert response.status_code == 503
    assert "GROQ_API_KEY is missing" in response.json()["detail"]
    assert "content-disposition" not in response.headers
    assert not list(tmp_path.iterdir())
