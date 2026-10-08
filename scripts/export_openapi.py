"""Export the public OpenAPI document from the running application definition."""

from __future__ import annotations

import json
from pathlib import Path

from app.main import create_app

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "docs" / "next-phase" / "openapi-v1.json"


def main() -> None:
    schema = create_app().openapi()
    OUTPUT.write_text(json.dumps(schema, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(f"wrote {OUTPUT} ({len(schema.get('paths', {}))} paths)")


if __name__ == "__main__":
    main()
