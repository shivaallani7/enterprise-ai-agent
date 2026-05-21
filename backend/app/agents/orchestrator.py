"""
OrchestratorAgent — LangGraph multi-agent entry point.

Architecture:
  Supervisor routes the user's question to specialist agents (Jira, Code, RAG).
  Each specialist calls its tools, accumulates results into shared AgentState.
  Supervisor loops until it has enough context, then routes to the Writer node.
  Writer generates the final response which is streamed token-by-token via SSE.

  Graph topology:
    START → supervisor → [jira_agent | code_agent | rag_agent] → supervisor → ... → writer → END

Streaming:
  astream_events(version="v2") emits fine-grained events per node.
  We filter for "on_chat_model_stream" events from the "writer" node only.
  All other node activity (supervisor decisions, tool calls) is logged but not streamed.
"""
from __future__ import annotations

import structlog
from typing import AsyncGenerator

from langchain_core.messages import HumanMessage, AIMessage

from app.agents.graph import get_graph
from app.agents.state import AgentState

logger = structlog.get_logger()


class OrchestratorAgent:
    """
    Entry point for all chat requests. Wraps the compiled LangGraph graph.
    One instance per worker process — the graph itself is stateless between requests.
    """

    def __init__(self) -> None:
        self._graph = get_graph()
        logger.info("LangGraph multi-agent orchestrator ready")

    async def stream_response(
        self,
        messages: list[dict],
        system_prompt: str,
        session_id: str,
    ) -> AsyncGenerator[dict, None]:
        """
        Yields SSE payload dicts: { delta, sources, confidence, done }.

        Phase 1 — Supervisor + specialists run silently (no streaming).
        Phase 2 — Writer streams tokens to the caller.
        """
        # Build initial state
        lc_messages = []
        for m in messages:
            role    = m.get("role", "user")
            content = m.get("content", "")
            if role == "user":
                lc_messages.append(HumanMessage(content=content))
            elif role == "assistant":
                lc_messages.append(AIMessage(content=content))

        initial_state: AgentState = {
            "messages":       lc_messages,
            "tool_results":   [],
            "citations":      [],
            "agents_called":  [],
            "iteration":      0,
            "critic_retries": 0,
            "system_prompt":  system_prompt,
            "session_id":     session_id,
        }

        final_citations: list[str] = []
        streamed_any = False
        writer_pass = 0   # increments each time the writer node starts

        try:
            async for event in self._graph.astream_events(
                initial_state, version="v2"
            ):
                kind      = event.get("event", "")
                node_name = event.get("metadata", {}).get("langgraph_node", "")

                # ── Detect writer restarts (critic retry) ─────────────────────
                if kind == "on_chain_start" and node_name == "writer":
                    writer_pass += 1
                    if writer_pass > 1 and streamed_any:
                        # Critic rejected previous answer — signal frontend to clear
                        # the partial response and start fresh
                        yield {
                            "delta":      "",
                            "sources":    [],
                            "confidence": 0.9,
                            "done":       False,
                            "clear":      True,
                        }
                        streamed_any = False

                # ── Stream writer tokens to the user ─────────────────────────
                elif kind == "on_chat_model_stream" and node_name == "writer":
                    chunk = event["data"].get("chunk")
                    if chunk and chunk.content:
                        streamed_any = True
                        yield {
                            "delta":      chunk.content,
                            "sources":    [],
                            "confidence": 0.9,
                            "done":       False,
                        }

                # ── Capture citations when graph finishes ─────────────────────
                elif kind == "on_chain_end" and node_name == "writer":
                    output = event.get("data", {}).get("output", {})
                    final_citations = output.get("citations", [])

                # ── Log critic decisions ──────────────────────────────────────
                elif kind == "on_chain_end" and node_name == "critic":
                    output = event.get("data", {}).get("output", {})
                    if isinstance(output, dict):
                        next_n = output.get("next_node", "__end__")
                        retries = output.get("critic_retries", 0)
                        if next_n == "supervisor":
                            logger.info("Critic rejected — retrying", retry=retries)
                        else:
                            logger.info("Critic approved answer")

                # ── Log specialist activity (not streamed) ────────────────────
                elif kind == "on_chain_end" and node_name in (
                    "jira_agent", "code_agent", "rag_agent"
                ):
                    output = event.get("data", {}).get("output", {})
                    citations_so_far = output.get("citations", [])
                    if citations_so_far:
                        final_citations = citations_so_far
                    logger.debug(
                        "Specialist finished",
                        agent=node_name,
                        results_count=len(output.get("tool_results", [])),
                    )

            # ── Final done sentinel ───────────────────────────────────────────
            yield {
                "delta":      "",
                "sources":    list(dict.fromkeys(final_citations)),  # deduplicated
                "confidence": 0.9 if streamed_any else 0.0,
                "done":       True,
            }

        except Exception as exc:
            logger.error(
                "LangGraph orchestrator failed",
                session_id=session_id,
                error=str(exc),
                exc_info=True,
            )
            yield {
                "delta": (
                    "I encountered an error while processing your request. "
                    "Please try again or rephrase your question."
                ),
                "sources":    [],
                "confidence": 0.0,
                "done":       True,
            }
