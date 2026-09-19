"""The agent's tool surface: retrieve (Elasticsearch) and act (roomctl -> executor -> robot).

Every tool call runs inside obs.agent_tool(name, kind), so a turn traces as
    agent.decide -> <tool span> -> es.search | robot.pick ...
Only names in TOOLS are ever dispatched (agent/README: never dispatch a name off the wire).
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for p in (ROOT, ROOT / "elastic", ROOT / "perception"):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))
import obs  # noqa: E402

TOOLS = [
    {"type": "function", "name": "search_objects", "strict": True,
     "description": "Hybrid search (BM25 + Jina + rerank) over every committed appearance of every object in "
                    "the room. Returns matching objects with their commit timeline: where each one was, when.",
     "parameters": {"type": "object", "additionalProperties": False, "required": ["query"],
                    "properties": {"query": {"type": "string", "description": "what the user called it"}}}},
    {"type": "function", "name": "room_revert", "strict": True,
     "description": "Undo ONE commit of the room: `git revert <ref>`, then the robot moves objects until the "
                    "room matches the reverted tree. Only pass a commit sha you saw in search results.",
     "parameters": {"type": "object", "additionalProperties": False, "required": ["ref", "reason"],
                    "properties": {"ref": {"type": "string", "description": "commit sha (7+ hex chars)"},
                                   "reason": {"type": "string", "description": "why this commit"}}}},
]


class Toolbox:
    """The tools, bound to one room repo, one ES client and one robot."""

    def __init__(self, repo_path: Path, es=None, robot=None, robot_name: str = "mock"):
        self.repo_path, self.es, self.robot_name = Path(repo_path), es, robot_name
        if robot is None:
            from roomctl.executor import MockRobot
            robot = MockRobot(out=lambda s: None)
        self.robot = robot   # MockRobot and HttpRobot emit robot.pick/place/drive themselves (roomctl)

    def dispatch(self, name: str, args: dict) -> dict:
        """Run one tool call. Unknown names are refused, never guessed at."""
        fn = {"search_objects": self.search_objects, "room_revert": self.room_revert}.get(name)
        if fn is None:
            return {"ok": False, "error": f"unknown tool {name!r}; allowed: {[t['name'] for t in TOOLS]}"}
        kind = {"search_objects": "elastic", "room_revert": "roomctl"}[name]
        with obs.agent_tool(name, kind=kind, **args) as sp:
            try:
                out = fn(**args)
            except Exception as e:  # noqa: BLE001 -- the model hears about it and decides
                out = {"ok": False, "error": f"{type(e).__name__}: {e}"}
            if sp is not None:
                sp.set_data("gen_ai.tool.call.result", json.dumps(out, default=str)[:500])
            return out

    def search_objects(self, query: str) -> dict:
        from queries import Queries   # elastic/queries.py: the one hybrid retriever (docs/10 GAP 3)

        if self.es is None:
            return {"ok": False, "error": "no Elasticsearch client (key parked or unset)"}
        with obs.span("es.search", "search_objects", index="room-objects", query=query) as sp:
            hits = Queries(self.es).search_objects(query, size=5)
            if sp is not None:
                sp.set_data("hits", len(hits))
        return {"ok": True, "objects": [
            {"object_id": h["object_id"], "class": h["class"], "score": round(float(h["score"] or 0), 3),
             "timeline": [{"commit": (t.get("commit_sha") or "")[:7], "at": t.get("@timestamp"), "zone": t.get("zone"),
                           "pose": {k: round(float(v), 2) for k, v in (t.get("pose") or {}).items()}}
                          for t in h["timeline"][:6]]}
            for h in hits]}

    def room_revert(self, ref: str, reason: str) -> dict:
        """roomctl's revert path (cli.cmd_apply): git decides the target tree, the executor plans
        from the room as it is, the robot runs the plan. No ES publish, no dashboard events:
        this acts on whatever repo it was given."""
        from roomctl.executor import execute, plan
        from roomctl.repo import ROBOT_EMAIL, ROBOT_NAME, Repo, load_room
        from roomctl.state import read_tree

        repo = Repo(self.repo_path)
        if repo.is_dirty():
            return {"ok": False, "error": "the room has uncommitted changes; revert would destroy them"}
        current = read_tree(repo.path)
        env = {"GIT_AUTHOR_NAME": ROBOT_NAME, "GIT_AUTHOR_EMAIL": ROBOT_EMAIL,
               "GIT_COMMITTER_NAME": ROBOT_NAME, "GIT_COMMITTER_EMAIL": ROBOT_EMAIL}
        r = repo.git("revert", "--no-edit", ref, env=env, check=False)
        if r.returncode:
            repo.git("revert", "--abort", check=False)
            return {"ok": False, "error": f"git revert {ref} failed, nothing moved: {(r.stderr or r.stdout).strip()[:200]}"}
        p = plan(current, repo.records(), load_room(repo.path))
        out = execute(p, self.robot)
        return {"ok": not (out.failed or out.skipped or p.unapplied), "reverted": ref, "new_head": repo.head()[:7],
                "robot": self.robot_name, "moved": [op.object_id for op in out.done],
                "failed": [f"{op.object_id}: {why}" for op, why in out.failed],
                "skipped": [f"{op.object_id}: {why}" for op, why in out.skipped],
                "unapplied": [str(u) for u in p.unapplied]}
