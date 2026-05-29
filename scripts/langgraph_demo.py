"""
LangGraph + Atomix demo using the filesystem adapter.

Creates a tiny graph that writes to /tmp/atomix_langgraph_demo.txt via Atomix,
commits with frontier advancement, and logs the effect.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import TypedDict
from uuid import uuid4

from langgraph.graph import END, START, StateGraph
from langchain_core.messages import AIMessage, HumanMessage

from atomix.adapters.filesystem import FileWriteAdapter
from atomix.integrations.langgraph import get_ids
from atomix.logging import setup_logging
from atomix.runtime import AtomixRuntime

logger = logging.getLogger("atomix.langgraph_demo")


class State(TypedDict):
    messages: list
    path: str
    content: str
    branch_id: str | None


def apply_effect(effect) -> None:
    payload = effect.payload
    path = Path(payload["path"])
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(payload["content"], encoding="utf-8")


def build_graph(runtime: AtomixRuntime):
    def start_node(state: State):
        return {
            "messages": [
                AIMessage(
                    content="Plan",
                    tool_calls=[
                        {
                            "name": "file_write",
                            "args": {"path": state["path"], "content": state["content"]},
                            "id": "call-1",
                            "type": "tool_call",
                        }
                    ],
                )
            ]
        }

    def tools_node(state: State, config=None):
        trace_id, branch_id = get_ids(config, state)
        messages = state["messages"]
        target = Path(state["path"]).resolve()
        tool_calls = []
        for msg in reversed(messages):
            if isinstance(msg, AIMessage) and msg.tool_calls:
                tool_calls = msg.tool_calls
                break
        for call in tool_calls:
            args = call["args"]
            epoch = runtime.epochs.next(trace_id=trace_id, branch_id=branch_id)

            def plan_fn(path: str, content: str):
                p = Path(path)
                before = p.read_text(encoding="utf-8") if p.exists() else None
                return {"before": before, "content": content, "path": path}

            runtime.run_tool("file_write", plan_fn, args, epoch)
            runtime.advance_frontier({str(target)}, epoch)
        return {"messages": messages, "path": str(target), "content": state["content"]}

    builder = StateGraph(State)
    builder.add_node("start", start_node)
    builder.add_node("tools", tools_node)
    builder.add_edge(START, "start")
    builder.add_edge("start", "tools")
    builder.add_edge("tools", END)
    return builder.compile()


def main() -> None:
    setup_logging(level=logging.INFO)

    runtime = AtomixRuntime(apply_effect=apply_effect)
    runtime.register_adapter("file_write", FileWriteAdapter())

    graph = build_graph(runtime)
    config = {"configurable": {"trace_id": uuid4().hex, "branch_id": "root"}}
    state = {
        "messages": [HumanMessage(content="start")],
        "path": "/tmp/atomix_langgraph_demo.txt",
        "content": "hello-atomix",
        "branch_id": "root",
    }
    result = graph.invoke(state, config=config)
    target = Path(state["path"])
    logger.info("Graph result: %s", result)
    logger.info("File content: %s", target.read_text(encoding="utf-8") if target.exists() else "(missing)")
    logger.info("Atomix log entries: %s", runtime.log.entries())


if __name__ == "__main__":
    main()
