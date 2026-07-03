from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType


def _load_module() -> ModuleType:
    path = Path(__file__).resolve().parents[1] / "scripts" / "set_env_secrets.py"
    spec = importlib.util.spec_from_file_location("set_env_secrets", path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_update_env_text_updates_existing_and_appends_missing() -> None:
    module = _load_module()

    new_text, result = module.update_env_text(
        "\n".join(
            [
                "HYDRAMIND_PROVIDER=openai_compatible",
                "BRAVE_SEARCH_API_KEY=old",
            ]
        )
        + "\n",
        {
            "BRAVE_SEARCH_API_KEY": "new",
            "DOUBAO_API_KEY": "db",
        },
    )

    assert "HYDRAMIND_PROVIDER=openai_compatible" in new_text
    assert "BRAVE_SEARCH_API_KEY=new" in new_text
    assert "DOUBAO_API_KEY=db" in new_text
    assert result == {
        "BRAVE_SEARCH_API_KEY": "updated",
        "DOUBAO_API_KEY": "added",
    }


def test_update_env_text_quotes_values_when_needed() -> None:
    module = _load_module()

    new_text, _ = module.update_env_text("", {"BRAVE_SEARCH_API_KEY": 'a b " c'})

    assert new_text == 'BRAVE_SEARCH_API_KEY="a b \\" c"\n'


def test_main_prompts_without_printing_secret_values(tmp_path: Path, capsys) -> None:
    module = _load_module()
    env_file = tmp_path / ".env"
    values = iter(["brv", "db"])

    rc = module.main(["--env-file", str(env_file)], prompt_fn=lambda _prompt: next(values))

    assert rc == 0
    output = capsys.readouterr().out
    assert "added: BRAVE_SEARCH_API_KEY" in output
    assert "added: DOUBAO_API_KEY" in output
    assert "brv" not in output
    assert "db" not in output
    text = env_file.read_text(encoding="utf-8")
    assert "BRAVE_SEARCH_API_KEY=brv" in text
    assert "DOUBAO_API_KEY=db" in text


def test_main_rejects_empty_values(tmp_path: Path, capsys) -> None:
    module = _load_module()

    rc = module.main(
        ["--env-file", str(tmp_path / ".env"), "--keys", "BRAVE_SEARCH_API_KEY"],
        prompt_fn=lambda _prompt: " ",
    )

    assert rc == 2
    assert not (tmp_path / ".env").exists()
    assert "refusing to write empty value for BRAVE_SEARCH_API_KEY" in capsys.readouterr().out
