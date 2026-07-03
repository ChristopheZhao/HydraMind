#!/usr/bin/env python3
"""Set local .env secrets through hidden interactive prompts."""

from __future__ import annotations

import argparse
import getpass
import re
from collections.abc import Callable, Mapping
from pathlib import Path

PromptFn = Callable[[str], str]

DEFAULT_KEYS = ("BRAVE_SEARCH_API_KEY", "DOUBAO_API_KEY")
_ASSIGNMENT = re.compile(r"^(\s*(?:export\s+)?)([A-Za-z_][A-Za-z0-9_]*)(\s*=).*$")


def main(argv: list[str] | None = None, *, prompt_fn: PromptFn = getpass.getpass) -> int:
    args = _parse_args(argv)
    values: dict[str, str] = {}
    for key in args.keys:
        value = prompt_fn(f"{key}: ").strip()
        if not value:
            print(f"refusing to write empty value for {key}")
            return 2
        values[key] = value
    result = update_env_file(Path(args.env_file), values)
    for key in args.keys:
        action = result[key]
        print(f"{action}: {key}")
    print("secret values were not printed")
    return 0


def update_env_file(path: Path, values: Mapping[str, str]) -> dict[str, str]:
    text = path.read_text(encoding="utf-8") if path.exists() else ""
    new_text, result = update_env_text(text, values)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(new_text, encoding="utf-8")
    return result


def update_env_text(text: str, values: Mapping[str, str]) -> tuple[str, dict[str, str]]:
    remaining = dict(values)
    result = {key: "added" for key in values}
    lines = text.splitlines()
    out: list[str] = []
    for line in lines:
        match = _ASSIGNMENT.match(line)
        if match is None:
            out.append(line)
            continue
        prefix, key, separator = match.groups()
        if key not in remaining:
            out.append(line)
            continue
        out.append(f"{prefix}{key}{separator}{_quote_env_value(remaining.pop(key))}")
        result[key] = "updated"
    if remaining and out and out[-1].strip():
        out.append("")
    for key, value in remaining.items():
        out.append(f"{key}={_quote_env_value(value)}")
    ending = "\n" if text.endswith("\n") or out else ""
    return "\n".join(out) + ending, result


def _quote_env_value(value: str) -> str:
    if value and re.fullmatch(r"[A-Za-z0-9_./:@+-]+", value):
        return value
    escaped = value.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Interactively set secret values in an ignored .env file without "
            "putting the values in shell history."
        )
    )
    parser.add_argument(
        "--env-file",
        default="/mnt/d/code/agent/framework/hydramind/.env",
        help="ignored .env file to update",
    )
    parser.add_argument(
        "--keys",
        nargs="+",
        choices=DEFAULT_KEYS,
        default=list(DEFAULT_KEYS),
        help="secret key names to prompt for",
    )
    return parser.parse_args(argv)


if __name__ == "__main__":
    raise SystemExit(main())
