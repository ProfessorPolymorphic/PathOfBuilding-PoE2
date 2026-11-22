"""Terminal chat with the build agent.

    app/api/.venv/bin/python app/api/chat.py                     # test build (Shaman)
    app/api/.venv/bin/python app/api/chat.py --code-file code.txt
    app/api/.venv/bin/python app/api/chat.py --xml build.xml
    app/api/.venv/bin/python app/api/chat.py -q "why is my cold max hit low?"   # one-shot

In the chat: /reset clears the conversation, /tools toggles tool-call output,
/quit exits.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

import buildcode
import settings
from agent import Agent, ToolEvent
from engine import EngineWorker

DEFAULT_BUILD = Path(__file__).resolve().parent / "tests" / "fixtures" / "shaman.xml"
DIM, RESET, BOLD = "\033[2m", "\033[0m", "\033[1m"


def load_xml(args: argparse.Namespace) -> str:
    if args.code_file:
        text = Path(args.code_file).read_text().strip()
        return text if buildcode.looks_like_xml(text) else buildcode.decode(text)
    return Path(args.xml or DEFAULT_BUILD).read_text()


async def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    source = parser.add_mutually_exclusive_group()
    source.add_argument("--code-file", help="file containing a PoB share code")
    source.add_argument("--xml", help="PoB build XML file")
    parser.add_argument("-q", "--question", help="ask one question and exit")
    args = parser.parse_args()
    settings.require_llm()

    show_tools = True

    def on_tool(e: ToolEvent) -> None:
        if show_tools:
            mark = "→" if e.ok else "✗"
            shown = {k: (v if len(str(v)) < 80 else str(v)[:77] + "...") for k, v in e.args.items()}
            print(f"{DIM}  {mark} {e.name}({', '.join(f'{k}={v!r}' for k, v in shown.items())})"
                  f"{'' if e.ok else '  ' + e.summary}{RESET}")

    worker = EngineWorker()
    print(f"{DIM}Starting engine...{RESET}", end="", flush=True)
    await worker.start()
    summary = await worker.call("load_xml", xml=load_xml(args), name="build")
    print(f"\r{DIM}Loaded level {summary['level']} {summary['class']} / {summary['ascendancy']}, "
          f"main skill {summary['mainSkill']}. Model: {settings.LLM_MODEL}{RESET}")

    agent = Agent(engine_call=worker.call, on_tool=on_tool)
    try:
        if args.question:
            print(await agent.ask(args.question))
            return
        print(f"{DIM}Ask about your build. /reset, /tools, /quit{RESET}")
        while True:
            try:
                question = (await asyncio.to_thread(input, f"\n{BOLD}you>{RESET} ")).strip()
            except EOFError:
                break
            if not question:
                continue
            if question in ("/quit", "/exit"):
                break
            if question == "/reset":
                agent.reset()
                print(f"{DIM}Conversation cleared (build changes from set_config are kept).{RESET}")
                continue
            if question == "/tools":
                show_tools = not show_tools
                print(f"{DIM}Tool output {'on' if show_tools else 'off'}.{RESET}")
                continue
            try:
                answer = await agent.ask(question)
            except Exception as e:  # network/VPN/quota problems shouldn't kill the session
                print(f"\nError talking to the model: {e}", file=sys.stderr)
                continue
            print(f"\n{answer}\n{DIM}[{agent.tokens_used:,} tokens this session]{RESET}")
    finally:
        await worker.close()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
