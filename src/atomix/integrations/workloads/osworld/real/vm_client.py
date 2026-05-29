"""
HTTP client for OSWorld VM action server.

Communicates with the OSWorld VM environment to execute pyautogui actions
and capture screenshots.
"""

from __future__ import annotations

import importlib.util
import json
import logging
import shlex
import time
from dataclasses import dataclass, field
from typing import Any, Dict, Optional

from .action_types import ActionType, OSWorldAction, to_pyautogui_command

logger = logging.getLogger(__name__)

# Try to import httpx, fall back to requests if not available
try:
    import httpx

    _HTTP_CLIENT = "httpx"
except ImportError:
    _HTTP_CLIENT = "requests" if importlib.util.find_spec("requests") else None


@dataclass
class VMConfig:
    """Configuration for VM connection."""

    host: str = "localhost"
    port: int = 5000
    timeout: float = 30.0
    screenshot_before: bool = True
    screenshot_after: bool = True
    # OSWorld-specific settings
    screen_width: int = 1920
    screen_height: int = 1080


@dataclass
class ActionResult:
    """Result from executing an action on the VM."""

    success: bool
    response: Dict[str, Any] = field(default_factory=dict)
    before_screenshot: Optional[bytes] = None
    after_screenshot: Optional[bytes] = None
    error: Optional[str] = None


