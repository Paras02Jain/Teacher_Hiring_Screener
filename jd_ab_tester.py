"""
jd_ab_tester.py — EduHire JD A/B Tester
==========================================
Scores the SAME uploaded resumes against TWO job descriptions in parallel
and produces a side-by-side comparison report.

Use cases
---------
  • Compare a broad JD vs a skills-focused JD for the same role.
  • Test whether rewording the JD after a bias audit changes who surfaces.
  • Decide between two role configurations (e.g. specialist vs generalist).

Returns a structured comparison dict consumed by app.py for rendering.

No extra dependencies beyond what backend.py already uses.
"""

from __future__ import annotations
import concurrent.futures
from typing import Any

from backend import (
    ResumeScreener,
    extract_text_from_file,
    extract_candidate_name,
    extract_skill_tags,
    extract_experience_years,
    detect_education_degree,
    detect_certification,
    detect_premier_institution,
    extract_jd_matched_keywords,
    extract_jd_missing_keywords,
    compute_similarity_score,
    compute_ats_score,
    get_last_groq_result,
)


# ══════════════════════════════════════════════════════════════════════════════
# INTERNAL: score a single candidate against one JD
# ══════════════════════════════════════════════════════════════════════════════

def _score_one(
    jd: str,
    candidate_text: str,
    candidate_name: str,
    groq_api_key: str = "",
    config: dict | None = None,
) -> dict:
    """
    Score a single candidate text against a JD and return a compact result dict.
    """
    config = config or {}
    min_exp       = config.get("min_experience_years", 1)
    min_score_thr = config.get("min_similarity_score", 0.20)
    req_degree    = config.get("require_teaching_degree", True)
    req_cert      = config.get("require_certification", False)

    tags       = extract_skill_tags(candidate_text)
    exp        = extract_experience_years(candidate_text)
    has_deg    = detect_education_degree(candidate_text)
    has_cert   = detect_certification(candidate_text)
    is_premier = detect_premier_institution(candidate_text)
    matched_kw = extract_jd_matched_keywords(jd, candidate_text)
    missing_kw = extract_jd_missing_keywords(jd, candidate_text)

    score, method = compute_similarity_score(
        jd, candidate_text,
        candidate_name=candidate_name,
        groq_api_key=groq_api_key,
    )
    groq_result = get_last_groq_result()

    fail_reasons = []
    if req_degree and not has_deg:
        fail_reasons.append("no teaching degree")
    if req_cert and not has_cert:
        fail_reasons.append("no certification")
    if exp < min_exp:
        fail_reasons.append(f"only {exp} yr(s) exp")
    if not fail_reasons and score < min_score_thr * 100:
        fail_reasons.append(f"score {score:.1f}% below threshold")

    is_filtered = bool(fail_reasons)

    ats = compute_ats_score(
        jd=jd, resume_text=candidate_text,
        overall_score=score, exp=exp,
        has_deg=has_deg, has_cert=has_cert,
        matched_kw=matched_kw, missing_kw=missing_kw,
        tags=tags,
    )

    return {
        "name":          candidate_name,
        "score":         score,
        "score_method":  method,
        "filtered":      is_filtered,
        "fail_reasons":  fail_reasons,
        "exp":           exp,
        "has_deg":       has_deg,
        "has_cert":      has_cert,
        "is_premier":    is_premier,
        "tags":          tags,
        "matched_kw":    matched_kw,
        "missing_kw":    missing_kw,
        "ats_breakdown": ats,
        "groq_strengths": groq_result.get("strengths", []) if groq_result else [],
        "groq_red_flags": groq_result.get("red_flags", [])  if groq_result else [],
        "groq_reasoning": groq_result.get("reasoning", "")  if groq_result else "",
    }


# ══════════════════════════════════════════════════════════════════════════════
# PUBLIC: run_ab_test()
# ══════════════════════════════════════════════════════════════════════════════

