"""Tiny example app used as an aligned-docs fixture."""

import os


def main(port: int = 8080) -> None:
    # Accept the --port CLI flag (documented in README).
    api_key = os.environ["API_KEY"]
    print(f"listening on port {port} with key prefix {api_key[:3]}")


if __name__ == "__main__":
    import sys

    p = 8080
    if "--port" in sys.argv:
        idx = sys.argv.index("--port")
        p = int(sys.argv[idx + 1])
    main(p)
