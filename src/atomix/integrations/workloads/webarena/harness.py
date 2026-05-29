"""Minimal WebArena harness for Atomix integration tests."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from atomix.effects import Effect
from atomix.runtime import AtomixRuntime

from .adapters import WebArenaActionAdapter

try:  # optional dependency
    import browser_env  # type: ignore
except Exception:  # pragma: no cover
    browser_env = None


@dataclass
class WebArenaTask:
    id: str
    name: str
    steps: List[Dict[str, Any]]
    verify: Optional[Callable[[], bool]] = None
    config_file: Optional[Path] = None


class WebArenaHarness:
    def __init__(
        self,
        *,
        use_real_env: bool = False,
        env_kwargs: Optional[Dict[str, Any]] = None,
    ) -> None:
        self._applied_effects: List[Effect] = []
        self._use_real_env = use_real_env
        self._env_kwargs = env_kwargs or {}
        self._env = None
        self._last_obs: Optional[Dict[str, Any]] = None
        self._last_info: Optional[Dict[str, Any]] = None

        if self._use_real_env:
            self._env = _create_webarena_env(self._env_kwargs)

    def _apply_effect(self, effect: Effect) -> None:
        self._applied_effects.append(effect)
        if not self._env:
            return
        payload = effect.payload if isinstance(effect.payload, dict) else {}
        action = payload.get("action") or payload.get("result")
        if isinstance(action, dict) and "action" in action:
            action = action.get("action")
        if action is None and isinstance(payload.get("args"), dict):
            action = _action_from_args(payload["args"])
        if isinstance(action, str):
            payload["action"] = action
            action = _parse_action(action)
        if action is None:
            return
        obs, _, _, _, info = self._env.step(action)
        self._last_obs = obs
        self._last_info = info

    def _setup_runtime(self) -> AtomixRuntime:
        runtime = AtomixRuntime(apply_effect=self._apply_effect, effect_log_path=None)
        runtime.register_adapter("webarena_action", WebArenaActionAdapter())
        return runtime

    def run_atomix(self, task: WebArenaTask) -> Dict[str, Any]:
        runtime = self._setup_runtime()
        if self._env:
            if task.config_file:
                self._env.reset(options={"config_file": str(task.config_file)})
            else:
                self._env.reset()
        for step in task.steps:
            args = dict(step.get("args", {}))
            epoch = runtime.epochs.next(trace_id=task.id)
            runtime.run_tool("webarena_action", lambda **kwargs: kwargs, args, epoch)
            runtime.advance_frontier(
                runtime.adapters.get("webarena_action").scopes(args), epoch
            )
        success = task.verify() if task.verify else True
        return {
            "task_id": task.id,
            "success": success,
            "effects_applied": len(self._applied_effects),
            "last_observation": self._last_obs,
            "last_info": self._last_info,
        }

    def close(self) -> None:
        if self._env:
            self._env.close()

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()


def _resolve_webarena_root(repo_root: Path) -> Path:
    env_root = os.environ.get("WEBARENA_DATA_DIR") or os.environ.get("WEBARENA_ROOT")
    if env_root:
        return Path(env_root).expanduser()
    data_root = Path(os.environ.get("DATA_ROOT", str(repo_root / "data"))).expanduser()
    data_candidate = data_root / "webarena"
    if data_candidate.exists():
        return data_candidate
    raise FileNotFoundError(
        "WebArena repo not found. Set WEBARENA_DATA_DIR/WEBARENA_ROOT or populate "
        "DATA_ROOT/webarena (via scripts/download_data.sh)."
    )


def _create_webarena_env(env_kwargs: Dict[str, Any]):
    import sys

    repo_root = Path(__file__).resolve().parents[5]
    webarena_root = _resolve_webarena_root(repo_root)
    if not webarena_root.exists():
        raise FileNotFoundError(
            "WebArena repo not found. Set WEBARENA_DATA_DIR/WEBARENA_ROOT or populate "
            "DATA_ROOT/webarena (via scripts/download_data.sh)."
        )
    if str(webarena_root) not in sys.path:
        sys.path.insert(0, str(webarena_root))

    from browser_env import ScriptBrowserEnv

    return ScriptBrowserEnv(**env_kwargs)


def _parse_action(raw: str):
    from browser_env import create_id_based_action

    return create_id_based_action(raw)


def _action_from_args(args: Dict[str, Any]) -> Optional[str]:
    raw = args.get("action") or args.get("raw_action") or args.get("action_str")
    if isinstance(raw, str):
        return raw

    action_type = args.get("action_type")
    if not action_type:
        return None

    action_type = str(action_type).lower()
    element_id = args.get("element_id") or args.get("element")
    text = args.get("text") or args.get("input")
    direction = args.get("direction")
    url = args.get("url")
    key_comb = args.get("key_comb") or args.get("keys") or args.get("key")
    page_number = args.get("page_number") or args.get("page_id")
    answer = args.get("answer")

    if action_type == "click" and element_id:
        return f"click [{element_id}]"
    if action_type == "type" and element_id:
        text_value = "" if text is None else str(text)
        return f"type [{element_id}] [{text_value}]"
    if action_type == "hover" and element_id:
        return f"hover [{element_id}]"
    if action_type == "scroll" and direction:
        return f"scroll [{direction}]"
    if action_type == "press" and key_comb:
        return f"press [{key_comb}]"
    if action_type in {"goto", "goto_url"} and url:
        return f"goto [{url}]"
    if action_type == "new_tab":
        return "new_tab"
    if action_type in {"close_tab", "page_close"}:
        return "close_tab"
    if action_type == "go_back":
        return "go_back"
    if action_type == "go_forward":
        return "go_forward"
    if action_type == "page_focus" and page_number is not None:
        return f"page_focus [{page_number}]"
    if action_type == "stop":
        return f"stop [{answer}]" if answer is not None else "stop"

    return None