def run_ab_test(
    jd_a: str,
    jd_b: str,
    uploaded_files: list,          # Streamlit UploadedFile objects
    label_a: str = "JD Version A",
    label_b: str = "JD Version B",
    groq_api_key: str = "",
    config: dict | None = None,
) -> dict:
    """
    Score all uploaded resumes against BOTH JDs and return a comparison report.

    Parameters
    ----------
    jd_a, jd_b       : The two JD strings to test.
    uploaded_files    : List of Streamlit UploadedFile objects.
    label_a, label_b  : Human-readable names shown in the UI.
    groq_api_key      : Groq key for LLM scoring (optional, falls back to ST/TFIDF).
    config            : dict with screening thresholds (same as ResumeScreener.screen).

    Returns
    -------
    dict:
        "label_a"      : str
        "label_b"      : str
        "candidates"   : list of per-candidate dicts (see below)
        "summary"      : high-level comparison text
        "winner"       : "A" | "B" | "Tie"
        "insights"     : list of 3-5 bullet-point insight strings
    """
    config = config or {}

    # ── Extract all resume texts up-front (single read per file) ────────────
    candidate_data: list[dict] = []
    for uf in uploaded_files:
        from backend import extract_text_from_file as _etf
        text = _etf(uf)
        name = extract_candidate_name(text, uf.name)
        candidate_data.append({"name": name, "text": text})
        uf.seek(0)  # reset for any subsequent reads

    if not candidate_data:
        return {
            "label_a":    label_a,
            "label_b":    label_b,
            "candidates": [],
            "summary":    "No resumes uploaded.",
            "winner":     "Tie",
            "insights":   [],
        }

    # ── Score each candidate against both JDs ────────────────────────────────
    results_a: list[dict] = []
    results_b: list[dict] = []

    for cand in candidate_data:
        ra = _score_one(jd_a, cand["text"], cand["name"], groq_api_key, config)
        rb = _score_one(jd_b, cand["text"], cand["name"], groq_api_key, config)
        results_a.append(ra)
        results_b.append(rb)

    # ── Build per-candidate comparison rows ──────────────────────────────────
    candidates = []
    for i, cand in enumerate(candidate_data):
        ra, rb = results_a[i], results_b[i]
        delta  = rb["score"] - ra["score"]

        # Rank shift: who qualifies under each JD?
        qual_a = not ra["filtered"]
        qual_b = not rb["filtered"]

        if qual_a and not qual_b:
            status_change = "drops_out"     # passes A but not B
        elif qual_b and not qual_a:
            status_change = "surfaces"      # fails A but passes B
        elif qual_a and qual_b:
            status_change = "both_pass"
        else:
            status_change = "both_fail"

        candidates.append({
            "name":          cand["name"],
            "score_a":       ra["score"],
            "score_b":       rb["score"],
            "delta":         round(delta, 1),
            "filtered_a":    ra["filtered"],
            "filtered_b":    rb["filtered"],
            "status_change": status_change,
            "matched_a":     ra["matched_kw"],
            "matched_b":     rb["matched_kw"],
            "missing_a":     ra["missing_kw"],
            "missing_b":     rb["missing_kw"],
            "tags":          list(set(ra["tags"] + rb["tags"])),
            "is_premier":    ra["is_premier"],
            "exp":           ra["exp"],
            "has_deg":       ra["has_deg"],
            "ats_a":         ra["ats_breakdown"],
            "ats_b":         rb["ats_breakdown"],
            "reasoning_a":   ra["groq_reasoning"],
            "reasoning_b":   rb["groq_reasoning"],
        })

    # ── Summary statistics ────────────────────────────────────────────────────
    avg_a = sum(c["score_a"] for c in candidates) / len(candidates)
    avg_b = sum(c["score_b"] for c in candidates) / len(candidates)
    pass_a = sum(1 for c in candidates if not c["filtered_a"])
    pass_b = sum(1 for c in candidates if not c["filtered_b"])

    surfaces  = [c["name"] for c in candidates if c["status_change"] == "surfaces"]
    drops_out = [c["name"] for c in candidates if c["status_change"] == "drops_out"]

    if avg_b > avg_a + 2:
        winner = "B"
    elif avg_a > avg_b + 2:
        winner = "A"
    else:
        winner = "Tie"

    summary = (
        f"{label_a}: avg score {avg_a:.1f}%, {pass_a} qualified. "
        f"{label_b}: avg score {avg_b:.1f}%, {pass_b} qualified. "
    )
    if surfaces:
        summary += f"{label_b} surfaces new candidates: {', '.join(surfaces[:3])}. "
    if drops_out:
        summary += f"{label_b} filters out: {', '.join(drops_out[:3])}. "
    if winner != "Tie":
        summary += f"Overall winner: {label_a if winner == 'A' else label_b} (higher average match)."

    # ── Insights ──────────────────────────────────────────────────────────────
    insights = _build_insights(candidates, label_a, label_b, avg_a, avg_b, pass_a, pass_b)

    return {
        "label_a":    label_a,
        "label_b":    label_b,
        "candidates": candidates,
        "summary":    summary,
        "winner":     winner,
        "avg_a":      round(avg_a, 1),
        "avg_b":      round(avg_b, 1),
        "pass_a":     pass_a,
        "pass_b":     pass_b,
        "insights":   insights,
    }


