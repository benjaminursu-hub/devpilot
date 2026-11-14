"""Write the OpenAPI specification to docs/openapi.json (or the path given as argument).

cd backend && python scripts/export_openapi.py
"""

import json
import sys
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_DIR))

from app.main import create_app  # noqa: E402


def main() -> None:
    default = BACKEND_DIR.parent / "docs" / "openapi.json"
    output = Path(sys.argv[1]) if len(sys.argv) > 1 else default
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(create_app().openapi(), indent=2) + "\n", encoding="utf-8")
    print(f"Wrote {output}")


if __name__ == "__main__":
    main()