class VMClient:
    """HTTP client for OSWorld VM action server."""

    def __init__(self, config: Optional[VMConfig] = None):
        self.config = config or VMConfig()
        self.base_url = f"http://{self.config.host}:{self.config.port}"

        if _HTTP_CLIENT == "httpx":
            self._client = httpx.Client(timeout=self.config.timeout)
        elif _HTTP_CLIENT == "requests":
            self._client = None  # Use requests directly
            self._timeout = self.config.timeout
        else:
            raise ImportError(
                "Either httpx or requests is required. Install with: pip install httpx"
            )

    def execute_action(self, action: OSWorldAction) -> ActionResult:
        """Execute an action on the VM and return result with screenshots."""
        before_screenshot = None
        after_screenshot = None

        command = to_pyautogui_command(action)
        if command in {"WAIT", "FAIL", "DONE"}:
            if command == "WAIT":
                time.sleep(action.duration or 1)
            return ActionResult(success=True, response={"status": command})

        # Capture before screenshot if configured
        if self.config.screenshot_before:
            before_screenshot = self.screenshot()

        # Build and send the action
        python_command = f"import pyautogui; {command}"
        payload = {"command": ["python", "-c", python_command], "shell": False}
        logger.debug(f"Executing action: {payload}")

        try:
            response = self._post("/execute", payload)
            success = response.get("status") == "success" or response.get(
                "success", True
            )
            error = response.get("error")
        except Exception as e:
            logger.error(f"Action execution failed: {e}")
            return ActionResult(
                success=False,
                error=str(e),
                before_screenshot=before_screenshot,
            )

        # Capture after screenshot
        if self.config.screenshot_after:
            after_screenshot = self.screenshot()

        return ActionResult(
            success=success,
            response=response,
            before_screenshot=before_screenshot,
            after_screenshot=after_screenshot,
            error=error,
        )

    def screenshot(self) -> Optional[bytes]:
        """Capture current VM screenshot."""
        try:
            return self._get_binary("/screenshot")
        except Exception as e:
            logger.warning(f"Failed to capture screenshot: {e}")
            return None

    def get_active_window(self) -> Dict[str, Any]:
        """Get information about the active window."""
        try:
            return self._get("/active_window")
        except Exception as e:
            logger.warning(f"Failed to get active window: {e}")
            return {"title": "unknown", "app": "unknown"}

    def get_accessibility_tree(self) -> Optional[str]:
        """Get the accessibility tree (if available)."""
        try:
            response = self._get("/accessibility_tree")
            return response.get("tree", "")
        except Exception as e:
            logger.warning(f"Failed to get accessibility tree: {e}")
            return None

    def close(self) -> None:
        if _HTTP_CLIENT == "httpx" and self._client:
            self._client.close()

    # Convenience methods for common actions
    def click(self, x: int, y: int, button: str = "left") -> ActionResult:
        """Click at coordinates."""
        return self.execute_action(
            OSWorldAction(ActionType.CLICK, coordinate=(x, y), button=button)
        )

    def double_click(self, x: int, y: int) -> ActionResult:
        """Double-click at coordinates."""
        return self.execute_action(
            OSWorldAction(ActionType.DOUBLE_CLICK, coordinate=(x, y))
        )

    def right_click(self, x: int, y: int) -> ActionResult:
        """Right-click at coordinates."""
        return self.execute_action(
            OSWorldAction(ActionType.RIGHT_CLICK, coordinate=(x, y))
        )

    def move_to(self, x: int, y: int) -> ActionResult:
        """Move cursor to coordinates."""
        return self.execute_action(OSWorldAction(ActionType.MOVE_TO, coordinate=(x, y)))

    def drag_to(
        self, start_x: int, start_y: int, end_x: int, end_y: int
    ) -> ActionResult:
        """Drag from start to end coordinates."""
        # First move to start
        self.move_to(start_x, start_y)
        # Then drag to end
        return self.execute_action(
            OSWorldAction(
                ActionType.DRAG_TO,
                coordinate=(end_x, end_y),
                start_coordinate=(start_x, start_y),
            )
        )

    def type_text(self, text: str) -> ActionResult:
        """Type text character by character."""
        return self.execute_action(OSWorldAction(ActionType.TYPING, text=text))

    def press_key(self, key: str) -> ActionResult:
        """Press a single key."""
        return self.execute_action(OSWorldAction(ActionType.PRESS, key=key))

    def hotkey(self, keys: list[str]) -> ActionResult:
        """Press a key combination (e.g., ["ctrl", "c"])."""
        return self.execute_action(OSWorldAction(ActionType.HOTKEY, keys=keys))

    def scroll(self, direction: str, clicks: int = 3) -> ActionResult:
        """Scroll in a direction."""
        return self.execute_action(
            OSWorldAction(ActionType.SCROLL, direction=direction, clicks=clicks)
        )

    def wait(self, duration: float) -> ActionResult:
        """Wait for a duration."""
        return self.execute_action(OSWorldAction(ActionType.WAIT, duration=duration))

    # Evaluation helper methods
    def execute_command(self, command: str, capture_output: bool = True) -> ActionResult:
        """Execute a shell command on the VM and return the result.

        This is used by evaluators to check VM state for task completion.
        """
        if capture_output:
            python_code = (
                "import subprocess, sys; "
                f"r = subprocess.run(['/bin/bash', '-c', {json.dumps(command)}], "
                "capture_output=True, text=True); "
                "sys.stdout.write(r.stdout or ''); "
                "sys.stderr.write(r.stderr or ''); "
                "raise SystemExit(r.returncode)"
            )
        else:
            python_code = (
                "import subprocess; "
                f"raise SystemExit(subprocess.run(['/bin/bash', '-c', {json.dumps(command)}]).returncode)"
            )

        payload = {"command": ["python", "-c", python_code], "shell": False}

        try:
            response = self._post("/execute", payload)
            success = response.get("status") == "success" or response.get("success", True)
            output = response.get("output", response.get("stdout", ""))
            return ActionResult(success=success, response={"output": output, **response})
        except Exception as e:
            logger.error(f"Command execution failed: {e}")
            return ActionResult(success=False, error=str(e))

    def read_file(self, file_path: str) -> Optional[str]:
        """Read a file from the VM.

        Returns file contents as string, or None if file doesn't exist or can't be read.
        """
        result = self.execute_command(f"cat {shlex.quote(file_path)}")
        if result.success:
            return result.response.get("output", "")
        return None

    def file_exists(self, file_path: str) -> bool:
        """Check if a file exists in the VM."""
        result = self.execute_command(
            f"test -f {shlex.quote(file_path)} && echo EXISTS || echo NOT_EXISTS"
        )
        if result.success:
            output = result.response.get("output", "")
            return "EXISTS" in output
        return False

    # HTTP helpers
    #
    # The OSWorld VM action server is flaky under sustained load — connection
    # resets and timeouts surface as transport errors that have nothing to do
    # with the experiment. We retry transport-level failures with a short
    # exponential backoff so a flaky VM doesn't poison Track-B success counts.
    # HTTP-level errors (4xx/5xx with a JSON body) are NOT retried — those are
    # genuine action failures that the harness must handle.
    _TRANSPORT_RETRIES = 3
    _TRANSPORT_BACKOFF_S = 0.5

    def _request_with_retry(self, kind: str, path: str, **kw):
        """kind ∈ {'GET', 'POST'}. Retries only on transport errors."""
        url = f"{self.base_url}{path}"
        last_exc: Optional[Exception] = None
        for attempt in range(self._TRANSPORT_RETRIES):
            try:
                if _HTTP_CLIENT == "httpx":
                    if kind == "POST":
                        return self._client.post(url, **kw)
                    return self._client.get(url, **kw)
                import requests
                if kind == "POST":
                    return requests.post(url, timeout=self._timeout, **kw)
                return requests.get(url, timeout=self._timeout, **kw)
            except Exception as exc:  # transport-level: timeout, ConnectionReset, ECONNREFUSED, ...
                last_exc = exc
                if attempt + 1 < self._TRANSPORT_RETRIES:
                    import time as _time
                    _time.sleep(self._TRANSPORT_BACKOFF_S * (2 ** attempt))
                    continue
                break
        # Exhausted retries.
        assert last_exc is not None
        raise last_exc

    def _post(self, path: str, payload: dict) -> dict:
        """POST JSON to VM server with transport retry."""
        response = self._request_with_retry("POST", path, json=payload)
        response.raise_for_status()
        return response.json()

    def _get(self, path: str) -> dict:
        """GET JSON from VM server with transport retry."""
        response = self._request_with_retry("GET", path)
        response.raise_for_status()
        return response.json()

    def _get_binary(self, path: str) -> bytes:
        """GET binary data from VM server with transport retry."""
        response = self._request_with_retry("GET", path)
        response.raise_for_status()
        return response.content


