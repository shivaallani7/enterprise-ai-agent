"""
Supervisor node — decides which specialist to invoke next, or to write the final answer.

Decision logic:
  1. Reads the user's question and accumulated tool_results.
  2. Uses structured output to pick the next step.
  3. Each specialist can only be called once per request (tracked via agents_called).
  4. After MAX_ITERATIONS or when enough context is gathered → routes to "writer".
"""
from __future__ import annotations

import re
from typing import Literal
from pydantic import BaseModel, Field
from langchain_core.messages import SystemMessage, HumanMessage
from langchain_openai import ChatOpenAI

from app.agents.state import AgentState, MAX_ITERATIONS, MAX_CRITIC_RETRIES
from app.config import get_settings

settings = get_settings()

_SUPERVISOR_SYSTEM = """\
You are a routing supervisor for a multi-agent AI assistant. Your job is to decide
which specialist agent to call next to best answer the user's question.

Available specialists:
- jira_agent   : fetches Jira story details, comments, and linked PRs
- code_agent   : searches the codebase (functions, classes, API handlers, patterns)
- rag_agent    : searches documentation, architecture records, README, runbooks, repo structure

Rules:
1. Only call each specialist once per conversation turn (unless on a critic retry pass).
2. If the already-collected context is sufficient to write a complete answer, choose "writer".
3. If you have called all relevant specialists, choose "writer".
4. MANDATORY: For ANY question about the codebase, files, repo structure, architecture,
   or project documentation — you MUST call at least one specialist (code_agent or rag_agent)
   before choosing "writer", even if no context has been collected yet.
   Only choose "writer" without any specialist calls for purely conversational questions
   (greetings, clarifications, "what can you do?").
5. If a specific Jira story is already loaded (shown in the story context), do NOT call
   jira_agent for basic story details — they are already available to the writer.
   Only call jira_agent for comments or linked PRs the user specifically asks for.
6. If the context includes a [Critic feedback] block, the previous answer was rejected.
   Read the "Search for:" instruction carefully and call the appropriate specialist
   to gather the specific information the critic asked for.
"""

_SUPERVISOR_PROMPT = """\
User question: {question}

Story/session context already loaded (story details are available to the writer):
{system_prompt_summary}

Critic retry pass: {critic_retries} of {max_critic_retries}
Specialists already called this turn: {agents_called}

Context collected so far (including any critic feedback):
{tool_results}

Which specialist should run next, or is it time to write the final answer?
Respond with your choice and a brief reason.
"""


class SupervisorDecision(BaseModel):
    next: Literal["jira_agent", "code_agent", "rag_agent", "writer"] = Field(
        description="The next specialist to call, or 'writer' to generate the final answer."
    )
    reasoning: str = Field(description="One sentence explaining the choice.")


def _build_llm() -> ChatOpenAI:
    if settings.llm_provider == "openai":
        return ChatOpenAI(
            model=settings.openai_model,
            api_key=settings.openai_api_key,
            temperature=0,
        )
    from langchain_openai import AzureChatOpenAI
    return AzureChatOpenAI(
        azure_endpoint=settings.azure_openai_endpoint,
        api_key=settings.azure_openai_api_key,
        azure_deployment=settings.azure_openai_deployment,
        openai_api_version=settings.azure_openai_api_version,
        temperature=0,
    )


_llm: ChatOpenAI | None = None


def get_supervisor_llm() -> ChatOpenAI:
    global _llm
    if _llm is None:
        _llm = _build_llm()
    return _llm


async def supervisor_node(state: AgentState) -> dict:
    """Route to the next specialist or the writer."""
    # Safety: force writer after too many iterations
    if state["iteration"] >= MAX_ITERATIONS:
        return {"iteration": state["iteration"] + 1, "next_node": "writer"}

    # Extract last user message
    user_messages = [m for m in state["messages"] if isinstance(m, HumanMessage)]
    question = user_messages[-1].content if user_messages else ""

    agents_called = state.get("agents_called", [])
    tool_results  = state.get("tool_results", [])

    critic_retries = state.get("critic_retries", 0)

    # Determine if a specific Jira story is loaded (vs. general chat)
    system_prompt = state.get("system_prompt", "")
    story_key_match = re.search(r"Jira story ([A-Z][A-Z0-9]+-\d+)", system_prompt)
    if story_key_match:
        system_prompt_summary = f"Jira story {story_key_match.group(1)} is loaded (title and description available to writer)"
    else:
        system_prompt_summary = "General chat — no specific Jira story loaded"

    prompt = _SUPERVISOR_PROMPT.format(
        question=question,
        system_prompt_summary=system_prompt_summary,
        critic_retries=critic_retries,
        max_critic_retries=MAX_CRITIC_RETRIES,
        agents_called=", ".join(agents_called) if agents_called else "none",
        tool_results="\n\n".join(tool_results) if tool_results else "none yet",
    )

    llm = get_supervisor_llm()
    decision: SupervisorDecision = await llm.with_structured_output(SupervisorDecision).ainvoke([
        SystemMessage(content=_SUPERVISOR_SYSTEM),
        HumanMessage(content=prompt),
    ])

    # Don't re-call an agent that already ran
    if decision.next != "writer" and decision.next in agents_called:
        return {"iteration": state["iteration"] + 1, "next_node": "writer"}

    return {
        "iteration": state["iteration"] + 1,
        "next_node": decision.next,
    }


def route_after_supervisor(state: AgentState) -> str:
    """Conditional edge — reads next_node set by supervisor_node."""
    return state.get("next_node", "writer")
