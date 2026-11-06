from __future__ import annotations

import argparse
from getpass import getpass
from pathlib import Path

import uvicorn

from .app import create_app


def main() -> None:
    project_root = Path(__file__).resolve().parents[2]
    parser = argparse.ArgumentParser(description="Data intelligence platform local API")
    parser.add_argument("--data-root", type=Path, default=project_root / "data" / "sources")
    parser.add_argument("--state-root", type=Path, default=project_root / "data" / "state")
    sub = parser.add_subparsers(dest="command", required=True)
    bootstrap = sub.add_parser("bootstrap-user", help="Create a local user without an open registration endpoint")
    bootstrap.add_argument("--username", required=True)
    serve = sub.add_parser("serve", help="Serve locally; not a production deployment")
    serve.add_argument("--port", type=int, default=18090)
    args = parser.parse_args()
    args.data_root.mkdir(parents=True, exist_ok=True)
    app = create_app(args.data_root, args.state_root)
    if args.command == "bootstrap-user":
        password = getpass("Password (at least 12 characters): ")
        app.state.identity.create_user(args.username, password)
        print("User created")
    else:
        uvicorn.run(app, host="127.0.0.1", port=args.port)


if __name__ == "__main__":
    main()
