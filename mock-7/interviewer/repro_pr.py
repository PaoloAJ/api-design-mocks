"""Reproduce every planted defect in mock-7's PR. Run with the patch applied."""
import sys
sys.path.insert(0, "..")

from app import create_app
from app.middleware import reset_rate_limits
from app.store import store
from seed import seed

A = {"Authorization": "Bearer key_acme"}
G = {"Authorization": "Bearer key_globex"}


def fresh():
    store.reset()
    reset_rate_limits()
    seed()
    return create_app().test_client()


def tpl(c, h=A):
    return c.get("/v1/templates", headers=h).get_json()["data"][0]["id"]


def send(c, to, h=A):
    return c.post("/v1/messages", json={
        "to": to, "subject": "Hi", "template_id": tpl(c, h)}, headers=h)


print("=" * 66)
print("D1  SECURITY: X-Delivery-Account forges tenant scope (SPEC 3)")
print("=" * 66)
c = fresh()
mine = c.get("/v1/messages", headers=A).get_json()["data"]
theirs = c.get("/v1/messages", headers={**A, "X-Delivery-Account": "acct_globex"}
               ).get_json()["data"]
print("acme sees own messages:        %d" % len(mine))
print("acme + forged header sees:     %d  -> %s" % (
    len(theirs), [m["to"] for m in theirs]))
print("Any customer reads any account's mail by guessing an account id.\n")

print("=" * 66)
print("D2  SECURITY: ingest lost account scoping (SPEC 4)")
print("=" * 66)
c = fresh()
victim = send(c, "victim@acme.test").get_json()["id"]
r = c.post("/v1/events", json={"events": [
    {"message_id": victim, "type": "message.bounced", "detail": "forged"}]},
    headers=G).get_json()
print("globex posting an event on acme's message: accepted=%d" % r["accepted"])
print("Cross-tenant write. Also poisons D3's suppression list.\n")

print("=" * 66)
print("D3  DEEP: suppression silently drops mail, returns 200 (SPEC shape)")
print("=" * 66)
c = fresh()
m = send(c, "repeat@acme.test").get_json()["id"]
c.post("/v1/messages/%s/transition" % m, json={"status": "sending"}, headers=A)
c.post("/v1/messages/%s/transition" % m,
       json={"status": "bounced", "reason": "550"}, headers=A)
r = send(c, "repeat@acme.test")
print("resend after bounce -> HTTP %s  body=%s" % (r.status_code, r.get_json()))
print("No id, no Location, no 202. Caller storing the id gets a KeyError;")
print("caller treating 200 as success believes the mail was queued.\n")

print("=" * 66)
print("D4  FULL SCAN: GET /messages/{id} loads the whole collection (SPEC 1)")
print("=" * 66)
c = fresh()
t = tpl(c)
for i in range(60):
    store.create("messages", {"account_id": "acct_acme", "to": "bulk%d@acme.test" % i,
                              "subject": "Hi", "template_id": t, "template_name": "welcome",
                              "variables": {}, "status": "queued", "attempts": 0,
                              "last_error": None})
m = c.get("/v1/messages", headers=A).get_json()["data"][0]["id"]
calls = {"n": 0}
orig = store.list
def counted(*a, **k):
    calls["n"] += 1
    return orig(*a, **k)
store.list = counted
c.get("/v1/messages/" + m, headers=A)
store.list = orig
print("store.list() calls for ONE keyed GET: %d" % calls["n"])
print("Detail endpoint is now O(collection). Send path scans too (D3).\n")

print("=" * 66)
print("D5  CORRECTNESS: events filtered after pagination (SPEC 2)")
print("=" * 66)
c = fresh()
target = send(c, "target@acme.test").get_json()["id"]
noise = send(c, "noise@acme.test").get_json()["id"]
def ev(mid):
    store.create("events", {"account_id": "acct_acme", "message_id": mid,
                            "type": "message.delivered", "detail": None})
ev(target)            # oldest event -> sorts LAST (newest-first order)
import time
time.sleep(0.01)      # distinct ms timestamps, so ordering is unambiguous
for _ in range(30):   # 30 newer noise events push it past the 20-row page
    ev(noise)
r = c.get("/v1/events?message_id=" + target, headers=A).get_json()
print("events for target message: %d   (correct answer: 1)" % len(r["data"]))
print("has_more=%s  -> the row exists but sits past the 20-row page, and the"
      % r["has_more"])
print("filter runs AFTER slicing, so the caller is told there are none.")

print("=" * 66)
print("D6  SECURITY: API key written to the request log (SPEC 7)")
print("=" * 66)
import logging, io
buf = io.StringIO()
app = create_app()
app.logger.addHandler(logging.StreamHandler(buf))
app.logger.setLevel(logging.INFO)
store.reset(); reset_rate_limits(); seed()
app.test_client().get("/v1/messages", headers=A)
line = [l for l in buf.getvalue().splitlines() if "auth=" in l]
print("log line: %s" % (line[0] if line else "(none)"))
print("Bearer token in plaintext logs -> anyone with log access can send mail.")
