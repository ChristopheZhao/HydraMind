# Releasing HydraMind

Alpha releases follow PEP 440 (`0.1.0a0`, `0.1.0a1`, …). The build is verified in
CI and locally; **publishing is a manual, owner-only step** that requires a PyPI
API token and is never performed by automation in this repo.

## 1. Pre-release checks

```bash
uv run --extra dev ruff check .
uv run --extra dev mypy --strict src
uv run --extra dev pytest -q          # includes tests/contract/test_architecture_invariants.py
uv run python scripts/p0_acceptance.py --mode local
```

Bump `version` in `pyproject.toml` and `__version__` in
`src/hydramind/__init__.py` together, and update `CHANGELOG.md`.

## 2. Build

```bash
rm -rf dist
uv build                              # -> dist/hydramind-<v>-py3-none-any.whl + .tar.gz
```

Verify the artifact installs cleanly from the wheel alone:

```bash
uv venv /tmp/hm-rel && VIRTUAL_ENV=/tmp/hm-rel uv pip install dist/hydramind-<v>-py3-none-any.whl
/tmp/hm-rel/bin/hydramind --version   # -> hydramind <v>
/tmp/hm-rel/bin/python -c "import hydramind; print(hydramind.__version__)"
```

## 3. Tag

```bash
git tag -a v<v> -m "HydraMind v<v>"
git push origin main --tags           # owner pushes when a remote is configured
```

## 4. Publish (owner-only; needs a token)

Test on TestPyPI first, then PyPI. Use a project-scoped token; never commit it.

```bash
# TestPyPI dry run
UV_PUBLISH_TOKEN=<testpypi-token> uv publish --publish-url https://test.pypi.org/legacy/ dist/*
# verify installable from TestPyPI
uv pip install --index-url https://test.pypi.org/simple/ \
  --extra-index-url https://pypi.org/simple/ hydramind==<v>

# Production PyPI
UV_PUBLISH_TOKEN=<pypi-token> uv publish dist/*
```

After a successful publish, confirm `pip install hydramind==<v>` from a clean
environment and create the GitHub release from the tag.
