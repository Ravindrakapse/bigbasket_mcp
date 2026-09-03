import os

from src.server import mcp, SERVE_SSE


def main():
    if SERVE_SSE:
        host = os.environ.get("HOST", "0.0.0.0")
        port = int(os.environ.get("PORT", "8000"))
        try:
            mcp.run(transport="sse", host=host, port=port)
        except TypeError:
            mcp.run(transport="sse")
    else:
        mcp.run()


if __name__ == "__main__":
    main()
