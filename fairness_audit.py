"""
fairness_audit.py — EduHire Fairness & Equity Audit Module
============================================================
Analyses the RANKED OUTPUT of the screening pipeline for demographic patterns
across two key dimensions:
  1. Institution Tier  (Premier vs Non-Premier)
  2. Degree Type       (B.Ed / M.Ed / B.El.Ed / PGCE / PhD etc.)

Returns structured audit reports used by app.py for the MBA-grade DEI section.

No external API required — all analysis is statistical + keyword-based.
"""

from __future__ import annotations
import re
from collections import defaultdict, Counter
from typing import Any

# ── Degree bucket classifier ──────────────────────────────────────────────────
_DEGREE_BUCKETS: list[tuple[str, list[str]]] = [
    ("PhD / M.Phil",  ["phd", "ph.d", "m.phil", "doctor of philosophy"]),
    ("M.Ed",          ["m.ed", "med", "master of education", "master of teaching", "m.teach"]),
    ("B.Ed",          ["b.ed", "bed", "bachelor of education", "bachelor of teaching", "b.teach"]),
    ("B.El.Ed",       ["b.el.ed", "beled", "bachelor of elementary education", "d.el.ed", "d el ed"]),
    ("PGCE",          ["pgce", "post graduate certificate in education", "post-graduate certificate"]),
    ("B.Sc / B.A",    ["b.sc", "b.a.", "b.a ", "bachelor of science", "bachelor of arts"]),
    ("Other / Unclear", []),   # catch-all
]

def _classify_degree(text: str) -> str:
    t = text.lower()
    for label, keywords in _DEGREE_BUCKETS:
        if keywords and any(kw in t for kw in keywords):
            return label
    return "Other / Unclear"


# ── Pass / Fail distribution helpers ─────────────────────────────────────────

def _pass_rate(group: list[dict]) -> float:
    """Fraction of qualified (non-filtered) candidates in a group."""
    if not group:
        return 0.0
    passed = sum(1 for r in group if not r.get("filtered", False))
    return round(passed / len(group) * 100, 1)

def _avg_score(group: list[dict]) -> float:
    if not group:
        return 0.0
    return round(sum(r.get("score", 0) for r in group) / len(group), 1)

def _avg_rank(group: list[dict]) -> float | None:
    """Average rank among QUALIFIED candidates only; None if none qualified."""
    ranked = [r["rank"] for r in group if not r.get("filtered") and r.get("rank")]
    return round(sum(ranked) / len(ranked), 1) if ranked else None


# ── Statistical disparity flag ────────────────────────────────────────────────
_DISPARITY_THRESHOLD = 20.0   # percentage-point gap that triggers a flag

def _flag_disparity(val_a: float, val_b: float, metric: str) -> dict | None:
    diff = abs(val_a - val_b)
    if diff >= _DISPARITY_THRESHOLD:
        higher = "Premier" if val_a >= val_b else "Non-Premier"
        return {
            "metric":    metric,
            "gap":       round(diff, 1),
            "direction": higher,
            "severity":  "High" if diff >= 35 else "Medium",
        }
    return None


# ══════════════════════════════════════════════════════════════════════════════
# PUBLIC API
# ══════════════════════════════════════════════════════════════════════════════

