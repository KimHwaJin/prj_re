"""Reconcile saver telemetry with selected persisted checkpoint rows."""

from collections import Counter, defaultdict
from statistics import mean
from analyze import analyze, quantile


def key(row):
    return row["thread_id"], row["namespace"], row["checkpoint_id"]


def analyze_checkpoint(raw):
    flow = analyze(raw)
    assert raw["config"]["checkpoint_profile"]
    profile = raw["checkpoint_profile"]
    cps, blobs, writes = (
        profile[k] for k in ("checkpoints", "blobs", "writes")
    )
    calls = raw["server"]["checkpoint_calls"]
    assert cps and calls and all(c["error"] is None for c in calls)
    threads = {c["thread_id"] for c in calls}
    assert threads == {c["thread_id"] for c in cps}
    assert len(threads) == raw["config"]["users"]
    assert all(c["namespace"] == "" for c in cps), (
        "Study expects the root graph only"
    )
    assert len({key(c) for c in cps}) == len(cps)
    assert len(
        {
            (b["thread_id"], b["namespace"], b["channel"], b["version"])
            for b in blobs
        }
    ) == len(blobs)
    assert len({(*key(w), w["task_id"], w["idx"]) for w in writes}) == len(
        writes
    )
    cp_by_key = {key(c): c for c in cps}
    put_by_key = {key(c): c for c in calls if c["method"] == "aput"}
    assert (
        len(put_by_key)
        == sum(c["method"] == "aput" for c in calls)
        == len(cps)
    )
    assert set(put_by_key) == set(cp_by_key)
    assert all(key(w) in cp_by_key for w in writes)
    assert all(
        c["parent_checkpoint_id"] is None
        or (c["thread_id"], c["namespace"], c["parent_checkpoint_id"])
        in cp_by_key
        for c in cps
    )
    for collection in (blobs, writes):
        assert all(
            v["bytes"] >= 0 and v["storage_bytes"] >= 0 for v in collection
        )
    assert all(
        c["checkpoint_json_bytes"] > 0 and c["metadata_json_bytes"] > 0
        for c in cps
    )
    assert all(c["end"] >= c["start"] for c in calls)

    # Each version is counted once, even when many checkpoints reference it.
    by_blob = {
        (b["thread_id"], b["namespace"], b["channel"], b["version"]): b
        for b in blobs
    }
    latest = {}
    first_reference = {}
    for cp in sorted(cps, key=key):
        latest[cp["thread_id"], cp["namespace"]] = cp
        for channel, version in cp["versions"].items():
            blob_key = (cp["thread_id"], cp["namespace"], channel, version)
            if blob_key in by_blob:
                first_reference.setdefault(blob_key, cp)
    assert set(first_reference) == set(by_blob), (
        "Unreferenced persisted blob requires investigation"
    )
    latest_keys = {
        (cp["thread_id"], cp["namespace"], channel, version)
        for cp in latest.values()
        for channel, version in cp["versions"].items()
        if (cp["thread_id"], cp["namespace"], channel, version) in by_blob
    }

    channels = defaultdict(
        lambda: dict(
            blob_versions=0,
            blob_bytes=0,
            write_rows=0,
            write_bytes=0,
            inline_occurrences=0,
            inline_json_value_bytes=0,
            latest_blob_bytes=0,
        )
    )
    phases = defaultdict(
        lambda: dict(
            checkpoints=0,
            checkpoint_json_bytes=0,
            metadata_json_bytes=0,
            first_referenced_blob_bytes=0,
            linked_write_bytes=0,
        )
    )
    for cp in cps:
        phase = cp["phase"] or "planning"
        phases[phase]["checkpoints"] += 1
        for name in ("checkpoint_json_bytes", "metadata_json_bytes"):
            phases[phase][name] += cp[name]
        for channel, size in cp["inline_bytes"].items():
            channels[channel]["inline_occurrences"] += 1
            channels[channel]["inline_json_value_bytes"] += size
    for blob_key, b in by_blob.items():
        v = channels[b["channel"]]
        v["blob_versions"] += 1
        v["blob_bytes"] += b["bytes"]
        phases[first_reference[blob_key]["phase"] or "planning"][
            "first_referenced_blob_bytes"
        ] += b["bytes"]
        if blob_key in latest_keys:
            v["latest_blob_bytes"] += b["bytes"]
    for w in writes:
        v = channels[w["channel"]]
        v["write_rows"] += 1
        v["write_bytes"] += w["bytes"]
        phases[cp_by_key[key(w)]["phase"] or "planning"][
            "linked_write_bytes"
        ] += w["bytes"]
    # Linked writes use their parent checkpoint phase, not the executing node's label.
    for v in channels.values():
        v["payload_bytes"] = (
            v["blob_bytes"] + v["write_bytes"] + v["inline_json_value_bytes"]
        )

    sizes = dict(
        checkpoint_json_bytes=sum(c["checkpoint_json_bytes"] for c in cps),
        metadata_json_bytes=sum(c["metadata_json_bytes"] for c in cps),
        blob_bytes=sum(b["bytes"] for b in blobs),
        write_bytes=sum(w["bytes"] for w in writes),
    )
    total = sum(sizes.values())
    assert (
        sum(p["first_referenced_blob_bytes"] for p in phases.values())
        == sizes["blob_bytes"]
    )
    assert (
        sum(p["linked_write_bytes"] for p in phases.values())
        == sizes["write_bytes"]
    )
    serialization = [s for c in calls for s in c["serialization"]]
    assert (
        sum(
            ch["bytes"]
            for s in serialization
            if s["method"] == "_dump_blobs"
            for ch in s["channels"]
        )
        == sizes["blob_bytes"]
    )
    # Intermediate special writes may be upserted: generated bytes >= unique stored bytes.
    dumped_writes = sum(
        ch["bytes"]
        for s in serialization
        if s["method"] == "_dump_writes"
        for ch in s["channels"]
    )
    assert dumped_writes >= sizes["write_bytes"]
    n = len(threads)
    timings = {}
    for method in ("aget_tuple", "aput", "aput_writes"):
        selected = [c for c in calls if c["method"] == method]
        ms = [(c["end"] - c["start"]) * 1000 for c in selected]
        timings[method] = dict(
            calls=len(ms),
            calls_per_user=len(ms) / n,
            total_ms=sum(ms),
            ms_per_user=sum(ms) / n,
            mean_ms=mean(ms),
            p95_ms=quantile(ms),
            max_ms=max(ms),
        )
    # Existing outer diagnostics include adapter construction in the new source.
    outer_write_ms = 0
    for method in ("aput", "aput_writes"):
        totals = [
            t["timings"].get("checkpoint." + method, {})
            for t in raw["server"]["traces"]
        ]
        assert (
            sum(t.get("count", 0) for t in totals) == timings[method]["calls"]
        )
        outer_write_ms += sum(t.get("total_ms", 0) for t in totals)
    write_call_ms = sum(
        timings[k]["total_ms"] for k in ("aput", "aput_writes")
    )
    slot_ms = flow["slot_work_seconds"] * 1000
    latest_blob_bytes = sum(by_blob[k]["bytes"] for k in latest_keys)
    latest_json_bytes = sum(
        c["checkpoint_json_bytes"] + c["metadata_json_bytes"]
        for c in latest.values()
    )
    lock_summary = None
    if raw["config"].get("checkpoint_lock_profile"):
        locks = [lock for call in calls for lock in call.get("locks", [])]
        assert len(locks) == len(calls), (
            "Every saver call must include its actual lock observation"
        )
        for call in calls:
            lock = call["locks"][0]
            assert 0 <= lock["wait_ms"] and 0 <= lock["hold_ms"]
            assert (
                abs(
                    lock["hold_ms"]
                    - (lock["released"] - lock["acquired"]) * 1000
                )
                < 1e-6
            )
            assert (
                lock["wait_ms"] + lock["hold_ms"]
                <= (call["end"] - call["start"]) * 1000 + 0.02
            )
        occupancy = sorted(
            [(lock["acquired"], 1) for lock in locks]
            + [(lock["released"], -1) for lock in locks]
        )
        active = peak = 0
        for _, change in occupancy:
            active += change
            peak = max(peak, active)
            assert 0 <= active <= 1, (
                "Official shared saver lock was not preserved"
            )
        assert active == 0
        lock_summary = dict(
            calls=len(locks),
            observed_peak_inside_lock=peak,
            wait_ms_per_user=sum(lock["wait_ms"] for lock in locks) / n,
            hold_ms_per_user=sum(lock["hold_ms"] for lock in locks) / n,
            wait_p95_ms=quantile([lock["wait_ms"] for lock in locks]),
            wait_max_ms=max(lock["wait_ms"] for lock in locks),
            fraction_of_saver_call_work=sum(lock["wait_ms"] for lock in locks)
            / sum((call["end"] - call["start"]) * 1000 for call in calls),
        )
    components = dict(
        Counter(
            {
                name: sum(c["component_json_bytes"].get(name, 0) for c in cps)
                for name in {
                    k for c in cps for k in c.get("component_json_bytes", {})
                }
            }
        )
    )
    return dict(
        flow=flow,
        threads=n,
        rows=dict(checkpoints=len(cps), blobs=len(blobs), writes=len(writes)),
        sizes=sizes,
        logical_payload_bytes=total,
        logical_payload_bytes_per_user=total / n,
        latest_state_payload_bytes=latest_blob_bytes + latest_json_bytes,
        historical_blob_bytes=sizes["blob_bytes"] - latest_blob_bytes,
        retained_writes_bytes=sizes["write_bytes"],
        checkpoints_per_user=len(cps) / n,
        timings=timings,
        saver_write_ms_per_user=write_call_ms / n,
        saver_write_outer_ms_per_user=outer_write_ms / n,
        saver_write_work_fraction=write_call_ms / slot_ms,
        serialization_ms_per_user=sum(s["ms"] for s in serialization) / n,
        generated_write_bytes=dumped_writes,
        channels=[
            dict(channel=k, **v)
            for k, v in sorted(
                channels.items(), key=lambda item: -item[1]["payload_bytes"]
            )
        ],
        phases=[dict(phase=k, **v) for k, v in phases.items()],
        per_thread=[
            dict(
                thread_id=t,
                checkpoints=sum(c["thread_id"] == t for c in cps),
                blob_bytes=sum(
                    b["bytes"] for b in blobs if b["thread_id"] == t
                ),
                write_bytes=sum(
                    w["bytes"] for w in writes if w["thread_id"] == t
                ),
                checkpoint_json_bytes=sum(
                    c["checkpoint_json_bytes"] + c["metadata_json_bytes"]
                    for c in cps
                    if c["thread_id"] == t
                ),
            )
            for t in sorted(threads)
        ],
        checkpoint_component_json_bytes=components,
        lock_summary=lock_summary,
        latest_linked_write_bytes=sum(
            w["bytes"]
            for w in writes
            if key(w) in {key(c) for c in latest.values()}
        ),
        relation_sizes_include_warmup=profile["relations"],
    )


if __name__ == "__main__":
    import argparse, json
    from pathlib import Path

    p = argparse.ArgumentParser()
    p.add_argument("captures", type=Path, nargs="+")
    p.add_argument("--output", type=Path, required=True)
    args = p.parse_args()
    rows = []
    for folder in args.captures:
        for path in sorted(folder.rglob("raw.json")):
            rows.append(
                dict(
                    capture=str(path),
                    **analyze_checkpoint(json.loads(path.read_text())),
                )
            )
    args.output.write_text(
        json.dumps(rows, ensure_ascii=False, indent=2) + "\n"
    )
    print(
        json.dumps(
            dict(
                validated_trials=len(rows),
                users=sum(r["threads"] for r in rows),
            )
        )
    )
