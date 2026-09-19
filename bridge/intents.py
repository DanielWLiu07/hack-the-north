"""bridge/intents.py — text → Intent, deterministic first (plan/roommate/03-interfaces.md §12).

    text ──► OUR grammar ──match──► Intent (source: "grammar", confidence 1.0)
               │ no match
               ▼
          Andrew's intent service  POST $ANDREW_INTENT_URL/v1/intent {text, request_id}  (source: "openai")
               │ unset, unreachable, or an answer that fails OUR schema
               ▼
          no Intent: the caller refuses. Never a guess.

Every Intent, whoever made it, is validated against bridge/intent.schema.json (strict, no extra keys).
An Intent is a request, not a job: only bridge/caretaker.py turns one into a job.
"""
from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.request
from functools import lru_cache
from pathlib import Path

SCHEMA_PATH = Path(__file__).with_name("intent.schema.json")
OBJECT_ID = re.compile(r"^[a-z][a-z0-9]*_[0-9a-f]{3,}$")          # mug_a1b2: an id, not a description
INTENT_TIMEOUT_S = 8.0


class IntentError(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code, self.message = code, message


@lru_cache(maxsize=1)
def _validator():
    import jsonschema
    schema = json.loads(SCHEMA_PATH.read_text())
    jsonschema.Draft202012Validator.check_schema(schema)
    return jsonschema.Draft202012Validator(schema)


def validate(intent: dict) -> dict:
    """The Intent, or IntentError("intent_invalid") naming the first problem."""
    errors = sorted(_validator().iter_errors(intent), key=lambda e: list(e.path))
    if errors:
        e = errors[0]
        where = "/".join(str(p) for p in e.path) or "(root)"
        raise IntentError("intent_invalid", f"{where}: {e.message}")
    return intent


# ── the grammar ─────────────────────────────────────────────────────────────────────

_LEAD = re.compile(r"^(?:(?:hey|hi|ok|okay)\s+)?(?:(?:robot|housebot|caretaker|gitspace|buddy)\s*,?\s+)?"
                   r"(?:(?:please|can you|could you|would you|will you)\s+)*")
_TAIL = re.compile(r"\s+(?:please|for me|again|now)$")
_DET = re.compile(r"^(?:my|the|a|an|our|your|his|her|their|this|that|those|these)\s+")
_WHOLE_ROOM = {"room", "the room", "my room", "everything", "house", "place", "up", "it all", "all", "things", ""}
_TEMPORAL = re.compile(r"\b(?:before|after|earlier|yesterday|today|tonight|this (?:morning|afternoon|evening)|"
                       r"last (?:night|week)|ago|noon|midnight|breakfast|lunch|dinner|at \d|\d\s*(?:am|pm)|"
                       r"\d{1,2}:\d{2})\b")

_RULES: list[tuple[str, re.Pattern]] = [(name, re.compile(rx)) for name, rx in (
    ("blame",        r"^who (?:moved|took|touched|stole|messed with|hid|put away) (?P<obj>.+)$"),
    ("blame",        r"^when did (?P<obj>.+?) (?:move|get moved|go missing|disappear|leave)$"),
    ("restore_time", r"^(?:put|set|make|get|turn) (?:the |my )?(?:room|place|desk|everything) back "
                     r"(?:to )?(?:how|like|the way|as) it was (?P<when>.+)$"),
    ("restore_time", r"^(?:make|put) (?:it|the room|everything) (?:look )?(?:like|how|the way|as) it was (?P<when>.+)$"),
    ("restore_time", r"^restore (?:the |my )?room to (?P<when>.+)$"),
    ("tidy",         r"^(?:clean|tidy|straighten)(?: up)?(?: (?P<zone>[a-z][a-z0-9 _-]*?))?$"),
    ("tidy",         r"^put (?:everything|it all|things|stuff) (?:back|away)(?: (?:on|in|at) (?P<zone>[a-z][a-z0-9 _-]*))?$"),
    ("move",         r"^(?:put|move|place|bring|take) (?P<obj>.+?) (?:on top of|onto|on|into|in|to|back on|back to|over to) "
                     r"(?P<zone>[a-z][a-z0-9 _-]*)$"),
    ("status",       r"^(?:is|was) (?:the |my )?(?:room|place|desk|everything) (?:clean|tidy|messy|ok|okay|in order|in place)$"),
    ("status",       r"^(?:room |git )?status$"),
    ("status",       r"^what(?:'s| has| is)? (?:changed|different|out of place|moved)(?: in (?:the |my )?room)?$"),
    ("status",       r"^is (?:everything|anything) (?:in|out of) place$"),
    ("point",        r"^(?:point (?:at|to)|show me) (?P<obj>.+)$"),
    ("find",         r"^where(?:'s| is| are| did i (?:leave|put)| did you see| have i left| did (?:i|we) last see)"
                     r" (?P<obj>.+?)(?: go| at)?$"),
    ("find",         r"^(?:find|locate|look for|search for|go get|fetch) (?P<obj>.+)$"),
    ("find",         r"^(?:have you seen|i can't find|i cannot find|i lost|i've lost|i misplaced) (?P<obj>.+)$"),
)]


def _clean(text: str) -> str:
    t = text.lower().replace("’", "'").replace("‘", "'")
    t = re.sub(r"[?!.,;:\"]+", " ", t)
    t = re.sub(r"\s+", " ", t).strip()
    t = _LEAD.sub("", t)
    for _ in range(2):
        t = _TAIL.sub("", t)
    return t.strip()


def _thing(phrase: str | None) -> str | None:
    if phrase is None:
        return None
    p = phrase.strip()
    for _ in range(2):
        p = _DET.sub("", p)
    return p or None


def _zone(phrase: str | None) -> str | None:
    p = _thing(phrase)
    if p is None or p in _WHOLE_ROOM:
        return None
    return re.sub(r"[\s-]+", "_", p)


def parse(text: str, request_id: str) -> dict | None:
    """OUR deterministic caretaker grammar: an Intent (validated), or None when nothing matches."""
    t = _clean(text)
    if not t:
        return None
    for name, rx in _RULES:
        m = rx.match(t)
        if not m:
            continue
        g = m.groupdict()
        obj = _thing(g.get("obj"))
        zone = _zone(g.get("zone"))
        when = (g.get("when") or "").strip() or None
        if name == "restore_time" and not (when and _TEMPORAL.search(when)):
            continue                       # "set my room back to study mode" is a named state, not a time
        if name == "tidy" and g.get("zone") and zone is None and _thing(g.get("zone")) not in _WHOLE_ROOM:
            continue
        if name == "move" and (zone is None or not obj or obj in _WHOLE_ROOM):
            continue                       # "put everything back" is tidy; "move it" has nothing to move
        oid = obj if obj and OBJECT_ID.match(obj) else None
        return validate({"request_id": request_id, "intent": name,
                         "object_query": None if oid else obj, "object_id": oid,
                         "zone": zone, "when": when, "raw_text": text.strip()[:500],
                         "confidence": 1.0, "source": "grammar"})
    return None


# ── Andrew's intent service: only when the grammar has nothing ───────────────────────────

def service_url() -> str:
    return os.getenv("ANDREW_INTENT_URL", "").strip().rstrip("/")


def from_service(text: str, request_id: str) -> dict | None:
    """POST {text, request_id} to his /v1/intent. None when the service is not configured. An answer
    that is not a valid Intent for THIS request raises IntentError: it is refused, never repaired."""
    base = service_url()
    if not base:
        return None
    token = os.getenv("ANDREW_INTENT_TOKEN", "").strip()
    req = urllib.request.Request(f"{base}/v1/intent", data=json.dumps({"text": text, "request_id": request_id}).encode(),
                                 method="POST", headers={"Content-Type": "application/json",
                                                         **({"Authorization": f"Bearer {token}"} if token else {})})
    try:
        with urllib.request.urlopen(req, timeout=INTENT_TIMEOUT_S) as r:
            answer = json.loads(r.read() or b"{}")
    except urllib.error.HTTPError as e:
        try:
            body = json.loads(e.read() or b"{}")
        except ValueError:
            body = {}
        if e.code in (404, 422) or body.get("error") in ("unknown_command", "refused", "no_intent"):
            return None                    # his layer refused too: the caller says "I didn't understand"
        raise IntentError("intent_unavailable", f"the intent service answered HTTP {e.code}") from None
    except (urllib.error.URLError, OSError, ValueError) as e:
        raise IntentError("intent_unavailable", f"the intent service did not answer: {e}") from None
    if not isinstance(answer, dict) or answer.get("intent") is None:
        return None
    validate(answer)
    if answer["request_id"] != request_id or answer["source"] != "openai":
        raise IntentError("intent_invalid", "the intent service answered for another request, or not as `openai`")
    return answer
