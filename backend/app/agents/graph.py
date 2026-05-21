"""
LangGraph multi-agent graph definition.

Topology:
    START → supervisor → jira_agent  ─┐
                       → code_agent  ─┤→ supervisor (loop) → writer → END
                       → rag_agent   ─┘

Each specialist:
  1. Calls its tools with the user's question as the query.
  2. Appends results + citations to state.
  3. Returns to supervisor.

The writer generates the final streaming response using all accumulated context.
"""
from __future__ import annotations

import json
import structlog

from langchain_core.messages import SystemMessage, HumanMessage, AIMessage, ToolMessage
from langchain_openai import ChatOpenAI
from langgraph.graph import StateGraph, END
from langgraph.prebuilt import ToolNode

from app.agents.state import AgentState, MAX_CRITIC_RETRIES
from app.agents.supervisor import supervisor_node, route_after_supervisor, get_supervisor_llm
from app.agents.tools.jira_tools import JIRA_TOOLS
from app.agents.tools.code_tools import CODE_TOOLS
from app.agents.tools.rag_tools import RAG_TOOLS
from app.config import get_settings

logger = structlog.get_logger()
settings = get_settings()

_WRITER_SYSTEM = """\
You are an expert software engineer assistant. Using ONLY the specialist context
below, write a complete, accurate answer to the user's question.

Rules:
- Your answer MUST be grounded in the specialist context. Do not use your training
  knowledge about how software is typically structured — use ONLY what the agents found.
- Cite every source with the EXACT file path from the specialist context.
- Quote specific variable names, strings, or line ranges from the context.
- Match the depth and language to the user's persona: {persona_instructions}
- If the specialist context is genuinely empty AND the story context doesn't cover the
  question, say so honestly: e.g. "The codebase search did not return relevant results
  for this question. The indexed content may not include repository structure files
  (README, directory listings)." Do NOT guess file paths or structures.
- Be concise but thorough. Use markdown for code blocks and lists.
- NEVER tell the user to search the codebase themselves (no "grep", "search for",
  "you may also want to check"). The specialist agents already searched — report
  only what they found. If a file wasn't returned by the search, do not mention it.
- NEVER suggest additional files to check beyond what the specialist context contains.
- NEVER say things like "look in your header component" or "check App.tsx" unless
  those EXACT paths appear in the specialist context below.
"""

# ── LLM factory ───────────────────────────────────────────────────────────────

def _build_llm(temperature: float = 0.1) -> ChatOpenAI:
    if settings.llm_provider == "openai":
        return ChatOpenAI(
            model=settings.openai_model,
            api_key=settings.openai_api_key,
            temperature=temperature,
        )
    from langchain_openai import AzureChatOpenAI
    return AzureChatOpenAI(
        azure_endpoint=settings.azure_openai_endpoint,
        api_key=settings.azure_openai_api_key,
        azure_deployment=settings.azure_openai_deployment,
        openai_api_version=settings.azure_openai_api_version,
        temperature=temperature,
    )


# ── Specialist agent factory ──────────────────────────────────────────────────

