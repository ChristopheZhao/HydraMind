"""Smoke tests — verify the package imports and the CLI surface is stable."""

from __future__ import annotations

import hydramind
from hydramind import cli


def test_version_is_string() -> None:
    assert isinstance(hydramind.__version__, str)
    assert hydramind.__version__


def test_cli_version(capsys) -> None:
    rc = cli.main(["--version"])
    captured = capsys.readouterr()
    assert rc == 0
    assert "hydramind" in captured.out


def test_cli_help(capsys) -> None:
    rc = cli.main(["--help"])
    captured = capsys.readouterr()
    assert rc == 0
    assert "usage:" in captured.out
