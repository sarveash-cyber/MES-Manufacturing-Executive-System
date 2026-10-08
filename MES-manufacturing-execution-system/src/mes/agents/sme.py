"""Manufacturing SME chat agent — the conversational co-pilot for sales execs.

Multi-turn chat built on ClaudeSDKClient (persistent sessions, unlike the
one-shot orchestrator). The SME explains the why/how/when of manufacturing,
grounds every number in MoneyWrench's KPI tools, may delegate to the
specialist crew, and can search the web for standards and benchmarks.

Determinism note: current Claude models expose no temperature parameter, so
"accurate but able to pivot" is enforced by the grounding protocol in the
system prompt (toggled by `strictness` in config/agents.yaml) — numbers only
from tools or cited sources, consistent answers, but always follow the user
when they redirect.
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field

from claude_agent_sdk import (AssistantMessage, ClaudeAgentOptions,
                              ClaudeSDKClient, TextBlock)

from mes.agents.config import load_config
from mes.agents.definitions import build_subagents
from mes.agents.tools import TOOL_NAMES, kpi_server

SESSION_TTL_SEC = 30 * 60

PERSONA = """You are the Manufacturing SME of MoneyWrench MES — a friendly,
plain-speaking subject-matter expert that helps SALES EXECUTIVES (not plant
engineers) understand manufacturing: why processes behave the way they do,
how the standard MES KPIs work, when problems typically occur, and what AI
agent orchestration changes about each.

Audience & style:
- Assume the reader sells software services and meets manufacturing clients.
  Explain like a seasoned consultant coaching a colleague before a meeting.
- Short answers first (2-6 sentences), then offer to go deeper. Use analogies
  sparingly and well. A little dry humour fits the house style.
- When a question maps to a screen of this app, point them to it:
  Dashboard (/ — money bleed & OEE), Subsystems (/subsystems — the full KPI
  matrix), KPI Playground (/playground — interactive formulas), The Agents
  (/agents — the crew), Sales Advisor (/advisor — ROI builder),
  Data Sources (/data — schema & feeds).

Capabilities:
- The kpi tools give you the live demo plant (12 months, 6 machines) — use
  them for any concrete number and name the machine/product/month.
- Use WebSearch for industry benchmarks, standards (ISA-95, SMED, Six Big
  Losses, GxP), and sector norms. Always give the source when you do.
- For deep numeric dives, delegate to the specialist subagents.
"""

GROUNDING_STRICT = """
Grounding protocol (strict):
- Every specific number must come from a kpi tool result or a web source you
  cite inline. If you can neither compute nor source it, say "I'd need to
  verify that" and offer the closest grounded fact instead. Never guess.
- Be consistent: the same question should get the same answer; prefer the
  KPI engine's definitions (documented on /data) over informal ones.
- Pivot on evidence, not on pressure: when the user redirects the topic or
  brings new information, follow them fully — but if they assert something
  that contradicts the data, show the data politely rather than agreeing.
"""

GROUNDING_RELAXED = """
Grounding: prefer tool results and cited sources for numbers; reasonable
industry generalisations are acceptable when clearly framed as such.
"""


def _sme_options() -> ClaudeAgentOptions:
    cfg = load_config()
    entry = (cfg.get("agents") or {}).get("manufacturing-sme") or {}
    model = entry.get("model") or cfg.get("default_model")
    strict = str(entry.get("strictness", "high")).lower() == "high"
    system = PERSONA + (GROUNDING_STRICT if strict else GROUNDING_RELAXED)
    return ClaudeAgentOptions(
        system_prompt=system,
        mcp_servers={"kpi": kpi_server()},
        allowed_tools=TOOL_NAMES + ["WebSearch", "Task"],
        agents=build_subagents(),
        permission_mode="bypassPermissions",
        max_turns=25,
        model=None if model == "inherit" else model,
    )


@dataclass
class _Session:
    client: ClaudeSDKClient
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    last_used: float = field(default_factory=time.time)


_sessions: dict[str, _Session] = {}


async def _sweep() -> None:
    now = time.time()
    for sid in [s for s, v in _sessions.items()
                if now - v.last_used > SESSION_TTL_SEC]:
        sess = _sessions.pop(sid)
        try:
            await sess.client.disconnect()
        except Exception:
            pass


async def _get(session_id: str) -> _Session:
    await _sweep()
    sess = _sessions.get(session_id)
    if sess is None:
        client = ClaudeSDKClient(options=_sme_options())
        await client.connect()
        sess = _Session(client=client)
        _sessions[session_id] = sess
    return sess


async def chat(session_id: str, message: str, page: str = ""):
    """Send one user message; yield answer text chunks as they stream."""
    sess = await _get(session_id)
    prompt = (f"[context: the user is currently viewing the '{page}' page "
              f"of MoneyWrench]\n{message}") if page else message
    async with sess.lock:
        sess.last_used = time.time()
        await sess.client.query(prompt)
        async for msg in sess.client.receive_response():
            if isinstance(msg, AssistantMessage):
                for block in msg.content:
                    if isinstance(block, TextBlock) and block.text:
                        yield block.text
        sess.last_used = time.time()


async def reset(session_id: str) -> bool:
    sess = _sessions.pop(session_id, None)
    if sess is None:
        return False
    try:
        await sess.client.disconnect()
    except Exception:
        pass
    return True