def run_fairness_audit(results: list[dict]) -> dict:
    """
    Core audit entry-point.

    Parameters
    ----------
    results : list[dict]
        The full output list from ResumeScreener.screen() — includes both
        qualified and filtered candidates.

    Returns
    -------
    dict with keys:
        "institution_tier"  : analysis across Premier vs Non-Premier
        "degree_type"       : analysis across degree bucket groups
        "disparities"       : list of flagged statistical disparities
        "summary"           : plain-English one-paragraph verdict
        "recommendations"   : list of 2-4 actionable recommendations
        "total_candidates"  : int
        "total_qualified"   : int
    """
    if not results:
        return _empty_audit()

    # ── Enrich each result with derived fields ────────────────────────────────
    enriched: list[dict] = []
    for r in results:
        deg_bucket = _classify_degree(r.get("text", ""))
        enriched.append({**r, "_degree_bucket": deg_bucket})

    total   = len(enriched)
    total_q = sum(1 for r in enriched if not r.get("filtered"))

    # ── 1. Institution Tier Analysis ──────────────────────────────────────────
    premier     = [r for r in enriched if r.get("is_premier")]
    non_premier = [r for r in enriched if not r.get("is_premier")]

    institution_tier = {
        "Premier": {
            "count":      len(premier),
            "pass_rate":  _pass_rate(premier),
            "avg_score":  _avg_score(premier),
            "avg_rank":   _avg_rank(premier),
        },
        "Non-Premier": {
            "count":      len(non_premier),
            "pass_rate":  _pass_rate(non_premier),
            "avg_score":  _avg_score(non_premier),
            "avg_rank":   _avg_rank(non_premier),
        },
    }

    # ── 2. Degree Type Analysis ───────────────────────────────────────────────
    bucket_groups: dict[str, list[dict]] = defaultdict(list)
    for r in enriched:
        bucket_groups[r["_degree_bucket"]].append(r)

    degree_type = {}
    for bucket, group in sorted(bucket_groups.items(), key=lambda x: -len(x[1])):
        degree_type[bucket] = {
            "count":     len(group),
            "pass_rate": _pass_rate(group),
            "avg_score": _avg_score(group),
            "avg_rank":  _avg_rank(group),
        }

    # ── 3. Disparity Flags ────────────────────────────────────────────────────
    disparities: list[dict] = []

    # Institution tier disparities
    pr_pass  = institution_tier["Premier"]["pass_rate"]
    npr_pass = institution_tier["Non-Premier"]["pass_rate"]
    pr_score  = institution_tier["Premier"]["avg_score"]
    npr_score = institution_tier["Non-Premier"]["avg_score"]

    d1 = _flag_disparity(pr_pass, npr_pass, "Pass Rate (Institution Tier)")
    d2 = _flag_disparity(pr_score, npr_score, "Avg Match Score (Institution Tier)")
    if d1: disparities.append(d1)
    if d2: disparities.append(d2)

    # Degree-type disparities: compare highest vs lowest pass-rate buckets
    bucket_pass_rates = {k: v["pass_rate"] for k, v in degree_type.items() if v["count"] >= 2}
    if len(bucket_pass_rates) >= 2:
        top_bucket   = max(bucket_pass_rates, key=bucket_pass_rates.get)
        bot_bucket   = min(bucket_pass_rates, key=bucket_pass_rates.get)
        deg_gap      = bucket_pass_rates[top_bucket] - bucket_pass_rates[bot_bucket]
        if deg_gap >= _DISPARITY_THRESHOLD:
            disparities.append({
                "metric":    f"Pass Rate gap: {top_bucket} vs {bot_bucket} (Degree Type)",
                "gap":       round(deg_gap, 1),
                "direction": top_bucket,
                "severity":  "High" if deg_gap >= 35 else "Medium",
            })

    # ── 4. Plain-English Summary ──────────────────────────────────────────────
    summary = _build_summary(institution_tier, degree_type, disparities, total, total_q)

    # ── 5. Recommendations ───────────────────────────────────────────────────
    recommendations = _build_recommendations(disparities, institution_tier, degree_type)

    return {
        "institution_tier":  institution_tier,
        "degree_type":       degree_type,
        "disparities":       disparities,
        "summary":           summary,
        "recommendations":   recommendations,
        "total_candidates":  total,
        "total_qualified":   total_q,
    }


def _build_summary(
    institution_tier: dict,
    degree_type: dict,
    disparities: list[dict],
    total: int,
    total_q: int,
) -> str:
    pr   = institution_tier["Premier"]
    npr  = institution_tier["Non-Premier"]

    lines = [
        f"Screened {total} candidates — {total_q} qualified ({round(total_q/total*100) if total else 0}% pass rate)."
    ]

    if pr["count"] > 0:
        lines.append(
            f"Premier-institution graduates make up {pr['count']} of {total} applicants "
            f"with a {pr['pass_rate']}% pass rate (avg score {pr['avg_score']}%). "
            f"Non-premier graduates: {npr['count']} applicants, {npr['pass_rate']}% pass rate "
            f"(avg score {npr['avg_score']}%)."
        )
    else:
        lines.append("No premier-institution graduates detected in this batch.")

    if disparities:
        flags = "; ".join(f"{d['metric']} gap of {d['gap']}pp" for d in disparities)
        lines.append(
            f"⚠️ Statistical disparities detected: {flags}. "
            "Review filter thresholds to ensure they are skill-based rather than credential-based."
        )
    else:
        lines.append("✅ No statistically significant demographic disparities detected in this batch.")

    return " ".join(lines)


def _build_recommendations(
    disparities: list[dict],
    institution_tier: dict,
    degree_type: dict,
) -> list[str]:
    recs: list[str] = []

    high_severity = [d for d in disparities if d["severity"] == "High"]
    if high_severity:
        recs.append(
            "High disparity detected — audit the minimum match-score and experience filters "
            "to confirm they reflect genuine job requirements rather than proxies for institutional prestige."
        )

    pr_pass  = institution_tier["Premier"]["pass_rate"]
    npr_pass = institution_tier["Non-Premier"]["pass_rate"]
    if pr_pass > npr_pass + 15:
        recs.append(
            "Premier-institution candidates are passing at a higher rate. Consider whether the JD language "
            "inadvertently favours credential signals (e.g., 'top-tier degree') over demonstrated teaching skills."
        )
    elif npr_pass > pr_pass + 15:
        recs.append(
            "Non-premier graduates are advancing at a higher rate than premier-institution candidates — "
            "verify the filtering logic handles B.Ed / pedagogy requirements consistently across both groups."
        )

    # Check if any degree bucket has 0% pass rate with ≥2 candidates
    zero_buckets = [k for k, v in degree_type.items() if v["count"] >= 2 and v["pass_rate"] == 0.0]
    if zero_buckets:
        recs.append(
            f"Candidates with {', '.join(zero_buckets)} degrees have a 0% pass rate. "
            "Confirm the degree-detection logic correctly identifies these qualifications."
        )

    recs.append(
        "Re-run this audit after each hiring cycle and track disparity trends over time to "
        "build an evidence base for equitable hiring policy."
    )

    return recs[:4]


def _empty_audit() -> dict:
    return {
        "institution_tier":  {},
        "degree_type":       {},
        "disparities":       [],
        "summary":           "No candidates available to audit.",
        "recommendations":   [],
        "total_candidates":  0,
        "total_qualified":   0,
    }
