"""
Agent implementations for OSWorld task execution.

Provides agents that decide actions based on screenshots and instructions.
"""

from __future__ import annotations

import base64
import json
import logging
from abc import ABC, abstractmethod
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


class BaseAgent(ABC):
    """Base class for agents that decide actions."""

    @abstractmethod
    def decide_action(
        self,
        screenshot: bytes,
        instruction: str,
        history: List[Dict[str, Any]],
    ) -> Dict[str, Any]:
        """Decide the next action based on screenshot and instruction.

        Args:
            screenshot: Current screenshot as PNG bytes
            instruction: Natural language task instruction
            history: List of previous actions taken

        Returns:
            Dict with 'action_type' and 'args' keys
        """
        ...


class ClaudeAgent(BaseAgent):
    """Agent that uses Claude to decide actions with persistent multi-turn memory."""

    # System prompt for action generation
    SYSTEM_PROMPT = """You are a computer use agent controlling a desktop environment.
Based on the screenshot and instruction, decide the next action to take.

Available actions:
- click: {"action_type": "click", "args": {"x": int, "y": int}}
- double_click: {"action_type": "double_click", "args": {"x": int, "y": int}}
- right_click: {"action_type": "right_click", "args": {"x": int, "y": int}}
- typing: {"action_type": "typing", "args": {"text": string}}
- press: {"action_type": "press", "args": {"key": string}}
- hotkey: {"action_type": "hotkey", "args": {"keys": [string, ...]}}
- scroll: {"action_type": "scroll", "args": {"direction": "up"|"down"|"left"|"right", "clicks": int}}
- wait: {"action_type": "wait", "args": {"duration": float}}
- done: {"action_type": "done", "args": {}}
- fail: {"action_type": "fail", "args": {"reason": string}}

IMPORTANT: After each action, check whether the instruction is already satisfied.
If the task is complete, output {"action_type": "done", "args": {}} immediately.
If the task is impossible, output {"action_type": "fail", "args": {"reason": "..."}}.

Respond with ONLY a JSON object containing the action.
"""

    def __init__(
        self,
        api_key: Optional[str] = None,
        model: str = "claude-sonnet-4-20250514",
        run_id: str = "",
    ):
        """Initialize Claude agent.

        Args:
            api_key: Anthropic API key. If None, uses ANTHROPIC_API_KEY env var.
            model: Claude model to use.
            run_id: tag for the usage_log so cost rolls up per run.
        """
        try:
            import anthropic
        except ImportError:
            raise ImportError("anthropic package required. Install with: pip install anthropic")

        self.client = anthropic.Anthropic(api_key=api_key)
        self.model = model
        self.run_id = run_id
        self.messages: List[Dict[str, Any]] = []  # Persistent conversation history

    def reset(self) -> None:
        """Reset conversation history for a new task."""
        self.messages = []

    def decide_action(
        self,
        screenshot: bytes,
        instruction: str,
        history: List[Dict[str, Any]],
    ) -> Dict[str, Any]:
        """Use Claude to decide the next action with persistent memory."""
        # Build history summary from recent actions (more context than before)
        recent = history[-15:] if len(history) > 15 else history
        history_text = ""
        if recent:
            history_lines = []
            for i, h in enumerate(recent, 1):
                action_type = h.get("action_type", "unknown")
                err = h.get("err", "")
                history_lines.append(f"{i}. {action_type}" + (f" (err: {err})" if err else ""))
            history_text = "\n\nRecent actions:\n" + "\n".join(history_lines)

        user_message = f"Instruction: {instruction}{history_text}\n\nDecide the next action. If the task is complete, choose done."

        # Build content: include image only if screenshot is valid
        content: List[Dict[str, Any]] = []
        if screenshot and len(screenshot) > 100:
            screenshot_b64 = base64.standard_b64encode(screenshot).decode("utf-8")
            content.append({
                "type": "image",
                "source": {
                    "type": "base64",
                    "media_type": "image/png",
                    "data": screenshot_b64,
                },
            })
        content.append({"type": "text", "text": user_message})

        # Append new user turn to persistent history
        self.messages.append({"role": "user", "content": content})

        # Keep a rolling window of messages to control context size
        # System prompt is passed separately, so we only need user/assistant turns
        conversation_window = self.messages[-8:] if len(self.messages) > 8 else self.messages

        try:
            response = self.client.messages.create(
                model=self.model,
                max_tokens=1200,  # Increased from 256 for better reasoning
                system=self.SYSTEM_PROMPT,
                messages=conversation_window,
            )

            # Capture token usage for cost reporting.
            try:
                from atomix.usage_log import record_anthropic
                record_anthropic(response, model=self.model, run_id=self.run_id)
            except Exception:
                logger.exception("usage_log.record_anthropic failed")

            # Parse response
            response_text = response.content[0].text.strip()

            # Capture assistant message in persistent history
            self.messages.append({"role": "assistant", "content": [{"type": "text", "text": response_text}]})

            # Try to extract JSON from response
            action = self._parse_action(response_text)
            logger.debug(f"Claude decided: {action}")
            return action

        except Exception as e:
            logger.error(f"Claude API error: {e}")
            # Add error to history so model knows what happened
            self.messages.append({
                "role": "assistant",
                "content": [{"type": "text", "text": f"Error: {str(e)}"}]
            })
            return {"action_type": "fail", "args": {"reason": f"Agent error: {e}"}}

    def _parse_action(self, response_text: str) -> Dict[str, Any]:
        """Parse action from Claude's response.

        Handles multiple response formats:
        1. Direct JSON: {"action_type": "click", "args": {"x": 100}}
        2. JSON in markdown: ```json\n{...}\n``` or ```\n{...}\n```
        3. Text + JSON: "Thinking..." then JSON code block
        """
        # Try direct JSON parse
        try:
            return json.loads(response_text)
        except json.JSONDecodeError:
            pass

        # Try to extract JSON from markdown code block (with or without "json" label)
        if "```" in response_text:
            # Find all code blocks
            lines = response_text.split("\n")
            in_code_block = False
            code_content = []
            for i, line in enumerate(lines):
                if line.strip().startswith("```"):
                    if in_code_block:
                        # End of code block
                        in_code_block = False
                        break
                    else:
                        # Start of code block, skip the opening line
                        in_code_block = True
                        continue
                if in_code_block:
                    code_content.append(line)

            if code_content:
                code_text = "\n".join(code_content).strip()
                try:
                    return json.loads(code_text)
                except json.JSONDecodeError:
                    pass

        # Try to find JSON object in text (last { } pair)
        start = response_text.find("{")
        end = response_text.rfind("}") + 1
        if start >= 0 and end > start:
            try:
                return json.loads(response_text[start:end])
            except json.JSONDecodeError:
                pass

        # Try to find JSON-like pattern: "action_type": "something"
        import re
        match = re.search(r'"action_type"\s*:\s*"([^"}\n]+)', response_text)
        if match:
            action_type = match.group(1).strip().strip('"')
            # Try to build minimal action
            return {"action_type": action_type, "args": {}}

        # Log the raw response for debugging
        logger.warning(f"Failed to parse action from response: {response_text[:200]}")

        # Default to fail
        return {"action_type": "fail", "args": {"reason": "Could not parse action"}}


