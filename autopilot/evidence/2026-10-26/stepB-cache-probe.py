"""Step B probe: what makes the composer's cached system prompt hit or miss?

Five short calls (64 output tokens each) with the composer's real system prompt, real model and real
output_config, and a fixed one-line user message, so the only variable is what the cache prefix holds.
  1 compose schema   2 compose schema again   3 revise schema   4 revise schema again   5 compose schema again
Paid: five calls booked through agents/llm.py. Writes the usage of each to the output path (numbers only).
"""
import json, sys, time
sys.path.insert(0, ".")
from agents import composer, framer, llm

profile = framer.load_json(framer.PROFILE_PATH)
client = llm.build_client()
system = composer.load_system_prompt()
USER = [{"role": "user", "content": "Reply with the single word ready."}]


def shaped(schema, effort=composer.COMPOSE_EFFORT):
    return dict(model=llm.FRAMER_MODEL, max_tokens=64, stream=True,
                system=[{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}],
                messages=USER, output_config={"format": {"type": "json_schema", "schema": schema}, "effort": effort})


plan = [("1 compose schema", composer.COMPOSE_SCHEMA), ("2 compose schema again", composer.COMPOSE_SCHEMA),
        ("3 revise schema", composer.REVISE_SCHEMA), ("4 revise schema again", composer.REVISE_SCHEMA),
        ("5 compose schema again", composer.COMPOSE_SCHEMA)]
out = []
for label, schema in plan:
    t = time.time()
    _m, usage, cost = llm.call_message(client, user_id=profile["user_id"], phase="cache-probe", **shaped(schema))
    row = {"call": label, "input_tokens": usage.input_tokens, "cache_read": usage.cache_read_input_tokens or 0,
           "cache_write": usage.cache_creation_input_tokens or 0, "output_tokens": usage.output_tokens, "cost_usd": round(cost, 5),
           "seconds": round(time.time() - t, 1)}
    out.append(row)
    print(row, flush=True)
json.dump(out, open(sys.argv[1], "w"), indent=1)
