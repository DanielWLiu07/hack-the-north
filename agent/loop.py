"""The agent loop: gpt-5 with the tools in agent/tools.py, one turn at a time.

One turn is one Sentry transaction, op gen_ai.invoke_agent (Sentry's AI Agents module), and
reads top to bottom as

    gen_ai.invoke_agent  gitspace
      agent.decide #1        -> gen_ai.responses       (the SDK's span for the LLM call)
      gen_ai.execute_tool  search_objects (tool.type elastic) -> es.search
      agent.decide #2        -> gen_ai.responses
      gen_ai.execute_tool  room_revert (tool.type roomctl)    -> robot.pick / robot.place ...
      agent.decide #3        -> gen_ai.responses       (the answer)

    python agent/loop.py --repo <room repo> "put my mug back the way it was before this afternoon"
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "agent"))
import obs  # noqa: E402
from tools import TOOLS, Toolbox  # noqa: E402

MODEL = os.getenv("OPENAI_AGENT_MODEL", os.getenv("OPENAI_VISION_MODEL", "gpt-5"))
MAX_STEPS = 6
MAX_OUTPUT_TOKENS = 4000   # gpt-5 reasons out of this budget before it answers
INSTRUCTIONS = (
    "You control a robot that keeps a room under version control: every commit is a snapshot of where "
    "each object was. Always search before you act, and only revert a commit sha you saw in a search "
    "result's timeline. After acting, answer in two sentences: what you did and what the robot reported. "
    "Say plainly if the robot was simulated or anything failed."
)


def run_turn(text: str, toolbox: Toolbox, client, model: str = MODEL) -> dict:
    """One user request -> tool calls -> answer. Returns {"answer", "steps", "trace"}."""
    steps, previous, pending = [], None, [{"role": "user", "content": text}]
    with obs.transaction("gen_ai.invoke_agent", "invoke_agent gitspace") as tx:
        if tx is not None:
            for k, v in (("gen_ai.operation.name", "invoke_agent"), ("gen_ai.agent.name", "gitspace"),
                         ("gen_ai.request.model", model)):
                tx.set_data(k, v)
        trace = obs.trace_fields()
        for i in range(1, MAX_STEPS + 1):
            with obs.span("agent.decide", f"decide #{i}", step=i):
                r = client.responses.create(model=model, instructions=INSTRUCTIONS, input=pending, tools=TOOLS,
                                            previous_response_id=previous, max_output_tokens=MAX_OUTPUT_TOKENS,
                                            reasoning={"effort": "low"})
            calls = [o for o in r.output if getattr(o, "type", None) == "function_call"]
            if not calls:
                steps.append({"step": i, "answer": r.output_text})
                return {"answer": r.output_text, "steps": steps, "trace": trace}
            previous, pending = r.id, []
            for c in calls:
                args = json.loads(c.arguments or "{}")
                out = toolbox.dispatch(c.name, args)
                steps.append({"step": i, "tool": c.name, "args": args, "result": out})
                pending.append({"type": "function_call_output", "call_id": c.call_id, "output": json.dumps(out)})
        return {"answer": None, "steps": steps, "trace": trace, "error": f"no answer after {MAX_STEPS} steps"}


def main() -> int:
    from dotenv import load_dotenv

    load_dotenv(ROOT / ".env")
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("text")
    ap.add_argument("--repo", required=True, help="the room repo to act on")
    ap.add_argument("--robot", choices=("mock",), default="mock",
                    help="only the mock arm here: a real arm needs roomctl's HttpRobot and a job stream")
    a = ap.parse_args()

    sys.path.insert(0, str(ROOT / "perception"))
    from keys import credential
    from openai import OpenAI

    obs.init("laptop")
    url, _ = credential("ELASTIC_URL")
    key, why = credential("ELASTIC_API_KEY")
    es = None
    if url and key:
        from elasticsearch import Elasticsearch
        es = Elasticsearch(url, api_key=key, request_timeout=30)
    oa_key, oa_why = credential("OPENAI_API_KEY")
    if not oa_key:
        print(f"agent offline: {oa_why}", file=sys.stderr)
        return 2
    res = run_turn(a.text, Toolbox(Path(a.repo), es=es, robot_name=a.robot),
                   OpenAI(api_key=oa_key, timeout=60, max_retries=0))
    obs.flush(10)
    for s in res["steps"]:
        print(json.dumps(s, default=str)[:400])
    print("\nanswer:", res["answer"])
    tid, org = res["trace"].get("sentry_trace_id"), os.getenv("SENTRY_ORG_SLUG", "").strip()
    # obs reads SENTRY_ORG_SLUG at import, before main() loads .env: build the link here
    print("trace:", res["trace"].get("sentry_url") or (f"https://{org}.sentry.io/performance/trace/{tid}/" if org and tid else tid))
    return 0 if res["answer"] else 1


if __name__ == "__main__":
    sys.exit(main())
