from uuid import UUID, uuid4

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import FileResponse, JSONResponse
from starlette.concurrency import run_in_threadpool

from app.agent import DocumentAgent
from app.compliance import DocumentComplianceError, RequestConstraintError
from app.config import Settings
from app.fixture_backend import DemoLLM
from app.document import render_document
from app.llm import GroqLLM, GroqRateLimited, InvalidModelOutput, ModelUnavailable, UnsupportedDemoRequest
from app.schemas import AgentRequest, AgentResponse, DocumentInfo

DOCX_MEDIA_TYPE = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"


def create_app(settings: Settings | None = None, llm=None) -> FastAPI:
    settings = settings or Settings()
    backend = llm if llm is not None else (
        DemoLLM() if settings.agent_backend == "demo" else GroqLLM(settings)
    )
    agent = DocumentAgent(backend)
    api = FastAPI(
        title="Autonomous Document Agent",
        version="1.0.0",
        description="Plan a business document, execute dependent writing tasks, and download a Word document.",
    )

    @api.get("/health")
    async def health():
        # Liveness only: this endpoint does not verify the API key or model access.
        return {"status": "ok", "backend": settings.agent_backend}

    @api.post(
        "/agent",
        response_model=AgentResponse,
        responses={
            200: {
                "description": "Word attachment by default; execution JSON when download=false.",
                "content": {DOCX_MEDIA_TYPE: {"schema": {"type": "string", "format": "binary"}}},
                "headers": {
                    "Content-Disposition": {"description": "Attachment filename in download mode", "schema": {"type": "string"}},
                    "X-Agent-Run-Id": {"description": "Run ID in download mode", "schema": {"type": "string"}},
                    "X-Agent-Task-Count": {"description": "Completed task count in download mode", "schema": {"type": "integer"}},
                },
            },
        },
    )
    async def run_agent(
        body: AgentRequest,
        request: Request,
        download: bool = Query(default=True, description="True (default): Download file directly here. False: return the plan and execution as JSON."),
    ):
        run_id = str(uuid4())
        try:
            plan, execution = await agent.run(body.request)
        except GroqRateLimited as exc:
            headers = {"Retry-After": str(exc.retry_after_seconds)} if exc.retry_after_seconds else {}
            return JSONResponse(
                status_code=429,
                headers=headers,
                content={
                    "detail": str(exc),
                    "retry_after_seconds": exc.retry_after_seconds,
                    "limits_url": "https://console.groq.com/settings/limits",
                },
            )
        except ModelUnavailable as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc
        except RequestConstraintError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        except InvalidModelOutput as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc
        except UnsupportedDemoRequest as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        try:
            path = await run_in_threadpool(
                render_document, settings.artifact_dir, run_id, plan, execution,
                settings.agent_backend == "demo",
            )
        except DocumentComplianceError as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc
        except (OSError, ValueError) as exc:
            raise HTTPException(status_code=500, detail="Could not save the Word document. Check server storage.") from exc
        if download:
            return FileResponse(
                path,
                filename=f"document-{run_id}.docx",
                media_type=DOCX_MEDIA_TYPE,
                headers={
                    "X-Agent-Run-Id": run_id,
                    "X-Agent-Task-Count": str(len(execution)),
                    "X-Agent-Message": "Document generated successfully",
                    "X-Agent-Backend": settings.agent_backend,
                },
            )
        warnings = ["Draft generated without external research. Review assumptions and figures before use."]
        if settings.agent_backend == "demo":
            warnings.insert(0, "DEMO FIXTURE: Authored sample content; no LLM was called.")
        return AgentResponse(
            run_id=run_id,
            message=f"Created '{plan.title}' by completing {len(execution)} planned tasks. Download the Word document using document.download_url.",
            backend=settings.agent_backend,
            model="fixture-replay" if settings.agent_backend == "demo" else settings.groq_model,
            warnings=warnings,
            plan=plan,
            execution=execution,
            document=DocumentInfo(
                filename=path.name,
                download_url=str(request.url_for("download_document", run_id=run_id)),
                size_bytes=path.stat().st_size,
            ),
        )

    @api.get("/documents/{run_id}", response_class=FileResponse, name="download_document", include_in_schema=False)
    async def download_document(run_id: UUID):
        path = settings.artifact_dir / f"{run_id}.docx"
        if not path.is_file():
            raise HTTPException(status_code=404, detail="Document not found")
        return FileResponse(
            path,
            filename=f"document-{run_id}.docx",
            media_type=DOCX_MEDIA_TYPE,
        )

    return api


app = create_app()
