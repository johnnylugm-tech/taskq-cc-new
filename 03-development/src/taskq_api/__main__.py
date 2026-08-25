"""``python -m taskq_api`` CLI entry point.

Subcommands:
    key create --scope <scope>
        Generate a fresh API key, persist its SHA-256 hash via
        ``taskq_api.repository.key_repo``, and print the plaintext to
        stdout exactly once (FR-03 §3 AC-3.4). The plaintext is never
        written to disk — only the hash is persisted.

[FR-03, NFR-04]
Citations:
  - FR-03 §3 AC-3.4: the plaintext key is printed exactly once at
    creation time and never persisted.
  - NFR-04 (security): plaintext keys MUST NOT appear in any
    persisted file (logs, metrics, DB rows).
"""
from __future__ import annotations

import argparse
import sys

from taskq_api.api.deps import create_key


def _build_parser() -> argparse.ArgumentParser:
    """Build the top-level argument parser."""
    parser = argparse.ArgumentParser(
        prog="taskq_api",
        description="taskq-api command-line interface. [FR-03]",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    key_parser = subparsers.add_parser(
        "key",
        help="Manage API keys. [FR-03]",
    )
    key_sub = key_parser.add_subparsers(dest="key_action", required=True)

    create = key_sub.add_parser(
        "create",
        help="Create a new API key. [FR-03]",
    )
    create.add_argument(
        "--scope",
        required=True,
        help="Scope for the new key (e.g. 'read', 'write', 'admin').",
    )

    return parser


def main(argv: list[str] | None = None) -> int:
    """Dispatch subcommands. Returns the process exit code."""
    parser = _build_parser()
    args = parser.parse_args(argv)

    if args.command == "key" and args.key_action == "create":
        plaintext = create_key(scope=args.scope)
        # AC-3.4: print the plaintext to stdout exactly once.
        sys.stdout.write(plaintext + "\n")
        sys.stdout.flush()
        return 0

    parser.error(f"unknown subcommand: {args.command!r} {args.key_action!r}")
    return 2  # unreachable: parser.error() raises SystemExit(2) above


if __name__ == "__main__":
    raise SystemExit(main())