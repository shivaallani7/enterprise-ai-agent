"""
Shared state for the LangGraph multi-agent pipeline.

messages       — full conversation history (user + assistant + tool results)
tool_results   — plain-text outputs accumulated from specialist agents
citations      — source references collected across all tool calls
agents_called  — tracks which specialists have run this turn (avoids duplicates)
iteration      — safety counter; supervisor aborts after MAX_ITERATIONS
system_prompt  — persona + story/general context injected once per request
session_id     — Cosmos DB session key (for logging)
"""
from __future__ import annotations

from typing import Annotated
from typing_extensions import TypedDict

from langchain_core.messages import BaseMessage
from langgraph.graph.message import add_messages

MAX_ITERATIONS  = 6   # max supervisor→specialist cycles per request
MAX_CRITIC_RETRIES = 3   # max times critic can reject and retry


class AgentState(TypedDict):
    messages:       Annotated[list[BaseMessage], add_messages]
    tool_results:   list[str]
    citations:      list[str]
    agents_called:  list[str]
    iteration:      int
    system_prompt:  str
    session_id:     str
    next_node:      str          # set by supervisor/critic, read by conditional edge
    critic_retries: int          # incremented each time critic rejects the writer's answer
