"""Exercise both examples over HTTP and save response JSON plus Word downloads."""
import argparse
import json
from pathlib import Path

import httpx


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--output", type=Path, default=Path("demo-output"))
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    root = Path(__file__).resolve().parents[1]
    # Allow time for sequential model calls and bounded rate-limit retries.
    with httpx.Client(base_url=args.base_url, timeout=1800) as client:
        for name in ("standard", "complex"):
            data = json.loads((root / "examples" / f"{name}.json").read_text(encoding="utf-8"))
            response = client.post("/agent", params={"download": "false"}, json={"request":data["request"]})
            response.raise_for_status()
            body = response.json()
            (args.output / f"{name}-response.json").write_text(json.dumps(body, indent=2, ensure_ascii=False), encoding="utf-8")
            download = client.get(body["document"]["download_url"])
            download.raise_for_status()
            path = args.output / f"{name}.docx"
            path.write_bytes(download.content)
            print(f"{name}: backend={body['backend']}; tasks={len(body['execution'])}; document={path}")


if __name__ == "__main__":
    main()
