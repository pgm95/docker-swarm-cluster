#!/usr/bin/env python3
"""Post-action borgmatic hook. Writes Prometheus exposition text to /shared/metrics.

Per-target invocation: rewrites only the current target's metric lines, preserves
metrics from other targets that were last written by their own invocations.
"""
import argparse
import json
import os
import re
import subprocess
import sys
import time

OUT_PATH = "/shared/metrics"
TMP_PATH = "/shared/metrics.tmp"

METRIC_LINE = re.compile(r"(\w+)\{([^}]*)\}\s+(\S+)")
LABEL_PAIR = re.compile(r'(\w+)="([^"]*)"')


def run_borgmatic(extra_args):
    try:
        result = subprocess.run(
            ["borgmatic", *extra_args],
            capture_output=True,
            text=True,
            timeout=120,
            check=True,
        )
        return json.loads(result.stdout)
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, json.JSONDecodeError) as e:
        print(f"exporter: borgmatic {extra_args} failed: {e}", file=sys.stderr)
        return None


def read_prior(target):
    """Returns (preserved_lines, prior_helps, prior_types, current_target_priors).

    preserved_lines: metric lines for OTHER targets, kept verbatim
    prior_helps/types: dicts of {metric_name: docstring} from prior file
    current_target_priors: {metric_name: value_str} for current target's counters/timestamps
    """
    preserved = []
    helps = {}
    types = {}
    priors = {}
    if not os.path.exists(OUT_PATH):
        return preserved, helps, types, priors

    with open(OUT_PATH) as f:
        for raw in f:
            line = raw.rstrip("\n")
            if line.startswith("# HELP "):
                rest = line[7:].split(" ", 1)
                if len(rest) == 2:
                    helps[rest[0]] = rest[1]
                continue
            if line.startswith("# TYPE "):
                rest = line[7:].split(" ", 1)
                if len(rest) == 2:
                    types[rest[0]] = rest[1]
                continue
            if not line or line.startswith("#"):
                continue
            m = METRIC_LINE.match(line)
            if not m:
                continue
            name, labels_str, value = m.groups()
            labels = dict(LABEL_PAIR.findall(labels_str))
            line_target = labels.get("target")
            if line_target is None:
                # Repo-wide metric; will be re-emitted with fresh data
                continue
            if line_target == target:
                # Current target's prior — overwrite, but capture for counter increment
                priors[name] = value
            else:
                preserved.append(
                    {"name": name, "labels": labels, "value": value}
                )
    return preserved, helps, types, priors


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--status", choices=["finish", "fail"], required=True)
    parser.add_argument("--target", required=True)
    parser.add_argument("--config", required=True,
                        help="Path to firing config file. Scopes borgmatic info/list to a single config so multi-config setups don't return ambiguous results.")
    parser.add_argument("repository_label")
    parser.add_argument("repository")
    args = parser.parse_args()

    now = int(time.time())
    success = 1 if args.status == "finish" else 0
    target_labels = {
        "repository_label": args.repository_label or "unknown",
        "target": args.target,
    }
    repo_labels = {"repository_label": args.repository_label or "unknown"}

    preserved, prior_helps, prior_types, priors = read_prior(args.target)

    # Build current target's metrics. Each entry is (name, type, help, value, labels).
    current = []

    def add(name, type_, help_, value, labels):
        current.append({"name": name, "type": type_, "help": help_, "value": value, "labels": labels})

    add("borgmatic_backup_last_run_timestamp_seconds", "gauge",
        "Unix timestamp of the last hook fire.", now, target_labels)
    add("borgmatic_backup_success", "gauge",
        "1 if the last backup run finished, 0 if it failed.", success, target_labels)

    success_ts = now if success else priors.get("borgmatic_backup_last_success_timestamp_seconds")
    if success_ts is not None:
        add("borgmatic_backup_last_success_timestamp_seconds", "gauge",
            "Unix timestamp of the last successful backup.", success_ts, target_labels)

    runs = int(float(priors.get("borgmatic_backup_runs_total", "0"))) + 1
    successes = int(float(priors.get("borgmatic_backup_successes_total", "0"))) + (1 if success else 0)
    failures = int(float(priors.get("borgmatic_backup_failures_total", "0"))) + (0 if success else 1)
    add("borgmatic_backup_runs_total", "counter",
        "Total backup runs (success or failure) since the file was last reset.", runs, target_labels)
    add("borgmatic_backup_successes_total", "counter",
        "Total successful backup runs since the file was last reset.", successes, target_labels)
    add("borgmatic_backup_failures_total", "counter",
        "Total failed backup runs since the file was last reset.", failures, target_labels)

    info = run_borgmatic(["--config", args.config, "info", "--json", "--archive", "latest", "--repository", args.repository])
    if info:
        repo = info[0]
        archives = repo.get("archives") or []
        if archives:
            stats = archives[0].get("stats", {})
            duration = archives[0].get("duration", 0)
            add("borgmatic_last_archive_original_bytes", "gauge",
                "Latest archive original (logical) size in bytes.",
                stats.get("original_size", 0), target_labels)
            add("borgmatic_last_archive_compressed_bytes", "gauge",
                "Latest archive compressed size in bytes.",
                stats.get("compressed_size", 0), target_labels)
            add("borgmatic_last_archive_deduplicated_bytes", "gauge",
                "Latest archive deduplicated size (chunks unique to this archive) in bytes.",
                stats.get("deduplicated_size", 0), target_labels)
            add("borgmatic_last_archive_files", "gauge",
                "Number of files in the latest archive.",
                stats.get("nfiles", 0), target_labels)
            add("borgmatic_last_archive_duration_seconds", "gauge",
                "Duration of the latest archive create run in seconds.",
                f"{duration:.3f}", target_labels)
        cache = (repo.get("cache") or {}).get("stats") or {}
        # Repo-wide metrics: no `target` label, freshly written by every invocation
        add("borgmatic_repository_original_bytes", "gauge",
            "Total logical size across all archives in bytes.",
            cache.get("total_size", 0), repo_labels)
        add("borgmatic_repository_compressed_bytes", "gauge",
            "Total compressed size across all archives in bytes.",
            cache.get("total_csize", 0), repo_labels)
        add("borgmatic_repository_deduplicated_bytes", "gauge",
            "Repository on-disk size in bytes.",
            cache.get("unique_csize", 0), repo_labels)
        add("borgmatic_repository_total_chunks", "gauge",
            "Total chunk references across all archives.",
            cache.get("total_chunks", 0), repo_labels)
        add("borgmatic_repository_unique_chunks", "gauge",
            "Total unique chunks in the repository.",
            cache.get("total_unique_chunks", 0), repo_labels)

    listing = run_borgmatic(["--config", args.config, "list", "--json", "--repository", args.repository])
    if listing:
        add("borgmatic_archives_count", "gauge",
            "Number of archives for this target in the repository.",
            len(listing[0].get("archives") or []), target_labels)

    # Index docstrings (current invocation wins; preserved lines fall back to prior file's HELP/TYPE)
    helps = dict(prior_helps)
    types = dict(prior_types)
    for m in current:
        helps[m["name"]] = m["help"]
        types[m["name"]] = m["type"]

    # Group all output lines by metric name
    by_name = {}
    for m in current:
        by_name.setdefault(m["name"], []).append((m["labels"], m["value"]))
    for p in preserved:
        by_name.setdefault(p["name"], []).append((p["labels"], p["value"]))

    out = []
    for name in by_name:
        out.append(f"# HELP {name} {helps.get(name, '')}")
        out.append(f"# TYPE {name} {types.get(name, 'gauge')}")
        for labels, value in by_name[name]:
            label_str = ",".join(f'{k}="{v}"' for k, v in labels.items())
            out.append(f"{name}{{{label_str}}} {value}")

    content = "\n".join(out) + "\n"
    with open(TMP_PATH, "w") as f:
        f.write(content)
    os.rename(TMP_PATH, OUT_PATH)


if __name__ == "__main__":
    main()
