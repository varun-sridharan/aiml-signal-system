import json, sys, unittest.mock
sys.path.insert(0, ".")
from datetime import datetime, timezone
from fastapi.testclient import TestClient
import app as site
from agents import briefs, budget, runs
from tests.test_digest import Borrowed

conn = budget.connect(autocommit=False); conn.execute("SELECT 1")
art = {"day": conn.execute("SELECT body FROM run_artifacts WHERE run_id = %s AND stage = %s ORDER BY attempt DESC LIMIT 1", ("2026-W43-r3", "gates")).fetchone()[0]["day"]}
reader_out = runs.stage_output(conn, "2026-W43-r3", "reader")
day = art["day"]
edition = json.load(open("autopilot/evidence/2026-10-21/stepC-compose-run8/edition.json"))
body = {**day, "edition": edition, "window": reader_out["window"]}
NOW = datetime(2026, 10, 20, 12, 0, tzinfo=timezone.utc)
runs.open_run(conn, "1999-W20", NOW)
briefs.publish(conn, "1999-W20", "1999-W20", body, NOW)
with unittest.mock.patch.object(site, "_connect", Borrowed(conn)):
    c = TestClient(site.app)
    r = c.get("/weekly-digest?week=1999-W20")
    open(sys.argv[1], "w").write(r.text)
    print(r.status_code, len(r.text))
    old = c.get("/weekly-digest?week=2026-W40")
    open(sys.argv[2], "w").write(old.text)
    print("legacy", old.status_code, len(old.text))
    a = c.get("/weekly-digest?tab=archive")
    open(sys.argv[3], "w").write(a.text); print("archive", a.status_code)
    sm = c.get("/weekly-digest/sample-2026-08-04"); print("sample", sm.status_code, len(sm.text))
conn.rollback(); conn.close()
