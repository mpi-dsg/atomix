from __future__ import annotations

from typing import Any, Awaitable, Callable, Dict, Optional

from ..runtime import AtomixRuntime
from ..transactions import CommitBlocked
from ..effects import Transaction
from ..tool_result import ToolMeta, normalize_tool_result


def run_with_atomix(
    runtime: AtomixRuntime,
    tool_name: str,
    tool_fn: Callable[..., Any],
    args: Dict[str, Any],
    trace_id: str,
    branch_id: Optional[str] = None,
) -> Any:
    """Framework-agnostic helper to wrap a tool call with Atomix.

    - Generates an epoch from trace/branch identifiers.
    - Routes through the runtime injector.
    - Records/commits effects gated by frontiers.
    """

    epoch = runtime.epochs.next(trace_id=trace_id, branch_id=branch_id)
    result, tx = runtime.run_tool(tool_name, tool_fn, args, epoch)
    return result, tx


async def run_with_atomix_async(
    runtime: AtomixRuntime,
    tool_name: str,
    tool_fn: Callable[..., Awaitable[Any]],
    args: Dict[str, Any],
    trace_id: str,
    branch_id: Optional[str] = None,
) -> tuple[Any, "Transaction"]:
    """Async variant for frameworks using async tool execution."""

    epoch = runtime.epochs.next(trace_id=trace_id, branch_id=branch_id)
    adapter = runtime.adapters.get(tool_name)
    scopes = adapter.scopes(args)
    should_auto_advance = runtime._should_auto_advance(epoch, None)
    if should_auto_advance:
        runtime._register_active_epoch(scopes, epoch)
    tx = runtime.tx_manager.begin(scopes, epoch)
    result: Any | None = None
    try:
        result = await runtime.injector.call_async(lambda: tool_fn(**args))
        meta = ToolMeta(
            tool_name=tool_name,
            trace_id=epoch.trace_id,
            branch_id=epoch.branch_id,
            attempt=0,
        )
        tool_result = normalize_tool_result(result, meta=meta)
        effect = adapter.to_effect(args, tool_result, epoch)
        runtime.tx_manager.record_effect(tx, effect)
        if should_auto_advance:
            runtime._finalize_auto_epoch(scopes, epoch)
        runtime.tx_manager.commit(tx)
        if should_auto_advance:
            runtime.tx_manager.flush_ready()
        return result, tx
    except CommitBlocked:
        return result, tx
    except Exception as exc:  # noqa: BLE001
        runtime.tx_manager.abort(tx, str(exc))
        if should_auto_advance:
            runtime._finalize_auto_epoch(scopes, epoch)
            runtime.tx_manager.flush_ready()
        raise


def wrap_middleware(
    runtime: AtomixRuntime,
    tool_name: str,
    trace_fn: Callable[[Dict[str, Any]], str],
    branch_fn: Callable[[Dict[str, Any]], Optional[str]] | None = None,
) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
    """Return a decorator to wrap framework tool functions with Atomix behavior.

    Example (sync):
        @wrap_middleware(runtime, "write", trace_fn=lambda ctx: ctx["trace"])
        def tool(args, ctx):
            ...
    """

    def decorator(call_next: Callable[..., Any]) -> Callable[..., Any]:
        def wrapper(args: Dict[str, Any], context: Dict[str, Any]) -> Any:
            trace_id = trace_fn(context)
            branch_id = branch_fn(context) if branch_fn else None
            result, _ = run_with_atomix(
                runtime=runtime,
                tool_name=tool_name,
                tool_fn=lambda **kwargs: call_next(kwargs, context),
                args=args,
                trace_id=trace_id,
                branch_id=branch_id,
            )
            return result

        return wrapper

    return decorator


def wrap_async_middleware(
    runtime: AtomixRuntime,
    tool_name: str,
    trace_fn: Callable[[Dict[str, Any]], str],
    branch_fn: Callable[[Dict[str, Any]], Optional[str]] | None = None,
) -> Callable[[Callable[..., Awaitable[Any]]], Callable[..., Awaitable[Any]]]:
    """Decorator factory to wrap async tool functions with Atomix behavior."""

    def decorator(
        call_next: Callable[..., Awaitable[Any]],
    ) -> Callable[..., Awaitable[Any]]:
        async def wrapper(args: Dict[str, Any], context: Dict[str, Any]) -> Any:
            trace_id = trace_fn(context)
            branch_id = branch_fn(context) if branch_fn else None
            epoch = runtime.epochs.next(trace_id=trace_id, branch_id=branch_id)
            adapter = runtime.adapters.get(tool_name)
            scopes = adapter.scopes(args)
            should_auto_advance = runtime._should_auto_advance(epoch, None)
            if should_auto_advance:
                runtime._register_active_epoch(scopes, epoch)
            tx = runtime.tx_manager.begin(scopes, epoch)
            result: Any | None = None
            try:
                result = await runtime.injector.call_async(
                    lambda: call_next(args, context)
                )
                meta = ToolMeta(
                    tool_name=tool_name,
                    trace_id=epoch.trace_id,
                    branch_id=epoch.branch_id,
                    attempt=0,
                )
                tool_result = normalize_tool_result(result, meta=meta)
                effect = adapter.to_effect(args, tool_result, epoch)
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

        return wrapper

    return decorator
