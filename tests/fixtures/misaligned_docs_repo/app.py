"""Tiny example app used as a misaligned-docs fixture.

The README claims more than the code delivers. Only one flag is wired up;
the others, plus the env var, helper script, and one dependency, are
absent on purpose so the alignment checks have something to find.
"""


def main(port: int = 8080) -> None:
    # Only --port is actually wired up.
    print(f"listening on port {port}")


if __name__ == "__main__":
    import sys

    p = 8080
    if "--port" in sys.argv:
        idx = sys.argv.index("--port")
        p = int(sys.argv[idx + 1])
    main(p)