def _make_specialist_node(tools: list, agent_name: str):
    """
    Returns an async node function that:
      1. Calls the LLM with tool-calling enabled.
      2. Executes all requested tool calls.
      3. Collects text results + citation names into state.
    """
    llm_with_tools = _build_llm(temperature=0).bind_tools(tools)
    tool_map = {t.name: t for t in tools}

    async def node(state: AgentState) -> dict:
        user_messages = [m for m in state["messages"] if isinstance(m, HumanMessage)]
        question = user_messages[-1].content if user_messages else ""

        # The system_prompt contains the story key, title, description, and acceptance
        # criteria pre-fetched by the chat API. Specialist agents need this so they
        # know which story is active (e.g. jira_agent needs "AZ-1" to call get_jira_story).
        system_prompt = state.get("system_prompt", "")
        system_context = (
            f"\n\nActive story/session context (USE THIS to know which story is being discussed):\n{system_prompt}"
        ) if system_prompt else ""

        # Include prior agent results + critic feedback so this agent can build on them.
        # Without this, code_agent can't know that jira_agent found "rename Azure Shop to Ecom Shop",
        # and critic retry instructions ("search for ShopAzure") are never seen by the agent.
        prior_results = state.get("tool_results", [])
        prior_context = (
            "\n\nContext already gathered by previous agents (use this to decide what to search for):\n"
            + "\n\n".join(prior_results)
        ) if prior_results else ""

        system_msg = SystemMessage(content=(
            f"You are the {agent_name}. Use your tools to gather information relevant to "
            f"the user's question. You may call tools multiple times with different queries "
            f"if the first result is insufficient or empty."
            f"{system_context}"
            f"{prior_context}"
        ))

        new_results: list[str] = []
        new_citations: list[str] = []

        # Agentic tool loop — allow up to 3 rounds of tool calls so the agent
        # can reformulate queries when initial results are empty or insufficient.
        history = [system_msg, HumanMessage(content=question)]
        for _round in range(3):
            response = await llm_with_tools.ainvoke(history)

            if not response.tool_calls:
                # No more tools to call — capture any text and stop
                if response.content:
                    new_results.append(f"[{agent_name}]\n{response.content}")
                break

            # Execute each tool call and collect results
            tool_messages = []
            for tc in response.tool_calls:
                tool_fn = tool_map.get(tc["name"])
                if not tool_fn:
                    continue
                try:
                    result = await tool_fn.ainvoke(tc["args"])
                    result_str = str(result)
                    new_results.append(f"[{agent_name} / {tc['name']}]\n{result_str}")
                    # Extract file paths or story keys as citations
                    for line in result_str.splitlines():
                        if line.startswith("File:") or line.startswith("Source:") or \
                                line.startswith("Story:"):
                            ref = line.split(":", 1)[-1].strip().split(" ")[0]
                            if ref:
                                new_citations.append(ref)
                    tool_messages.append(
                        ToolMessage(content=result_str, tool_call_id=tc["id"])
                    )
                except Exception as exc:
                    logger.warning(f"{agent_name} tool call failed",
                                   tool=tc["name"], error=str(exc))
                    err = f"[{agent_name} / {tc['name']}] Error: {exc}"
                    new_results.append(err)
                    tool_messages.append(
                        ToolMessage(content=str(exc), tool_call_id=tc["id"])
                    )

            # Feed results back so LLM can decide whether to call more tools
            history = history + [response] + tool_messages

        return {
            "tool_results":  state.get("tool_results", []) + new_results,
            "citations":     state.get("citations", []) + new_citations,
            "agents_called": state.get("agents_called", []) + [agent_name],
        }

    node.__name__ = agent_name
    return node


# ── Writer node (streaming-compatible) ───────────────────────────────────────

async def writer_node(state: AgentState) -> dict:
    """
    Generates the final answer. When called via astream_events() in the
    orchestrator, token-level events from this node are streamed to the user.
    """
    from app.models.user import PERSONA_INSTRUCTIONS

    # system_prompt holds the full story context (key, title, description, AC)
    # pre-fetched by chat.py. Extract persona from it, but keep the full text
    # available as story context for the writer.
    full_system_prompt = state.get("system_prompt", "")

    # Extract persona hint — check if a "Persona context:" section is present
    if "Persona context:" in full_system_prompt:
        persona_instructions = full_system_prompt.split("Persona context:", 1)[-1].strip()
    else:
        persona_instructions = "general engineering audience"

    tool_results = state.get("tool_results", [])
    tool_context = "\n\n".join(tool_results)
    if not tool_context:
        tool_context = "No specialist context was collected."

    logger.info(
        "Writer invoked",
        tool_result_count=len(tool_results),
        context_chars=len(tool_context),
        context_preview=tool_context[:300],
    )

    system = _WRITER_SYSTEM.format(persona_instructions=persona_instructions)

    user_messages = [m for m in state["messages"] if isinstance(m, HumanMessage)]
    question = user_messages[-1].content if user_messages else ""

    writer_llm = _build_llm(temperature=0.1)

    messages_to_send = [SystemMessage(content=system)]

    # Always include the story/session context so the writer can answer questions
    # like "give me the details about the story" without needing a jira tool call.
    if full_system_prompt:
        messages_to_send.append(
            SystemMessage(content=f"Story/session context (always available):\n\n{full_system_prompt}")
        )

    messages_to_send.append(SystemMessage(content=(
        "═══ SPECIALIST SEARCH RESULTS — USE EXACT FILE PATHS FROM HERE ═══\n\n"
        f"{tool_context}\n\n"
        "═══ END OF SPECIALIST RESULTS ═══\n\n"
        "Ground your answer in the story context and/or the specialist results above. "
        "Do not mention any file path that does not appear in the specialist results."
    )))
    messages_to_send.append(HumanMessage(content=question))

    response = await writer_llm.ainvoke(messages_to_send)

    return {"messages": [AIMessage(content=response.content)]}


