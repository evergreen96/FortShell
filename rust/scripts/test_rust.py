from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path


RUST_WORKSPACE_ROOT = Path(__file__).resolve().parents[1]


def run_step(label: str, command: list[str]) -> int:
    print(f"[rust-tests] {label}: {' '.join(command)}", flush=True)
    completed = subprocess.run(command, cwd=RUST_WORKSPACE_ROOT)
    return completed.returncode


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Run the Rust test baseline. By default this runs the fast workspace baseline "
            "and skips the long ai-ide-windows-helper helper_binary integration suite."
        )
    )
    parser.add_argument(
        "--include-helper-binary",
        action="store_true",
        help="run the long helper_binary integration suite after the fast Rust baseline",
    )
    parser.add_argument(
        "--helper-binary-only",
        action="store_true",
        help="run only the long helper_binary integration suite",
    )
    args = parser.parse_args()

    if args.include_helper_binary and args.helper_binary_only:
        parser.error("--include-helper-binary and --helper-binary-only cannot be combined")

    fast_steps = [
        (
            "fast-workspace",
            ["cargo", "test", "--workspace", "--exclude", "ai-ide-windows-helper"],
        ),
        (
            "windows-helper-protocol",
            ["cargo", "test", "-p", "ai-ide-windows-helper", "--lib", "--test", "helper_protocol"],
        ),
    ]
    helper_binary_step = (
        "windows-helper-binary",
        ["cargo", "test", "-p", "ai-ide-windows-helper", "--test", "helper_binary"],
    )

    steps: list[tuple[str, list[str]]] = []
    if args.helper_binary_only:
        steps.append(helper_binary_step)
    else:
        steps.extend(fast_steps)
        if args.include_helper_binary:
            steps.append(helper_binary_step)

    for label, command in steps:
        returncode = run_step(label, command)
        if returncode != 0:
            return returncode
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
