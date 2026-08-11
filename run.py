"""Entry point: `python run.py` starts the Photo Culler server on http://localhost:8000"""

import argparse

import uvicorn


def main():
    parser = argparse.ArgumentParser(description="Photo Culler local server")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--reload", action="store_true")
    args = parser.parse_args()

    uvicorn.run("backend.main:app", host=args.host, port=args.port, reload=args.reload)


if __name__ == "__main__":
    main()