# ── Critic node ──────────────────────────────────────────────────────────────

_CRITIC_SYSTEM = """\
You are a strict QA critic for an AI assistant that answers software engineering
questions about a SPECIFIC indexed codebase.

The specialist agents have access to a real code search index. If they ran, the
specialist context below contains ACTUAL file paths and content from that codebase.

─── REJECT the answer if ANY of these are true ───────────────────────────────

1. GENERIC ADVICE: The answer says things like "look in your header component",
   "check App.tsx or index.html", "search for occurrences", or gives a list of
   file TYPES to check — without naming the ACTUAL files from the specialist context.
   A correct answer names real files like "frontend/components/layout/Header.tsx".

2. TELLS USER TO SEARCH: The answer says "you should search", "use grep",
   "find occurrences", or "search your codebase" — the agent should have already
   done the searching. If it didn't, reject so it can try again.

3. IGNORES SPECIALIST CONTEXT: The specialist context contains real file paths
   and content, but the draft answer doesn't reference any of them.

4. HALLUCINATED PATHS: The answer mentions file paths NOT present in the
   specialist context.

5. EMPTY CONTEXT + CODEBASE QUESTION: The context is empty but the question
   clearly needs code search (e.g. "what files need changing", "where is X defined").

─── APPROVE the answer if ────────────────────────────────────────────────────

- It names SPECIFIC files, line numbers, or function names from the specialist context.
- The specialist context is genuinely empty AND the answer honestly says so.
- The question is conversational and doesn't require code lookup.

─── retry_instruction (when rejecting) ──────────────────────────────────────

Write a specific instruction for the code_agent or rag_agent, e.g.:
  "Search code index for 'Azure Shop' OR 'ShopAzure' to find exact file paths
   and line content where the app name is displayed."
"""

_CRITIC_PROMPT = """\
User question:
{question}

Specialist context collected (ACTUAL search results from the codebase):
{tool_results}

Draft answer:
{draft_answer}

Is the draft answer grounded in the specialist context above, or is it generic advice?
"""


from pydantic import BaseModel as _BaseModel, Field as _Field
from typing import Literal as _Literal


class _CriticDecision(_BaseModel):
    approved: bool = _Field(description="True if the answer is grounded and addresses the question.")
    reason: str = _Field(description="One sentence explaining the decision.")
    retry_instruction: str = _Field(
        description=(
            "If rejected: what the supervisor should search for differently next time. "
            "Be specific — name the file type, function, or concept to look for. "
            "Empty string if approved."
        )
    )


