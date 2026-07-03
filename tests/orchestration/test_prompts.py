"""PromptLibrary + PromptTemplate tests."""

from __future__ import annotations

import textwrap
from pathlib import Path

import pytest

from hydramind.orchestration import PromptLibrary, PromptTemplate


def test_template_render_substitutes_variables() -> None:
    t = PromptTemplate(
        role="writer",
        system="You write {language}.",
        user_template="Write about {topic}",
    )
    sys, user = t.render(language="python", topic="async")
    assert sys == "You write python."
    assert user == "Write about async"


def test_template_render_missing_variable_raises_friendly_error() -> None:
    t = PromptTemplate(role="r", system="s", user_template="hi {missing}")
    with pytest.raises(KeyError, match="missing"):
        t.render()


def test_library_lookup_returns_registered() -> None:
    lib = PromptLibrary()
    t = PromptTemplate(role="planner", system="plan", user_template="{input}")
    lib.register(t)
    assert lib.lookup("planner") is t
    assert "planner" in lib.roles()


def test_library_lookup_unknown_role_lists_known() -> None:
    lib = PromptLibrary({"a": PromptTemplate(role="a", system="x")})
    with pytest.raises(KeyError, match="known roles"):
        lib.lookup("zzz")


def test_library_from_dict() -> None:
    lib = PromptLibrary.from_dict(
        {
            "planner": {"system": "plan", "user_template": "{topic}"},
            "writer": {"system": "write", "user_template": "{outline}"},
        }
    )
    assert set(lib.roles()) == {"planner", "writer"}


def test_library_from_dict_rejects_non_mapping_entry() -> None:
    with pytest.raises(TypeError, match="mapping"):
        PromptLibrary.from_dict({"bad": "not a dict"})  # type: ignore[arg-type]


def test_library_from_yaml(tmp_path: Path) -> None:
    yaml_path = tmp_path / "prompts.yaml"
    yaml_path.write_text(
        textwrap.dedent(
            """\
            planner:
              system: "Plan this"
              user_template: "{input}"
              variables: ["input"]
            writer:
              system: "Write that"
              user_template: "{outline}"
            """
        )
    )
    lib = PromptLibrary.from_yaml(yaml_path)
    assert set(lib.roles()) == {"planner", "writer"}
    sys, user = lib.lookup("planner").render(input="topic")
    assert sys == "Plan this"
    assert user == "topic"


def test_library_from_yaml_rejects_list(tmp_path: Path) -> None:
    yaml_path = tmp_path / "bad.yaml"
    yaml_path.write_text("- not\n- a\n- mapping\n")
    with pytest.raises(TypeError, match="mapping"):
        PromptLibrary.from_yaml(yaml_path)
