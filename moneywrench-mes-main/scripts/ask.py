"""Ask the multi-agent MES analyst a question from the command line.

    python scripts/ask.py "where did I lose the most money last quarter?"

Requires ANTHROPIC_API_KEY (or an active Claude Code login).
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))


async def main() -> None:
    if len(sys.argv) < 2:
        sys.exit('usage: python scripts/ask.py "your question"')
    question = " ".join(sys.argv[1:])

    from mes.agents.orchestrator import ask_stream

    print(f"Q: {question}\n")
    async for chunk in ask_stream(question):
        print(chunk, end="", flush=True)
    print()


if __name__ == "__main__":
    asyncio.run(main())