async def critic_node(state: AgentState) -> dict:
    """
    Evaluates the writer's draft answer.
    - Approved  → routes to END via next_node = '__end__'
    - Rejected  → appends critic feedback to tool_results, resets agents_called,
                  routes back to supervisor for a smarter retry.
    Hard cap: after MAX_CRITIC_RETRIES rejections, approve regardless to avoid loops.
    """
    from langchain_core.messages import AIMessage, HumanMessage

    critic_retries = state.get("critic_retries", 0)

    # Hard cap — stop retrying after MAX_CRITIC_RETRIES
    if critic_retries >= MAX_CRITIC_RETRIES:
        logger.info("Critic retry limit reached — approving", retries=critic_retries)
        return {"next_node": "__end__"}

    # Get the latest draft answer from writer
    ai_messages = [m for m in state["messages"] if isinstance(m, AIMessage)]
    draft = ai_messages[-1].content if ai_messages else ""

    # Get original question
    user_messages = [m for m in state["messages"] if isinstance(m, HumanMessage)]
    question = user_messages[-1].content if user_messages else ""

    tool_results = state.get("tool_results", [])
    tool_context = "\n\n".join(tool_results[:5]) if tool_results else "No specialist context collected."

    critic_llm = _build_llm(temperature=0).with_structured_output(_CriticDecision)

    try:
        raw = await _build_llm(temperature=0).with_structured_output(
            _CriticDecision, include_raw=True
        ).ainvoke([
            SystemMessage(content=_CRITIC_SYSTEM),
            HumanMessage(content=_CRITIC_PROMPT.format(
                question=question,
                tool_results=tool_context,
                draft_answer=draft,
            )),
        ])
        # include_raw=True returns {"raw": msg, "parsed": model, "parsing_error": ...}
        decision: _CriticDecision = raw["parsed"]
        if decision is None:
            raise ValueError("Parsed output is None")
    except Exception as exc:
        # If critic itself fails, approve to avoid blocking the user
        logger.warning("Critic LLM call failed — approving", error=str(exc))
        return {"next_node": "__end__"}

    if decision.approved:
        logger.info("Critic approved answer", retries=critic_retries)
        return {"next_node": "__end__"}

    # Rejected — feed back the retry instruction and reset for another pass
    logger.info(
        "Critic rejected answer",
        retries=critic_retries,
        reason=decision.reason,
        instruction=decision.retry_instruction,
    )
    feedback = (
        f"[Critic feedback — retry {critic_retries + 1}/{MAX_CRITIC_RETRIES}]\n"
        f"Reason: {decision.reason}\n"
        f"Search for: {decision.retry_instruction}"
    )
    # Return a plain dict — no Pydantic objects
    return {
        "next_node":      "supervisor",
        "critic_retries": critic_retries + 1,
        "tool_results":   list(tool_results) + [feedback],
        "agents_called":  [],
        "iteration":      0,
    }


def route_after_critic(state: AgentState) -> str:
    """Conditional edge after critic — routes to supervisor (retry) or END."""
    return state.get("next_node", "__end__")


# ── Build the compiled graph ──────────────────────────────────────────────────

def build_graph():
    graph = StateGraph(AgentState)

    # Nodes
    graph.add_node("supervisor", supervisor_node)
    graph.add_node("jira_agent",  _make_specialist_node(JIRA_TOOLS,  "jira_agent"))
    graph.add_node("code_agent",  _make_specialist_node(CODE_TOOLS,  "code_agent"))
    graph.add_node("rag_agent",   _make_specialist_node(RAG_TOOLS,   "rag_agent"))
    graph.add_node("writer",      writer_node)
    graph.add_node("critic",      critic_node)

    # Entry
    graph.set_entry_point("supervisor")

    # Supervisor routes conditionally
    graph.add_conditional_edges(
        "supervisor",
        route_after_supervisor,
        {
            "jira_agent": "jira_agent",
            "code_agent": "code_agent",
            "rag_agent":  "rag_agent",
            "writer":     "writer",
        },
    )

    # All specialists return to supervisor for re-evaluation
    graph.add_edge("jira_agent", "supervisor")
    graph.add_edge("code_agent", "supervisor")
    graph.add_edge("rag_agent",  "supervisor")

    # Writer → critic (always)
    graph.add_edge("writer", "critic")

    # Critic → supervisor (retry) or END (approved)
    graph.add_conditional_edges(
        "critic",
        route_after_critic,
        {
            "supervisor": "supervisor",
            "__end__":    END,
        },
    )

    return graph.compile()


# Singleton compiled graph
_graph = None

def get_graph():
    global _graph
    if _graph is None:
        _graph = build_graph()
    return _graph
