"""BaseAgent: agentic tool-use loop using the Anthropic SDK."""

import json
import time
from typing import Any, Callable

import anthropic

from agencybill.config import ANTHROPIC_API_KEY, MODEL, MAX_AGENT_TURNS
from agencybill.tools.audit_tools import log_audit_event
from agencybill.display.console import console, print_agent_action, print_tool_call


class BaseAgent:
    """
    Runs an agentic loop: sends messages to Claude, executes tool calls,
    feeds results back, and loops until done or max turns reached.
    """

    name: str = "BaseAgent"
    system_prompt: str = "You are a helpful insurance workflow assistant."

    def __init__(self, workflow_id: str):
        self.workflow_id = workflow_id
        self.client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
        self.tools: list[dict] = []          # Anthropic tool schemas
        self.tool_handlers: dict[str, Callable] = {}  # name → callable
        self._register_tools()

    def _register_tools(self) -> None:
        """Subclasses override to register their tools."""
        pass

    def _add_tool(self, name: str, description: str,
                   input_schema: dict, handler: Callable) -> None:
        """Register one tool."""
        self.tools.append({
            "name": name,
            "description": description,
            "input_schema": input_schema,
        })
        self.tool_handlers[name] = handler

    def _execute_tool(self, tool_name: str, tool_input: dict) -> Any:
        """Dispatch a tool call and return the result."""
        handler = self.tool_handlers.get(tool_name)
        if not handler:
            return {"error": f"Unknown tool: {tool_name}"}
        try:
            result = handler(**tool_input)
            log_audit_event(
                self.workflow_id, self.name, f"tool:{tool_name}",
                {"input": tool_input, "result_type": type(result).__name__},
            )
            print_tool_call(tool_name, str(result)[:120] if result else "")
            return result
        except Exception as exc:
            log_audit_event(
                self.workflow_id, self.name, f"tool_error:{tool_name}",
                {"input": tool_input, "error": str(exc)},
            )
            return {"error": str(exc)}

    def run(self, initial_message: str) -> str:
        """
        Run the agentic loop.
        Returns the final text response from the agent.
        """
        print_agent_action(self.name, "Starting", initial_message[:120])
        log_audit_event(self.workflow_id, self.name, "agent_start",
                        {"message": initial_message[:300]})

        messages = [{"role": "user", "content": initial_message}]
        final_text = ""

        for turn in range(MAX_AGENT_TURNS):
            response = self.client.messages.create(
                model=MODEL,
                max_tokens=4096,
                system=self.system_prompt,
                tools=self.tools,
                messages=messages,
            )

            # Collect text and tool_use blocks
            tool_calls = []
            text_parts = []
            for block in response.content:
                if block.type == "text":
                    text_parts.append(block.text)
                elif block.type == "tool_use":
                    tool_calls.append(block)

            if text_parts:
                final_text = " ".join(text_parts)
                print_agent_action(self.name, "Thinking", final_text[:200])

            # Append assistant message
            messages.append({"role": "assistant", "content": response.content})

            # If no tool calls or stop_reason is end_turn, we're done
            if response.stop_reason == "end_turn" or not tool_calls:
                break

            # Execute all tool calls and build tool_result blocks
            tool_results = []
            for tc in tool_calls:
                result = self._execute_tool(tc.name, tc.input)
                tool_results.append({
                    "type": "tool_result",
                    "tool_use_id": tc.id,
                    "content": json.dumps(result, default=str),
                })

            messages.append({"role": "user", "content": tool_results})

        log_audit_event(self.workflow_id, self.name, "agent_complete",
                        {"final_text": final_text[:300], "turns": turn + 1})
        return final_text
