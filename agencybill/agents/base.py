"""BaseAgent: agentic tool-use loop using the Anthropic Messages API.

Calls go to Anthropic directly, or to OpenRouter when an OpenRouter key is
configured. A key saved on the web Admin page overrides OPENROUTER_API_KEY.
OpenRouter speaks the same Messages API, so every agent uses one client path.
"""

import json
import time
from typing import Any, Callable

import anthropic

from agencybill.config import MAX_AGENT_TURNS, resolve_llm_settings
from agencybill.tools.audit_tools import log_audit_event
from agencybill.display.console import console, print_agent_action, print_tool_call
from agencybill.logging_config import get_logger

log = get_logger("agencybill.agents")

_MAX_RETRIES = 4
_RETRY_BASE_SECONDS = 1.5


class BaseAgent:
    """
    Runs an agentic loop: sends messages to Claude, executes tool calls,
    feeds results back, and loops until done or max turns reached.
    """

    name: str = "BaseAgent"
    system_prompt: str = "You are a helpful insurance workflow assistant."

    def __init__(self, workflow_id: str):
        settings = resolve_llm_settings()
        if not settings.configured:
            raise RuntimeError(
                "No LLM API key is set. Set OPENROUTER_API_KEY or ANTHROPIC_API_KEY "
                "in .env, or save an OpenRouter key on the web Admin page. "
                "A saved key overrides OPENROUTER_API_KEY and routes every agent call through OpenRouter."
            )
        self.workflow_id = workflow_id
        self.llm = settings
        client_kwargs: dict[str, Any] = {"api_key": settings.api_key}
        if settings.base_url:
            client_kwargs["base_url"] = settings.base_url
        self.client = anthropic.Anthropic(**client_kwargs)
        log.info("LLM provider=%s model=%s", settings.provider, settings.model)
        self.tools: list[dict] = []
        self.tool_handlers: dict[str, Callable] = {}
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
            log.exception("Tool %s failed", tool_name)
            log_audit_event(
                self.workflow_id, self.name, f"tool_error:{tool_name}",
                {"input": tool_input, "error": str(exc)},
            )
            return {"error": str(exc)}

    def _create_message(self, messages: list) -> Any:
        """Call the configured LLM with retries on rate limits and transient errors."""
        last_exc: Exception | None = None
        provider = self.llm.provider
        for attempt in range(_MAX_RETRIES):
            try:
                return self.client.messages.create(
                    model=self.llm.model,
                    max_tokens=4096,
                    system=self.system_prompt,
                    tools=self.tools,
                    messages=messages,
                )
            except (anthropic.RateLimitError, anthropic.APIConnectionError,
                    anthropic.InternalServerError) as exc:
                last_exc = exc
                delay = _RETRY_BASE_SECONDS * (2 ** attempt)
                log.warning(
                    "%s call failed (attempt %s/%s): %s — retrying in %.1fs",
                    provider, attempt + 1, _MAX_RETRIES, exc, delay,
                )
                time.sleep(delay)
            except anthropic.APIStatusError as exc:
                if exc.status_code and exc.status_code >= 500:
                    last_exc = exc
                    delay = _RETRY_BASE_SECONDS * (2 ** attempt)
                    log.warning(
                        "%s 5xx (attempt %s/%s): %s — retrying in %.1fs",
                        provider, attempt + 1, _MAX_RETRIES, exc, delay,
                    )
                    time.sleep(delay)
                    continue
                raise
        raise RuntimeError(f"{provider} API failed after {_MAX_RETRIES} retries: {last_exc}")

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
        turn = 0

        for turn in range(MAX_AGENT_TURNS):
            response = self._create_message(messages)

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

            messages.append({"role": "assistant", "content": response.content})

            if response.stop_reason == "end_turn" or not tool_calls:
                break

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
