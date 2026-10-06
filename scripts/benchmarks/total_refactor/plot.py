"""Standalone, exportable figures; no browser rendering or external assets."""

import argparse, json
from pathlib import Path
import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt

p = argparse.ArgumentParser()
p.add_argument("--report", type=Path, required=True)
a = p.parse_args()
rows = json.loads((a.report / "data/summary.json").read_text())
out = a.report / "figures"
out.mkdir(exist_ok=True)
plt.rcParams.update(
    {
        "font.size": 11,
        "axes.spines.top": False,
        "axes.spines.right": False,
        "figure.facecolor": "white",
        "axes.facecolor": "white",
        "savefig.facecolor": "white",
    }
)
fig, ax = plt.subplots(figsize=(10, 5.3))
for label, slots, title, color, marker in [
    ("before", 1, "Initial code · 1 slot", "#334155", "o"),
    ("after", 4, "Current code · 4 slots", "#0f766e", "s"),
    ("after", 1, "Current code · 1 slot (control)", "#b45309", "D"),
]:
    rs = sorted(
        [
            r
            for r in rows
            if r["config"]["scenario"] == "flow"
            and r["config"]["label"] == label
            and r["config"]["slots"] == slots
            and r["completed"] == r["config"]["users"]
        ],
        key=lambda r: r["config"]["users"],
    )
    if not rs:
        continue
    ax.plot(
        [r["config"]["users"] for r in rs],
        [r["completion_seconds"]["mean"] for r in rs],
        color=color,
        marker=marker,
        label=title,
        lw=2,
    )
    for r in rs:
        if r["config"]["users"] == 50:
            ax.annotate(
                f"{r['completion_seconds']['mean']:,.1f}s",
                (50, r["completion_seconds"]["mean"]),
                xytext=(-8, 10),
                textcoords="offset points",
                ha="right",
                color=color,
            )
ax.set(
    xlabel="Concurrent users in one batch",
    ylabel="Mean complete flow latency (seconds)",
    title="Initial → current: 5 seconds per LLM call",
    xticks=[1, 10, 30, 50],
    ylim=(0, None),
)
ax.grid(axis="y", alpha=0.18)
ax.legend(frameon=False, loc="upper left")
fig.text(
    0.12,
    0.015,
    (
        "One process; 4 calls per user; approval-wait endpoint; "
        "completed batches "
        "only."
    ),
    fontsize=9,
    color="#475569",
)
fig.tight_layout(rect=(0, 0.04, 1, 1))
fig.savefig(out / "flow-latency.png", dpi=180)
fig.savefig(out / "flow-latency.svg")
plt.close(fig)
fig, ax = plt.subplots(figsize=(10, 5.4))
selected = []
for label, slots, title in [
    ("before", 1, "Initial · 1 slot"),
    ("after", 4, "Current · 4 slots"),
]:
    r = next(
        (
            r
            for r in rows
            if r["config"]["scenario"] == "flow"
            and r["config"]["users"] == 50
            and r["config"]["label"] == label
            and r["config"]["slots"] == slots
            and r["completed"] == 50
        ),
        None,
    )
    if r:
        selected.append((title, r))
keys = [
    ("Queue wait", "#64748b", "completed_user_queue_seconds"),
    ("Model calls", "#0f766e", "completed_user_model_seconds"),
    ("Other Run work", "#d97706", None),
    ("HTTP / polling / think", "#a5b4fc", "completed_user_other_seconds"),
]
bottom = [0.0] * len(selected)
for title, color, key in keys:
    vals = [
        r[key]["mean"]
        if key
        else r["completed_user_execution_seconds"]["mean"]
        - r["completed_user_model_seconds"]["mean"]
        for _, r in selected
    ]
    ax.barh(
        [x[0] for x in selected],
        vals,
        left=bottom,
        label=title,
        color=color,
        height=0.45,
    )
    bottom = [x + y for x, y in zip(bottom, vals)]
ax.set(
    xlabel="Mean seconds per completed user",
    title="50 users: most of the difference is queue time",
)
ax.invert_yaxis()
ax.grid(axis="x", alpha=0.15)
ax.legend(
    loc="upper center", bbox_to_anchor=(0.5, -0.12), ncol=2, frameon=False
)
fig.tight_layout()
fig.savefig(out / "flow-breakdown-50.png", dpi=180)
fig.savefig(out / "flow-breakdown-50.svg")
plt.close(fig)
print(out)
