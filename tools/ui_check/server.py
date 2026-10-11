"""Local demo of the reviewer UI: fake model (no API calls), real de-identifier, seeded queue.

Signs in as lead@clinic.example / correct-horse-battery (clinic harbourline) on http://127.0.0.1:8765/ui.
For local testing only: fixed session secret, insecure cookies.
"""
import copy, json, re, sys
from pathlib import Path
import uvicorn
from fastapi.testclient import TestClient
from safety_eval.deid import deidentify_conversation
from safety_eval.schemas import CRITERIA
from safety_eval.service import admin
from safety_eval.service.app import create_app
from safety_eval.service.settings import Settings
from safety_eval.llm import ModelResponse

ROOT = Path(sys.argv[1]); DB = sys.argv[2]
hl = json.loads((ROOT / "tests/fixtures/clinics/harbourline_conversations.json").read_text())

def turns_of(prompt):
    return re.findall(r"Turn \d+, [^:]+: (.*)", prompt)

class Scripted:
    """Picks a verdict pattern from the conversation id in the prompt."""
    model = "fake-sonnet"; max_tokens = 16000; n = 0
    def complete(self, system, prompt, cache_prefix=None):
        staff = [t for i, t in enumerate(turns_of(prompt)) if i % 2 == 1]
        quote = staff[-1][:40]
        Scripted.n += 1; kind = Scripted.n % 4
        v = {c: {"evidence": [], "analysis": "Consistent with the protocol.", "verdict": "pass"} for c in CRITERIA}
        sev = "none"
        if kind == 1:
            v["scope"] = {"evidence": [quote], "analysis": "The peer worker advises on medication, which the protocol reserves for the client's doctor.", "verdict": "fail"}
            v["safety"] = {"evidence": [quote], "analysis": "Changing a prescribed dose without the prescriber could cause harm.", "verdict": "fail"}
            sev = "high"
        elif kind == 2:
            v["escalation"] = {"evidence": [quote], "analysis": "Unclear whether the client's words meet the urgent tier.", "verdict": "unsure"}
        elif kind == 3:
            v["grounding"] = {"evidence": ["a quote that is not in the conversation"], "analysis": "x", "verdict": "fail"}
            sev = "low"
        flag = any(x["verdict"] == "fail" for x in v.values())
        return ModelResponse(text=json.dumps({"criteria": v, "overall_flag": flag, "severity": sev}),
                             model=self.model, latency_s=0.1, raw={"usage": {}})

settings = Settings(database_url=f"sqlite:///{DB}", session_secret="x" * 48, cookie_secure=False,
                    spend_limit_usd=5.0)
# The fake model has no published price; give it Sonnet's so the budget guard can cost it.
from safety_eval.service import budget; budget.PRICES["fake-sonnet"] = budget.PRICES["claude-sonnet-5-5"]
app = create_app(settings, Scripted(), deidentify_conversation)
admin.create_clinic(app.state.engine, "harbourline")
key = admin.create_key(app.state.engine, "harbourline")
admin.add_reviewer(app.state.engine, "harbourline", "lead@clinic.example", "admin", "correct-horse-battery")
c = TestClient(app)
for n in range(1, 13):
    conv = copy.deepcopy(hl[(n - 1) % 3]); conv["conversation_id"] = f"hl-{n:03d}"
    if n == 1:
        conv["turns"][0]["text"] = "Hi, I'm Mia. My number is 0412 345 678. " + conv["turns"][0]["text"]
    r = c.post("/v1/evaluate", headers={"Authorization": f"Bearer {key}"}, json={"conversation": conv})
    print(n, r.status_code, r.json().get("result", {}).get("outcome"), flush=True)
uvicorn.run(app, host="127.0.0.1", port=8765, log_level="warning")
