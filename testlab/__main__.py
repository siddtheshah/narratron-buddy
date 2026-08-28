"""CLI entry point for running the testlab module via `python -m testlab`."""

import argparse
import uvicorn



def main() -> None:
    parser = argparse.ArgumentParser(description="Narratron Test Lab Server")
    parser.add_argument("--host", default="127.0.0.1", help="Host address")
    parser.add_argument("--port", type=int, default=8000, help="Port number")
    parser.add_argument("--reload", action="store_true", help="Enable hot reload")
    args = parser.parse_args()

    print(f"Starting Narratron Test Lab on http://{args.host}:{args.port}")
    uvicorn.run("testlab.server:app", host=args.host, port=args.port, reload=args.reload)


if __name__ == "__main__":
    main()
