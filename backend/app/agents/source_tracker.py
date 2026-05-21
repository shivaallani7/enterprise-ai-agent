"""
SourceTracker — collects tool invocations from a Semantic Kernel stream
and formats them as human-readable citations for the UI.

SK streams `FunctionCallContent` when the model decides to call a tool,
and `FunctionResultContent` (added to history) when the tool returns.
We capture function names + key arguments to build citations.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field

from semantic_kernel.contents.function_call_content import FunctionCallContent
from semantic_kernel.contents.function_result_content import FunctionResultContent
from semantic_kernel.contents.streaming_chat_message_content import (
    StreamingChatMessageContent,
)


@dataclass
class SourceTracker:
    _calls: list[dict] = field(default_factory=list)
    _results: list[dict] = field(default_factory=list)

    def ingest(self, chunk: StreamingChatMessageContent) -> None:
        """Call once per streamed chunk to capture tool invocations."""
        for item in chunk.items or []:
            if isinstance(item, FunctionCallContent):
                self._calls.append({
                    "plugin": item.plugin_name or "",
                    "function": item.function_name or "",
                    "args": _safe_parse(item.arguments),
                    "call_id": item.id,
                })
            elif isinstance(item, FunctionResultContent):
                self._results.append({
                    "plugin": item.plugin_name or "",
                    "function": item.function_name or "",
                    "call_id": item.id,
                    "result_snippet": str(item.result or "")[:300],
                })

    def ingest_history_message(self, message) -> None:
        """
        After streaming completes, scan the updated ChatHistory for any
        FunctionResultContent that wasn't captured during the stream phase.
        """
        for item in message.items or []:
            if isinstance(item, FunctionResultContent):
                already = any(r["call_id"] == item.id for r in self._results)
                if not already:
                    self._results.append({
                        "plugin": item.plugin_name or "",
                        "function": item.function_name or "",
                        "call_id": item.id,
                        "result_snippet": str(item.result or "")[:300],
                    })

    def citations(self) -> list[str]:
        """
        Return a deduplicated list of citation strings for the UI.
        Format: PluginName.function_name(key_arg)
        """
        seen: set[str] = set()
        out: list[str] = []
        for call in self._calls:
            label = _format_citation(call)
            if label not in seen:
                seen.add(label)
                out.append(label)
        return out

    def confidence(self) -> float:
        """
        Heuristic: more tool calls resolved → higher confidence.
        Caps at 0.95 (never claim perfect certainty).
        """
        n = len(self._calls)
        if n == 0:
            return 0.80   # answered from model knowledge only
        return min(0.95, 0.85 + n * 0.02)

    def has_calls(self) -> bool:
        return bool(self._calls)


def _safe_parse(raw: str | None) -> dict:
    if not raw:
        return {}
    try:
        return json.loads(raw)
    except Exception:
        return {"raw": str(raw)[:100]}


def _format_citation(call: dict) -> str:
    plugin = call.get("plugin", "")
    func = call.get("function", "")
    args = call.get("args", {})

    # Pick the most informative argument as the citation key
    key_arg = (
        args.get("story_key")
        or args.get("filepath")
        or args.get("query", "")[:60]
        or ""
    )

    base = f"{plugin}.{func}" if plugin else func
    return f"{base}({key_arg})" if key_arg else base
