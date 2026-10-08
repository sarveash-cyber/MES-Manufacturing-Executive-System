"""Lead orchestrator: receives a natural-language question about the plant's
history and coordinates the specialist subagents + KPI tools to answer it."""

from __future__ import annotations

from claude_agent_sdk import AssistantMessage, ClaudeAgentOptions, TextBlock, query

from mes.agents.config import orchestrator_setting
from mes.agents.definitions import build_subagents
from mes.agents.tools import TOOL_NAMES, kpi_server

SYSTEM_PROMPT = """You are the lead manufacturing-analytics agent of an MES \
(Manufacturing Execution System). You answer questions about the plant's \
production history: schedules, OEE, downtime, scrap, maintenance, quality, \
inventory, energy, workforce — and above all where money is being lost.

Rules:
- Every number you state must come from the kpi tools (or a subagent that used \
them). Never estimate KPIs yourself.
- Start with get_data_overview if you don't know what data exists; \
get_subsystem_kpis gives the full subsystem KPI matrix in one call.
- Delegate deep dives to the specialist subagents (kpi-analyst, \
downtime-investigator, cost-leakage-hunter, schedule-auditor, \
maintenance-strategist, quality-warden, material-controller, energy-optimizer) \
when the question spans multiple areas; answer simple lookups directly.
- Answer like a seasoned plant consultant: lead with the headline finding, \
quantify in dollars and minutes, name machines/products/months, and end with \
the 2-3 highest-impact recommendations.
"""


def _options() -> ClaudeAgentOptions:
    orch = orchestrator_setting()
    kwargs = {}
    if orch["model"] != "inherit":
        kwargs["model"] = orch["model"]
    return ClaudeAgentOptions(
        system_prompt=SYSTEM_PROMPT,
        mcp_servers={"kpi": kpi_server()},
        allowed_tools=TOOL_NAMES + ["Task"],
        agents=build_subagents(),
        permission_mode="bypassPermissions",
        max_turns=orch["max_turns"],
        **kwargs,
    )


async def ask(question: str) -> str:
    """Run the orchestrator on a question and return the final answer text."""
    chunks: list[str] = []
    async for message in query(prompt=question, options=_options()):
        if isinstance(message, AssistantMessage):
            for block in message.content:
                if isinstance(block, TextBlock):
                    chunks.append(block.text)
    return "\n".join(chunks).strip() or "(the agent returned no text)"


async def ask_stream(question: str):
    """Yield answer text chunks as they arrive (for CLI streaming)."""
    async for message in query(prompt=question, options=_options()):
        if isinstance(message, AssistantMessage):
            for block in message.content:
                if isinstance(block, TextBlock):
                    yield block.text
