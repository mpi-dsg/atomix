from __future__ import annotations

from typing import Any, Awaitable, Callable, Dict, Optional, Tuple
from uuid import uuid4

from langchain_core.runnables import RunnableConfig
from langgraph.prebuilt import ToolNode

from ..runtime import AtomixRuntime
from ..transactions import CommitBlocked
from ..tool_result import ToolMeta, normalize_tool_result


def get_ids(
    config: Optional[RunnableConfig], state: Dict[str, Any]
) -> Tuple[str, str | None]:
    cfg = (config or {}).get("configurable", {}) if isinstance(config, dict) else {}
    trace_id = cfg.get("trace_id") or cfg.get("thread_id") or str(uuid4())
    branch_id = state.get("branch_id") or cfg.get("branch_id")
    return trace_id, branch_id


def make_transactional_tool_node(
    runtime: AtomixRuntime, tool_node: ToolNode, node_name: str = "tools"
):
    """Wrap a ToolNode so each call is enrolled in Atomix."""

    def sync_node(
        state: Dict[str, Any], config: RunnableConfig | None = None
    ) -> Dict[str, Any]:
        trace_id, branch_id = get_ids(config, state)
        epoch = runtime.epochs.next(trace_id=trace_id, branch_id=branch_id)
        should_auto_advance = runtime._should_auto_advance(epoch, None)
        if should_auto_advance:
            runtime._register_active_epoch({"tools"}, epoch)
        tx = runtime.tx_manager.begin({"tools"}, epoch)
        try:
            out = tool_node.invoke(state, config=config)
            # Each tool call inside ToolNode should be adapted; here we log coarse grain
            if should_auto_advance:
                runtime._finalize_auto_epoch({"tools"}, epoch)
            runtime.tx_manager.commit(tx)
            if should_auto_advance:
                runtime.tx_manager.flush_ready()
            return out
        except CommitBlocked:
            return state
        except Exception as exc:  # noqa: BLE001
            runtime.tx_manager.abort(tx, str(exc))
            if should_auto_advance:
                runtime._finalize_auto_epoch({"tools"}, epoch)
                runtime.tx_manager.flush_ready()
            raise

    async def async_node(
        state: Dict[str, Any], config: RunnableConfig | None = None
    ) -> Dict[str, Any]:
        trace_id, branch_id = get_ids(config, state)
        epoch = runtime.epochs.next(trace_id=trace_id, branch_id=branch_id)
        should_auto_advance = runtime._should_auto_advance(epoch, None)
        if should_auto_advance:
            runtime._register_active_epoch({"tools"}, epoch)
        tx = runtime.tx_manager.begin({"tools"}, epoch)
        try:
            out = await tool_node.ainvoke(state, config=config)
            if should_auto_advance:
                runtime._finalize_auto_epoch({"tools"}, epoch)
            runtime.tx_manager.commit(tx)
            if should_auto_advance:
                runtime.tx_manager.flush_ready()
            return out
        except CommitBlocked:
            return state
        except Exception as exc:  # noqa: BLE001
            runtime.tx_manager.abort(tx, str(exc))
            if should_auto_advance:
                runtime._finalize_auto_epoch({"tools"}, epoch)
                runtime.tx_manager.flush_ready()
            raise

    return sync_node, async_node


def wrap_tool_function(
    runtime: AtomixRuntime,
    tool_name: str,
    tool_fn: Callable[..., Any],
):
    """Wrap a tool callable so each invocation is enrolled in Atomix."""

    def wrapped(
        tool_input: Dict[str, Any],
        config: RunnableConfig | None = None,
        state: Dict[str, Any] | None = None,
    ) -> Any:
        trace_id, branch_id = get_ids(config, state or {})
        epoch = runtime.epochs.next(trace_id=trace_id, branch_id=branch_id)
        adapter = runtime.adapters.get(tool_name)
        scopes = adapter.scopes(tool_input)
        should_auto_advance = runtime._should_auto_advance(epoch, None)
        if should_auto_advance:
            runtime._register_active_epoch(scopes, epoch)
        tx = runtime.tx_manager.begin(scopes, epoch)
        result: Any | None = None
        try:
            try:
                result = tool_fn(**tool_input)
            except TypeError:
                result = tool_fn(tool_input)
            meta = ToolMeta(
                tool_name=tool_name,
                trace_id=epoch.trace_id,
                branch_id=epoch.branch_id,
                attempt=0,
            )
            tool_result = normalize_tool_result(result, meta=meta)
            effect = adapter.to_effect(tool_input, tool_result, epoch)
            runtime.tx_manager.record_effect(tx, effect)
            if should_auto_advance:
                runtime._finalize_auto_epoch(scopes, epoch)
            runtime.tx_manager.commit(tx)
            if should_auto_advance:
                runtime.tx_manager.flush_ready()
            return result
        except CommitBlocked:
            return result
        except Exception as exc:  # noqa: BLE001
            runtime.tx_manager.abort(tx, str(exc))
            if should_auto_advance:
                runtime._finalize_auto_epoch(scopes, epoch)
                runtime.tx_manager.flush_ready()
            raise

    return wrapped


def wrap_tool_function_async(
    runtime: AtomixRuntime,
    tool_name: str,
    tool_fn: Callable[..., Awaitable[Any]],
):
    """Async wrapper for tool callables."""

    async def wrapped(
        tool_input: Dict[str, Any],
        config: RunnableConfig | None = None,
        state: Dict[str, Any] | None = None,
    ) -> Any:
        trace_id, branch_id = get_ids(config, state or {})
        epoch = runtime.epochs.next(trace_id=trace_id, branch_id=branch_id)
        adapter = runtime.adapters.get(tool_name)
        scopes = adapter.scopes(tool_input)
        should_auto_advance = runtime._should_auto_advance(epoch, None)
        if should_auto_advance:
            runtime._register_active_epoch(scopes, epoch)
        tx = runtime.tx_manager.begin(scopes, epoch)
        result: Any | None = None
        try:
            try:
                result = await tool_fn(**tool_input)
            except TypeError:
                result = await tool_fn(tool_input)
            meta = ToolMeta(
                tool_name=tool_name,
                trace_id=epoch.trace_id,
                branch_id=epoch.branch_id,
                attempt=0,
            )
            tool_result = normalize_tool_result(result, meta=meta)
            effect = adapter.to_effect(tool_input, tool_result, epoch)
            runtime.tx_manager.record_effect(tx, effect)
            if should_auto_advance:
                runtime._finalize_auto_epoch(scopes, epoch)
            runtime.tx_manager.commit(tx)
            if should_auto_advance:
                runtime.tx_manager.flush_ready()
            return result
        except CommitBlocked:
            return result
        except Exception as exc:  # noqa: BLE001
            runtime.tx_manager.abort(tx, str(exc))
            if should_auto_advance:
                runtime._finalize_auto_epoch(scopes, epoch)
                runtime.tx_manager.flush_ready()
            raise

    return wrapped
