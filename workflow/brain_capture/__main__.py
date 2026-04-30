from __future__ import annotations

import argparse
import sys

from .core import (
    alfred_menu_json,
    run_action,
)


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="brain_capture")
    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser("alfred-menu", help="Print Alfred Script Filter JSON.")

    a = sub.add_parser("run", help="Run an action (capture/open-vault/open-config/health-check).")
    a.add_argument("action", choices=["capture", "open-vault", "open-config", "health-check"])

    return p


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)

    if args.cmd == "alfred-menu":
        sys.stdout.write(alfred_menu_json())
        return 0

    if args.cmd == "run":
        try:
            msg = run_action(args.action)
        except Exception as e:  # noqa: BLE001
            # Alfred notifications show stdout; keep it concise.
            msg = f"Error: {e}"
        if msg:
            sys.stdout.write(msg.rstrip() + "\n")
        return 0

    raise AssertionError(f"Unhandled cmd: {args.cmd}")


if __name__ == "__main__":
    raise SystemExit(main())
