import json
from pathlib import Path

from app.llm import InvalidModelOutput, UnsupportedDemoRequest
from app.schemas import Plan, Section

EXAMPLES_DIR = Path(__file__).resolve().parent.parent / "examples"


class DemoLLM:
    """Explicit fixture replay for reproducible tests; never a substitute for an LLM."""

    async def generate(self, schema, system: str, payload: dict):
        fixture = None
        for name in ("standard", "complex"):
            candidate = json.loads((EXAMPLES_DIR / f"{name}.json").read_text(encoding="utf-8"))
            if candidate["request"].strip() == payload["request"].strip():
                fixture = candidate
                break
        if fixture is None:
            raise UnsupportedDemoRequest(
                "Demo mode supports only the exact requests in examples/standard.json and examples/complex.json. "
                "Set AGENT_BACKEND=groq for arbitrary requests."
            )
        if schema is Plan:
            return Plan.model_validate(fixture["plan"])
        if schema is Section:
            return Section.model_validate(fixture["sections"][payload["task"]["id"]])
        raise InvalidModelOutput("Unsupported demo output schema")
