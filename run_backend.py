"""Start the local, credential-free demo from any working directory."""
from __future__ import annotations

import argparse
import os
from pathlib import Path
import sys


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8000)
    args = parser.parse_args()
    root = Path(__file__).resolve().parent
    sys.path.insert(0, str(root / "backend"))
    # Match the deployed demo. No hosted model SDK or credentials are needed.
    os.environ.setdefault("ESP_AGENTIC_LAYER", "off")
    os.environ.setdefault("ESP_STORAGE_ROOT", str(root / "data" / "perimeters"))
    import uvicorn

    uvicorn.run("api.app:app", host="127.0.0.1", port=args.port)


if __name__ == "__main__":
    main()