def _build_insights(
    candidates: list[dict],
    label_a: str,
    label_b: str,
    avg_a: float,
    avg_b: float,
    pass_a: int,
    pass_b: int,
) -> list[str]:
    insights: list[str] = []
    n = len(candidates)

    # Score delta direction
    if abs(avg_b - avg_a) >= 5:
        better = label_b if avg_b > avg_a else label_a
        insights.append(
            f"{better} produces higher average match scores ({max(avg_a, avg_b):.1f}% vs "
            f"{min(avg_a, avg_b):.1f}%). Consider using it as your primary JD."
        )
    else:
        insights.append(
            f"Both JDs produce similar average scores ({avg_a:.1f}% vs {avg_b:.1f}%)."
            " Differences are likely in keyword specificity rather than overall fit."
        )

    # Status changes
    surfaces  = [c for c in candidates if c["status_change"] == "surfaces"]
    drops_out = [c for c in candidates if c["status_change"] == "drops_out"]

    if surfaces:
        names = ", ".join(c["name"] for c in surfaces[:3])
        insights.append(
            f"{label_b} surfaces {len(surfaces)} candidate(s) that {label_a} filtered out: {names}. "
            "Review whether these candidates meet the spirit of your requirements."
        )

    if drops_out:
        names = ", ".join(c["name"] for c in drops_out[:3])
        insights.append(
            f"{label_b} excludes {len(drops_out)} candidate(s) that {label_a} passed: {names}. "
            f"{label_b} may use stricter or more specific language."
        )

    # Premier institution shift
    premier_candidates = [c for c in candidates if c["is_premier"]]
    if premier_candidates:
        avg_delta_premier = sum(c["delta"] for c in premier_candidates) / len(premier_candidates)
        avg_delta_rest    = sum(c["delta"] for c in candidates if not c["is_premier"]) / max(1, n - len(premier_candidates))
        if abs(avg_delta_premier - avg_delta_rest) >= 5:
            direction = "more" if avg_delta_premier > avg_delta_rest else "less"
            insights.append(
                f"{label_b} favours premier-institution graduates {direction} than {label_a} "
                f"(avg delta: {avg_delta_premier:+.1f}pp for premier, {avg_delta_rest:+.1f}pp for others). "
                "Check for credential-signal language in the JDs."
            )

    # Big movers
    big_movers = sorted(candidates, key=lambda c: abs(c["delta"]), reverse=True)
    if big_movers and abs(big_movers[0]["delta"]) >= 10:
        c = big_movers[0]
        dir_str = f"↑ {c['delta']:+.1f}pp" if c["delta"] > 0 else f"↓ {c['delta']:+.1f}pp"
        insights.append(
            f"Largest score swing: {c['name']} moved {dir_str} from {label_a} to {label_b}. "
            "Review the keyword differences driving this shift."
        )

    return insights[:5]
