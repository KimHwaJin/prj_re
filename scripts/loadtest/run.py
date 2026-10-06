#!/usr/bin/env python3
"""Bounded service-only load check with end-to-end Run/HITL latency metrics."""

import argparse
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
import json
import math
from pathlib import Path
import sys
import threading
import time

import httpx
from scenario import create_random_resource, execute, prepare_user


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://127.0.0.1:18080")
    parser.add_argument(
        "--scenario",
        choices=["approval", "submit", "crud"],
        default="approval",
    )
    parser.add_argument("--concurrency", type=int, default=4)
    parser.add_argument("--scenarios", type=int, default=20)
    parser.add_argument("--timeout", type=float, default=120)
    parser.add_argument("--poll-seconds", type=float, default=0.25)
    parser.add_argument("--output", default="var/loadtest/results.json")
    args = parser.parse_args()
    if min(
        args.concurrency, args.scenarios, args.timeout, args.poll_seconds
    ) <= 0 or not all(
        math.isfinite(v) for v in (args.timeout, args.poll_seconds)
    ):
        parser.error(
            "concurrency, scenarios, timeout and poll-seconds must be positive"
        )
    metrics = defaultdict(list)
    lock = threading.Lock()

    def record(name, ms):
        with lock:
            metrics[name].append(ms)

    def one(index):
        with httpx.Client(
            base_url=args.base_url, timeout=min(30, args.timeout)
        ) as client:

            def request(method, path, **kwargs):
                start = time.perf_counter()
                response = client.request(method, path, **kwargs)
                record("HTTP/" + method, (time.perf_counter() - start) * 1000)
                response.raise_for_status()
                return response.json()

            try:
                headers, project = prepare_user(request)
                result = (
                    create_random_resource(
                        request, headers, [project], record=record
                    )
                    if args.scenario == "crud"
                    else execute(
                        request,
                        headers,
                        project,
                        record=record,
                        timeout=args.timeout,
                        poll_seconds=args.poll_seconds,
                        submit=args.scenario == "submit",
                    )
                )
                return {"ok": True, **result}
            except Exception as exc:
                return {"ok": False, "scenario": index, "error": str(exc)}

    start = time.perf_counter()
    with ThreadPoolExecutor(max_workers=args.concurrency) as pool:
        results = list(pool.map(one, range(args.scenarios)))
    seconds = time.perf_counter() - start

    def stats(values):
        ordered = sorted(values)
        return {
            "count": len(values),
            "p50_ms": round(ordered[math.ceil(len(values) * 0.50) - 1], 2),
            "p95_ms": round(ordered[math.ceil(len(values) * 0.95) - 1], 2),
            "max_ms": round(max(values), 2),
        }

    report = {
        "target": args.base_url,
        "scenario": args.scenario,
        "concurrency": args.concurrency,
        "scenarios": args.scenarios,
        "passed": sum(r["ok"] for r in results),
        "failed": sum(not r["ok"] for r in results),
        "wall_seconds": round(seconds, 2),
        "successful_scenarios_per_second": round(
            sum(r["ok"] for r in results) / seconds, 3
        ),
        "retried_runs": sum(
            (run["attempt_count"] or 0) > 1
            for r in results
            for run in r.get("runs", [])
        ),
        "metrics": {k: stats(v) for k, v in metrics.items()},
        "results": results,
    }
    out = Path(args.output)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2))
    print(
        json.dumps(
            {k: v for k, v in report.items() if k != "results"},
            ensure_ascii=False,
            indent=2,
        )
    )
    for r in results:
        if not r["ok"]:
            print(r["error"])
    print("Report:", out)
    return 1 if report["failed"] else 0


if __name__ == "__main__":
    sys.exit(main())