class DesktopEnvClient:
    def __init__(self, env: Any):
        self._env = env

    def execute_action(self, action: OSWorldAction) -> ActionResult:
        before_screenshot = self.screenshot()
        command = to_pyautogui_command(action)
        obs, *_rest, info = self._env.step(command)
        after_screenshot = None
        if isinstance(obs, dict):
            after_screenshot = obs.get("screenshot")
        return ActionResult(
            success=True,
            response=info or {},
            before_screenshot=before_screenshot,
            after_screenshot=after_screenshot,
        )

    def screenshot(self) -> Optional[bytes]:
        controller = getattr(self._env, "controller", None)
        if controller is None:
            return None
        return controller.get_screenshot()

    def get_active_window(self) -> Dict[str, Any]:
        return {"title": "unknown", "app": "unknown"}

    def press_key(self, key: str) -> ActionResult:
        return self.execute_action(OSWorldAction(ActionType.PRESS, key=key))

    def hotkey(self, keys: list[str]) -> ActionResult:
        return self.execute_action(OSWorldAction(ActionType.HOTKEY, keys=keys))

    def scroll(self, direction: str, clicks: int = 3) -> ActionResult:
        return self.execute_action(
            OSWorldAction(ActionType.SCROLL, direction=direction, clicks=clicks)
        )

    def drag_to(
        self, start_x: int, start_y: int, end_x: int, end_y: int
    ) -> ActionResult:
        return self.execute_action(
            OSWorldAction(
                ActionType.DRAG_TO,
                coordinate=(end_x, end_y),
                start_coordinate=(start_x, start_y),
            )
        )

    def close(self) -> None:
        closer = getattr(self._env, "close", None)
        if callable(closer):
            closer()

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()


class MockVMClient(VMClient):
    """Mock VM client for testing without a real VM."""

    def __init__(self, config: Optional[VMConfig] = None):
        self.config = config or VMConfig()
        self.base_url = "mock://vm"
        self.action_history: list[OSWorldAction] = []
        self._mock_window = {"title": "Mock Application", "app": "mock"}
        self._mock_screenshot = b"MOCK_SCREENSHOT_DATA"

    def execute_action(self, action: OSWorldAction) -> ActionResult:
        """Record action and return mock success."""
        self.action_history.append(action)
        logger.debug(f"Mock execute: {action.action_type.value}")

        return ActionResult(
            success=True,
            response={"status": "success", "mock": True},
            before_screenshot=self._mock_screenshot
            if self.config.screenshot_before
            else None,
            after_screenshot=self._mock_screenshot
            if self.config.screenshot_after
            else None,
        )

    def screenshot(self) -> Optional[bytes]:
        """Return mock screenshot."""
        return self._mock_screenshot

    def get_active_window(self) -> Dict[str, Any]:
        """Return mock window info."""
        return self._mock_window

    def set_mock_window(self, title: str, app: str) -> None:
        """Set mock window info for testing."""
        self._mock_window = {"title": title, "app": app}

    def set_mock_screenshot(self, data: bytes) -> None:
        """Set mock screenshot data for testing."""
        self._mock_screenshot = data

    def close(self) -> None:
        """No-op for mock."""
        pass
