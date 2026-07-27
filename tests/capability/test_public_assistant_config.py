from __future__ import annotations

from pathlib import Path
from unittest import TestCase

from kohakuterrarium.core.config import (
    load_agent_config,
)


PROJECT_ROOT = Path(__file__).resolve().parents[2]

PUBLIC_ASSISTANT_DIRECTORY = (
    PROJECT_ROOT
    / "creatures"
    / "public-assistant"
)

SYSTEM_PROMPT_PATH = (
    PUBLIC_ASSISTANT_DIRECTORY
    / "prompts"
    / "system.md"
)


class PublicAssistantConfigurationTests(
    TestCase
):
    """Protect the effective public-assistant configuration."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.config = load_agent_config(
            PUBLIC_ASSISTANT_DIRECTORY
        )

    def test_effective_config_is_headless(
        self,
    ) -> None:
        self.assertEqual(
            self.config.input.type,
            "none",
            (
                "The public assistant must use "
                "input.type='none'. Restoring CLI input "
                "would start a blocking stdin reader and "
                "can prevent clean Python shutdown on "
                "Windows."
            ),
        )

    def test_effective_config_preserves_web_agent_contract(
        self,
    ) -> None:
        self.assertEqual(
            self.config.name,
            "public_assistant",
        )

        self.assertEqual(
            self.config.tool_format,
            "bracket",
            (
                "The public assistant must keep bracket "
                "tool formatting for the current model "
                "and inherited tool configuration."
            ),
        )

        self.assertEqual(
            self.config.agent_path.resolve(),
            PUBLIC_ASSISTANT_DIRECTORY.resolve(),
        )

        self.assertEqual(
            self.config.system_prompt_file,
            "prompts/system.md",
        )

        self.assertTrue(
            SYSTEM_PROMPT_PATH.is_file(),
            (
                "The public assistant system prompt file "
                "is missing."
            ),
        )

        child_prompt = (
            SYSTEM_PROMPT_PATH
            .read_text(encoding="utf-8")
            .strip()
        )

        self.assertTrue(
            child_prompt,
            (
                "The public assistant system prompt file "
                "must not be empty."
            ),
        )

        self.assertIn(
            child_prompt,
            self.config.system_prompt,
            (
                "The effective inherited AgentConfig did "
                "not include the public assistant system "
                "prompt."
            ),
        )


if __name__ == "__main__":
    import unittest

    unittest.main()