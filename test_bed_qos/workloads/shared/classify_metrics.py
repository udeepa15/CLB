#!/usr/bin/env python3
"""classify_metrics.py - score automatic attacker identification from a controller JSONL log.

truth = {"attacker_labels": [...], "start_epoch": float}
Labels must use the controller's key space (port names such as 'veth-att-br', or dotted IPs).
Returns precision/recall over the whole run, time-to-classify (s after the attack started until
the first true attacker is flagged), and the number of ticks any non-attacker was flagged.
"""
import json
import math


def classification_metrics(log_path, truth_labels, start_epoch):
    truth = set(truth_labels)
    flagged_ever, fp_ticks, ticks, first_tp, max_sources = set(), 0, 0, None, 0
    try:
        with open(log_path) as f:
            for line in f:
                rec = json.loads(line)
                ticks += 1
                heavy = set(rec.get("heavy", []))
                flagged_ever |= heavy
                max_sources = max(max_sources, int(rec.get("n_sources", 0)))
                if heavy - truth:
                    fp_ticks += 1
                if first_tp is None and heavy & truth:
                    first_tp = rec["timestamp"] - start_epoch
    except FileNotFoundError:
        return {k: math.nan for k in ("precision", "recall", "time_to_classify_s", "fp_ticks", "fp_tenants", "ticks",
                                      "max_sources")}
    tp = len(flagged_ever & truth)
    fp = len(flagged_ever - truth)
    return {
        "precision": tp / (tp + fp) if (tp + fp) else math.nan,   # nan: nothing was ever flagged
        "recall": tp / len(truth) if truth else math.nan,         # nan: no attacker exists
        "time_to_classify_s": first_tp if first_tp is not None else math.nan,
        "fp_ticks": fp_ticks, "fp_tenants": fp, "ticks": ticks, "max_sources": max_sources,
    }
