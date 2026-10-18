"""Claude investigation agent for the RMA returns network.

Starts the MCP server as a subprocess, hands its tools to Claude through the
Anthropic SDK tool runner, and prints the investigation report.

Usage:
    python -m rma_agent.agent                       # full network audit
    python -m rma_agent.agent "Why is RMA inventory aging at Reno?"

Requires Anthropic credentials (ANTHROPIC_API_KEY or an `ant auth login` profile).
"""

import asyncio
import sys

import anthropic
from anthropic.lib.tools.mcp import async_mcp_tool
from mcp import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client

MODEL = "claude-opus-5-5"

SYSTEM = """You are a senior service-parts operations investigator. You reconcile RMAs, \
3PL receiving, ERP transactions, transfers, and inventory across a global returns network.

Work the way an experienced inventory control lead does: start from the network overview, \
follow each discrepancy to its source, and keep going until the physical movement, the 3PL \
record, the ERP transaction, and the financial value tell the same story.

For each problem you find:
- Name the site and the 3PL operator, the failure mechanism, and when it started.
- Back it with evidence: counts, dollar value, date windows, and one or two traced RMAs.
- Separate 3PL or process failures from genuine customer non-returns. Customers should be \
billed only for parts that never arrived anywhere; flag any customer who would be billed \
for a part the 3PL actually received.
- Recommend a specific corrective action and who owns it.

Use only numbers returned by the tools. If a tool result is ambiguous, trace an example \
before concluding.

Finish with:
1. An executive summary table: site, issue, units, value, owner.
2. Findings in order of financial exposure, each with its evidence.
3. A short corrective-action note to each affected 3PL, ready to send."""

DEFAULT_QUESTION = (
    "Run a full reconciliation of the returns network as of the latest snapshot. "
    "Find every material discrepancy, quantify the exposure, and tell me what to fix first."
)


async def investigate(question: str) -> str:
    client = anthropic.AsyncAnthropic()
    server = StdioServerParameters(command=sys.executable, args=["-m", "rma_agent.mcp_server"])
    async with stdio_client(server) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            tools = (await session.list_tools()).tools
            runner = client.beta.messages.tool_runner(
                model=MODEL,
                max_tokens=16000,
                system=SYSTEM,
                thinking={"type": "adaptive"},
                output_config={"effort": "high"},
                betas=["server-side-fallback-2026-07-01"],
                fallbacks="default",
                tools=[async_mcp_tool(t, session) for t in tools],
                messages=[{"role": "user", "content": question}],
            )
            final = None
            async for message in runner:
                for block in message.content:
                    if block.type == "tool_use":
                        print(f"  -> {block.name}({block.input})", file=sys.stderr)
                final = message
    if final is None:
        return "No response."
    if final.stop_reason == "refusal":
        return f"Request declined: {final.stop_details}"
    return "\n".join(b.text for b in final.content if b.type == "text")


def main():
    question = " ".join(sys.argv[1:]) or DEFAULT_QUESTION
    # The MCP client runs in a task group, so API errors arrive wrapped in an ExceptionGroup
    try:
        print(asyncio.run(investigate(question)))
    except* anthropic.AuthenticationError:
        sys.exit("No valid Anthropic credentials. Set ANTHROPIC_API_KEY or run `ant auth login`.")
    except* anthropic.RateLimitError:
        sys.exit("Rate limited by the API. Try again shortly.")
    except* anthropic.APIConnectionError:
        sys.exit("Could not reach the Anthropic API. Check your network connection.")


if __name__ == "__main__":
    main()