class ScriptedAgent(BaseAgent):
    """Agent that follows a predefined script of actions.

    Useful for testing and reproducible experiments.
    """

    def __init__(self, actions: List[Dict[str, Any]]):
        """Initialize with a list of actions to execute.

        Args:
            actions: List of action dicts in order
        """
        self.actions = actions
        self.step = 0

    def decide_action(
        self,
        screenshot: bytes,
        instruction: str,
        history: List[Dict[str, Any]],
    ) -> Dict[str, Any]:
        """Return the next scripted action."""
        if self.step >= len(self.actions):
            return {"action_type": "done", "args": {}}

        action = self.actions[self.step]
        self.step += 1
        return action

    def reset(self) -> None:
        """Reset to start of script."""
        self.step = 0


class RandomAgent(BaseAgent):
    """Agent that takes random actions. Useful for stress testing."""

    def __init__(
        self,
        screen_width: int = 1920,
        screen_height: int = 1080,
        max_steps: int = 20,
        seed: Optional[int] = None,
    ):
        import random

        self.screen_width = screen_width
        self.screen_height = screen_height
        self.max_steps = max_steps
        self.rng = random.Random(seed)
        self._step_count = 0

    def decide_action(
        self,
        screenshot: bytes,
        instruction: str,
        history: List[Dict[str, Any]],
    ) -> Dict[str, Any]:
        """Return a random action."""
        self._step_count += 1

        if self._step_count >= self.max_steps:
            return {"action_type": "done", "args": {}}

        action_type = self.rng.choice(
            ["click", "typing", "scroll", "hotkey", "press", "wait"]
        )

        if action_type == "click":
            return {
                "action_type": "click",
                "args": {
                    "x": self.rng.randint(0, self.screen_width),
                    "y": self.rng.randint(0, self.screen_height),
                },
            }
        elif action_type == "typing":
            words = ["hello", "test", "world", "example", "data"]
            return {
                "action_type": "typing",
                "args": {"text": self.rng.choice(words)},
            }
        elif action_type == "scroll":
            return {
                "action_type": "scroll",
                "args": {
                    "direction": self.rng.choice(["up", "down"]),
                    "clicks": self.rng.randint(1, 5),
                },
            }
        elif action_type == "hotkey":
            return {
                "action_type": "hotkey",
                "args": {"keys": ["ctrl", self.rng.choice(["c", "v", "z", "a"])]},
            }
        elif action_type == "press":
            return {
                "action_type": "press",
                "args": {"key": self.rng.choice(["enter", "tab", "escape"])},
            }
        else:  # wait
            return {
                "action_type": "wait",
                "args": {"duration": self.rng.uniform(0.5, 2.0)},
            }

    def reset(self) -> None:
        """Reset step counter."""
        self._step_count = 0
