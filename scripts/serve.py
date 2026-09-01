"""Run the API.

    python scripts/serve.py                 # http://localhost:8000/docs
    python scripts/serve.py --port 8001     # when 8000 is taken

Requires PostgreSQL to be up (`docker compose up -d`) and a dataset loaded
(`python scripts/generate_data.py`).
"""

from __future__ import annotations

import argparse
import socket
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import uvicorn


def port_is_free(host: str, port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        return probe.connect_ex((host, port)) != 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Serve the Settlement Detective API")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--reload", action="store_true")
    args = parser.parse_args()

    # Checked up front so the failure reads as "that port is taken" rather than
    # a bind error buried under a successful-looking startup log.
    if not port_is_free(args.host, args.port):
        print(f"port {args.port} is already in use on {args.host}.")
        print(f"  try: python scripts/serve.py --port {args.port + 1}")
        return 1

    print(f"\n  docs   http://{args.host}:{args.port}/docs")
    print(f"  health http://{args.host}:{args.port}/api/health\n")
    uvicorn.run(
        "backend.api.app:app", host=args.host, port=args.port, reload=args.reload
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
