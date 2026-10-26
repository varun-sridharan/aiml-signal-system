"""Step D probe: how the citations feature behaves with the pieces the composer uses. Tiny synthetic document (no article text).
Calls go through agents/llm.py so they are booked. Prints and saves numbers/shapes only."""
import json, sys
sys.path.insert(0, ".")
from agents import llm, framer
client = llm.build_client()
profile = framer.load_json(framer.PROFILE_PATH)
DOC = ("Helio Labs released Orbit 1.0 on October 14, 2026. The SDK cut merge conflicts from 31 percent of runs to 9 percent across 212 repositories. "
       "The company measured only repositories under 2 million lines of code.")
def doc(): return {"type": "document", "source": {"type": "text", "media_type": "text/plain", "data": DOC}, "title": "s01", "citations": {"enabled": True}}
ASK = "Write two sentences about this release, one factual and one a judgement. Every factual sentence must rest on the document."
cases = {
 "A citations only (Haiku)": dict(model=llm.CHECK_MODEL, max_tokens=300),
 "B citations + json_schema (Haiku)": dict(model=llm.CHECK_MODEL, max_tokens=300, output_config={"format": {"type": "json_schema", "schema": {"type": "object", "properties": {"t": {"type": "string"}}, "required": ["t"], "additionalProperties": False}}}),
 "C citations + effort medium, streamed (Opus)": dict(model=llm.FRAMER_MODEL, max_tokens=2000, stream=True, output_config={"effort": "medium"}),
 "D citations + cache_control on the document (Opus)": dict(model=llm.FRAMER_MODEL, max_tokens=300, stream=True),
}
out = {}
for name, kw in cases.items():
    d = doc()
    if name.startswith("D"): d["cache_control"] = {"type": "ephemeral"}
    try:
        m, usage, cost = llm.call_message(client, user_id=profile["user_id"], phase="cache-probe", messages=[{"role": "user", "content": [d, {"type": "text", "text": ASK}]}], **kw)
        blocks = [{"type": b.type, "citations": [{"type": c.type, "start": getattr(c, "start_char_index", None), "end": getattr(c, "end_char_index", None), "cited_len": len(c.cited_text), "in_document": c.cited_text in DOC} for c in (getattr(b, "citations", None) or [])], "text_len": len(getattr(b, "text", "") or "")} for b in m.content]
        out[name] = {"ok": True, "stop": m.stop_reason, "blocks": blocks, "cost_usd": round(cost, 5)}
    except Exception as e:
        out[name] = {"ok": False, "error": type(e).__name__ + ": " + str(e)[:200]}
    print(name, json.dumps(out[name])[:600], flush=True)
json.dump(out, open(sys.argv[1], "w"), indent=1)
