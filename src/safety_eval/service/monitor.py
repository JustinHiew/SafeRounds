"""Monitoring CLI (roadmap 2F, decision D52). Run on the server, for example from cron.

    python -m safety_eval.service.monitor check               # print active alerts (codes and numbers)
    python -m safety_eval.service.monitor check --notify      # also POST new ones to ALERT_WEBHOOK_URL
    python -m safety_eval.service.monitor check --json
    python -m safety_eval.service.monitor report --clinic harbourline --days 14   # daily metrics incl. estimated cost

Nothing is sent without --notify and a webhook URL. The exit code is 0 whether or not there are
alerts; it is non-zero only when notification was asked for and failed.
"""

import argparse
import json
import sys

from . import monitoring
from .db import make_engine
from .settings import Settings


def describe(a):
    bits = [f"{k}={a[k]}" for k in ("direction", "value", "baseline_mean", "baseline_sd", "ceiling",
                                    "threshold", "n", "spent_usd", "limit_usd") if k in a]
    return f"{a['scope']}  {a['code']}  " + " ".join(bits)


def main(argv=None, settings=None, sender=monitoring.post_json, out=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="command", required=True)
    chk = sub.add_parser("check")
    chk.add_argument("--notify", action="store_true", help="POST new alerts to ALERT_WEBHOOK_URL")
    chk.add_argument("--json", action="store_true", help="print the alerts as JSON")
    rep = sub.add_parser("report")
    rep.add_argument("--clinic", required=True)
    rep.add_argument("--days", type=int, default=28)
    args = ap.parse_args(argv)
    out = out or sys.stdout
    settings = settings or Settings.from_env()
    if args.command == "report":
        engine = make_engine(settings.database_url)
        daily = monitoring.clinic_daily(engine, args.clinic, max(1, min(args.days, 365)))
        print(json.dumps(daily, indent=2), file=out)
        return daily
    if args.notify and not settings.alert_webhook_url:
        sys.exit("--notify needs ALERT_WEBHOOK_URL")
    engine = make_engine(settings.database_url)
    alerts = monitoring.compute_alerts(engine, settings)
    if args.json:
        print(json.dumps(monitoring.payload_for(alerts), indent=2), file=out)
    elif not alerts:
        print("no active alerts", file=out)
    else:
        for a in alerts:
            print(describe(a), file=out)
    if args.notify:
        try:
            sent = monitoring.notify(engine, settings, alerts, sender)
        except Exception as exc:       # the type only: a message could carry the URL
            sys.exit(f"notification failed ({type(exc).__name__}); nothing was recorded as sent")
        print(f"sent {len(sent)} new alert(s) of {len(alerts)} active", file=out)
    return alerts


if __name__ == "__main__":
    main()
