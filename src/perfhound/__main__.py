"""CLI entry point. Real commands arrive once the gateway is usable."""

from __future__ import annotations

import sys

from perfhound import __version__


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if argv and argv[0] in ("--version", "-V"):
        print(f"perfhound {__version__}")
        return 0
    print("perfhound: no commands yet (gateway under construction).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
