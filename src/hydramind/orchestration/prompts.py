"""PromptLibrary — role → template lookup, loadable from YAML.

Role prompts live in configuration, not code. The reference project's
``template_manager.py`` debt (hard-coded Python strings per role) is fixed
by requiring callers to construct or load a library.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml  # type: ignore[import-untyped]
from pydantic import BaseModel, ConfigDict, Field


class PromptTemplate(BaseModel):
    """One named template with simple ``{name}`` interpolation."""

    model_config = ConfigDict(frozen=True)

    role: str
    system: str
    user_template: str = "{input}"
    variables: tuple[str, ...] = Field(
        default=(),
        description="Names of variables expected during render (advisory).",
    )

    def render(self, **variables: Any) -> tuple[str, str]:
        """Return (system_prompt, user_message) with variables interpolated.

        Both ``system`` and ``user_template`` support ``{name}`` placeholders
        and share the same variable pool.
        """
        try:
            system = self.system.format(**variables)
            user = self.user_template.format(**variables)
        except KeyError as exc:
            missing = exc.args[0]
            raise KeyError(
                f"prompt {self.role!r} missing variable {missing!r}"
            ) from None
        return system, user


class PromptLibrary:
    """Named registry of PromptTemplate keyed by role."""

    def __init__(self, templates: dict[str, PromptTemplate] | None = None) -> None:
        self._templates: dict[str, PromptTemplate] = dict(templates or {})

    def register(self, template: PromptTemplate) -> None:
        self._templates[template.role] = template

    def lookup(self, role: str) -> PromptTemplate:
        try:
            return self._templates[role]
        except KeyError:
            raise KeyError(
                f"no prompt template registered for role {role!r}; "
                f"known roles: {sorted(self._templates)}"
            ) from None

    def roles(self) -> tuple[str, ...]:
        return tuple(sorted(self._templates))

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> PromptLibrary:
        """Build from a {role: {system, user_template, variables}} dict."""
        lib = cls()
        for role, fields in raw.items():
            if not isinstance(fields, dict):
                raise TypeError(
                    f"prompt entry {role!r} must be a mapping, got {type(fields).__name__}"
                )
            lib.register(PromptTemplate(role=role, **fields))
        return lib

    @classmethod
    def from_yaml(cls, path: str | Path) -> PromptLibrary:
        text = Path(path).read_text(encoding="utf-8")
        raw = yaml.safe_load(text) or {}
        if not isinstance(raw, dict):
            raise TypeError(
                f"prompt YAML at {path} must be a mapping at the top level"
            )
        return cls.from_dict(raw)
