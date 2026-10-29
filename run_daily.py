#!/usr/bin/env python3
"""Daily pipeline: get NSE data up to date, run the screener, write results/.

results/status.json   did today's data arrive (fresh), which sources were used, errors
results/latest.json   full scan output (read by the Claude morning report)
results/latest.md     readable summary
results/history/      one JSON per data date
Env: EOD2_DIR (default /tmp/eod2_data), FORCE=1 to re-run, FINAL_ATTEMPT=1 to fail the job if data is stale.
"""
import json
import os
import subprocess
import sys

import fetch_data
import screener

ROOT = os.path.dirname(os.path.abspath(__file__))
RES = os.path.join(ROOT, "results")
EOD2 = os.environ.get("EOD2_DIR", "/tmp/eod2_data")


def main():
    os.makedirs(os.path.join(RES, "history"), exist_ok=True)
    status_path, latest_path = os.path.join(RES, "status.json"), os.path.join(RES, "latest.json")

    if not os.path.exists(os.path.join(EOD2, "daily")):
        subprocess.run(["git", "clone", "--depth", "1", "--quiet",
                        "https://github.com/BennyThadikaran/eod2_data.git", EOD2], check=True)

    hol, _ = fetch_data.holidays(EOD2)
    exp = fetch_data.expected_date(hol).isoformat()
    if os.path.exists(status_path) and os.environ.get("FORCE") != "1":
        st = json.load(open(status_path))
        if st.get("fresh") and st.get("data_date", "") >= exp:
            print(f"Already up to date for {exp}; nothing to do.")
            return 0

    status = fetch_data.update(EOD2)
    prev = json.load(open(latest_path)) if os.path.exists(latest_path) else None
    out = screener.run(EOD2, prev)
    if prev and prev.get("asof") == out["asof"]:          # same-day re-run: keep yesterday's follow-up
        out["yesterday"], out["previous_asof"] = prev.get("yesterday", []), prev.get("previous_asof")
    status["scan_asof"] = out["asof"]

    json.dump(out, open(latest_path, "w"), indent=1)
    json.dump(out, open(os.path.join(RES, "history", f"{out['asof']}.json"), "w"), indent=1)
    json.dump(status, open(status_path, "w"), indent=1)
    md = screener.to_markdown(out, status)
    open(os.path.join(RES, "latest.md"), "w").write(md)
    print(json.dumps(status, indent=1))
    print(md)

    if not status["fresh"] and os.environ.get("FINAL_ATTEMPT") == "1":
        print(f"::error::Data still stale: have {status['data_date']}, expected {exp}")
        return 1
    if not status["fresh"]:
        print(f"::warning::Data stale: have {status['data_date']}, expected {exp}; a later run will retry")
    return 0


if __name__ == "__main__":
    sys.exit(main())
