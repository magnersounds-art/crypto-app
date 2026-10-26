"""
trends_probe.py — OPTIONAL, MANUAL diagnostic. Never imported by the app.

    python trends_probe.py            # default term: bitcoin

It goes through the SAME guard as the app (trends_core.get_guard().request), so it obeys and updates the
same saved cooldown / spacing / cache file.  If a 429 pause is active it sends NOTHING and says so.
Run it only when you deliberately want to test live; do not put it in a loop or a startup script.
"""
import sys
from datetime import datetime, timezone

import trends_core as tc

term = sys.argv[1] if len(sys.argv) > 1 else "bitcoin"
guard = tc.get_guard()
st = guard.status(term)
print(f'Term "{term}"  | live requests allowed right now: {st["can_request"]}  ({st["reason"] or "ok"})')
if not st["can_request"] and st["next_allowed_epoch"]:
    print("Next live request possible after:", datetime.fromtimestamp(st["next_allowed_epoch"], timezone.utc).strftime("%d %b %H:%M UTC"))

out = guard.request(term)
print("\noutcome :", out["outcome"])
print("notice  :", out["notice"]["message"] or "-")
if out["error"]:
    print("ERROR   :", out["error"]["kind"], "|", out["error"]["message"], "| HTTP", out["error"]["status"],
          "| Retry-After:", out["error"]["retry_after"])
d = out["data"]
if d:
    print(f'data    : {out["data_source"]}  backend={d["backend"]}  status={d["status"]}')
    for name, sec in d["sections"].items():
        extra = (f'avg7={sec["avg7"]} avg_period={sec["avg_period"]} n={sec["n_period"]}' if name == "interest"
                 else f'items={sec["total"]}')
        print(f"  {name:9s} {sec['state']:13s} {extra}")
else:
    print("data    : none stored")
print("\nState file:", guard._store.path or "(memory only)")
