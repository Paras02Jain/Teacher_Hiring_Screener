"""
interview_brief.py — EduHire Pre-Interview Brief Generator
===========================================================
Feature: Medium Impact #6
Auto-compiles a pre-interview brief before each scheduled interview:
  - Candidate background (resume summary, score, education, experience, skills)
  - Last call / meeting notes from pipeline tracker
  - Hiring manager's open questions (auto-suggested + manually added)

All data is drawn from session_state — no external API needed for the
base brief.  An optional AI-enriched version uses Groq (same key as
the rest of the app) to write a polished narrative summary.

Usage (from app.py):
    from interview_brief import generate_brief, render_brief_ui
    render_brief_ui(candidate, pipeline_data, groq_key, school_name)
"""

from __future__ import annotations

import datetime
import io
import re
from typing import Optional

# ── Optional Groq (AI enrichment) ────────────────────────────────────────────
try:
    from groq import Groq as _Groq
    _GROQ_AVAILABLE = True
except ImportError:
    _GROQ_AVAILABLE = False


# =============================================================================
# DATA ASSEMBLY
# =============================================================================

def _safe(val, fallback="—"):
    if val is None or (isinstance(val, str) and not val.strip()):
        return fallback
    return val


def _fmt_ts(iso_ts: str) -> str:
    """Format ISO timestamp to readable string."""
    try:
        dt = datetime.datetime.fromisoformat(iso_ts)
        return dt.strftime("%d %b %Y, %I:%M %p")
    except Exception:
        return iso_ts


def _last_n_notes(notes: list[dict], n: int = 5) -> list[dict]:
    """Return the most recent n notes, newest-first."""
    return sorted(notes, key=lambda x: x.get("ts", ""), reverse=True)[:n]


def _stage_history(history: list[dict]) -> list[dict]:
    """Return stage-change history, newest-first."""
    return sorted(history, key=lambda x: x.get("ts", ""), reverse=True)


def generate_brief(
    candidate: dict,
    pipeline_data: Optional[dict],
    interview_dt: Optional[datetime.datetime] = None,
    school_name: str = "Our School",
    interviewer_name: str = "Hiring Manager",
    job_title: str = "Teacher Position",
    extra_questions: Optional[list[str]] = None,
) -> dict:
    """
    Assemble a structured pre-interview brief dict.

    Parameters
    ----------
    candidate       : screening result dict (from backend.py)
    pipeline_data   : pipeline entry for this candidate (from session_state)
    interview_dt    : scheduled datetime (from calendar_scheduler results)
    school_name     : school name for header
    interviewer_name: hiring manager name
    job_title       : position being filled
    extra_questions : additional questions from the HM

    Returns a dict with keys:
        header, background, scores, skills, gaps, last_notes,
        stage_history, open_questions, generated_at
    """
    pd = pipeline_data or {}
    notes = pd.get("notes", [])
    history = pd.get("history", [])

    # ── Background ──────────────────────────────────────────────────────────
    background = {
        "name":        _safe(candidate.get("name")),
        "email":       _safe(candidate.get("email")),
        "job_title":   job_title,
        "school":      school_name,
        "interviewer": interviewer_name,
        "interview_dt": interview_dt,
        "current_stage": _safe(pd.get("stage"), "Unknown"),
    }

    # ── Scores & ATS ────────────────────────────────────────────────────────
    # Each ats_breakdown value is {"score": float, "weight": float, "detail": str}
    ats = candidate.get("ats_breakdown", {})

    def _ats_score(key: str) -> float:
        val = ats.get(key, 0)
        if isinstance(val, dict):
            return float(val.get("score", 0))
        try:
            return float(val)
        except (TypeError, ValueError):
            return 0.0

    scores = {
        "overall":        round(float(candidate.get("score", 0)), 1),
        "keyword_match":  round(_ats_score("keywords"),  1),   # backend key is "keywords"
        "experience":     round(_ats_score("experience"),     1),
        "education":      round(_ats_score("education"),      1),
        "certifications": round(_ats_score("certifications"), 1),
        "formatting":     round(_ats_score("formatting"),     1),
        "rank":           candidate.get("rank"),
    }

    # ── Skills ──────────────────────────────────────────────────────────────
    skills = {
        # Backend stores keywords as matched_kw / missing_kw on the result dict.
        # Fallback: pull from ats_breakdown["keywords"]["detail"] if present.
        "matched":   (candidate.get("matched_kw") or
                      (ats.get("keywords", {}).get("detail") or {}).get("matched", [])),
        "missing":   (candidate.get("missing_kw") or
                      (ats.get("keywords", {}).get("detail") or {}).get("missing", [])),
        "all_found": candidate.get("matched_kw", []),
    }

    # ── Last call notes ──────────────────────────────────────────────────────
    last_notes = _last_n_notes(notes, n=5)

    # ── Stage history ────────────────────────────────────────────────────────
    stage_hist = _stage_history(history)[:6]

    # ── Auto-suggested open questions ────────────────────────────────────────
    suggested_questions = _auto_questions(candidate, scores, skills)
    if extra_questions:
        suggested_questions = list(extra_questions) + [
            q for q in suggested_questions if q not in extra_questions
        ]

    return {
        "header": {
            "school":      school_name,
            "job_title":   job_title,
            "interviewer": interviewer_name,
        },
        "background":      background,
        "scores":          scores,
        "skills":          skills,
        "last_notes":      last_notes,
        "stage_history":   stage_hist,
        "open_questions":  suggested_questions,
        "generated_at":    datetime.datetime.now().isoformat(),
    }


def _auto_questions(candidate: dict, scores: dict, skills: dict) -> list[str]:
    """Generate smart interview questions based on candidate profile."""
    questions = []

    # Gap-based questions
    missing = skills.get("missing", [])
    if missing:
        top_missing = ", ".join(missing[:3])
        questions.append(
            f"You appear to have limited experience with {top_missing}. "
            "Can you walk us through how you've handled similar topics in the past?"
        )

    # Experience questions
    if scores.get("experience", 0) < 60:
        questions.append(
            "Your application shows limited years of teaching experience. "
            "What hands-on classroom work have you done outside of formal employment?"
        )

    # Education / certification questions
    if scores.get("certifications", 0) < 50:
        questions.append(
            "Are you currently pursuing or planning to obtain any teaching certifications "
            "or professional development credentials?"
        )

    # Education score
    if scores.get("education", 0) < 60:
        questions.append(
            "Can you tell us more about your academic background and how it aligns "
            "with the curriculum demands of this role?"
        )

    # Strong candidate — deeper questions
    if scores.get("overall", 0) >= 80:
        questions.append(
            "Your profile looks strong overall. What specifically draws you to this "
            "school/position over other opportunities you may be considering?"
        )
        questions.append(
            "How do you measure the effectiveness of your teaching methods "
            "and iterate on them?"
        )

    # Moderate candidate
    elif scores.get("overall", 0) >= 60:
        questions.append(
            "Your background shows potential. What has been your greatest challenge "
            "as a teacher and how did you overcome it?"
        )

    # Always include a motivation question
    questions.append(
        "What motivates you most about working in education, and what impact do "
        "you hope to create in this role?"
    )

    return questions[:7]  # cap at 7 questions


# =============================================================================
# AI ENRICHMENT (optional, Groq)
# =============================================================================

def enrich_brief_with_ai(
    brief: dict,
    groq_key: str,
    groq_model: str = "llama3-8b-8192",
) -> str:
    """
    Use Groq to write a polished 3-paragraph narrative summary of the brief.
    Returns a markdown string or an error message.
    """
    if not _GROQ_AVAILABLE or not groq_key:
        return ""

    bg      = brief["background"]
    scores  = brief["scores"]
    skills  = brief["skills"]
    notes   = brief["last_notes"]
    q_list  = brief["open_questions"]

    note_text = ""
    if notes:
        note_text = "Recent call notes:\n" + "\n".join(
            f"- [{_fmt_ts(n.get('ts',''))}] {n.get('author','?')}: {n.get('text','')}"
            for n in notes[:3]
        )

    prompt = f"""You are an expert HR assistant. Write a concise, professional pre-interview brief (3 short paragraphs, no headers) for the following candidate. 
Be factual and neutral. Focus on: (1) candidate overview & strengths, (2) areas of concern or gaps, (3) key talking points and tone for the interview.

Candidate: {bg['name']}
Role: {bg['job_title']} at {bg['school']}
Interviewer: {bg['interviewer']}
Overall Score: {scores['overall']}/100 (Rank #{scores.get('rank','N/A')})
Keyword Match: {scores['keyword_match']}%, Experience: {scores['experience']}%, Education: {scores['education']}%
Matched Skills: {', '.join(skills['matched'][:8]) or 'None'}
Missing Skills: {', '.join(skills['missing'][:5]) or 'None'}
{note_text}
Suggested questions: {'; '.join(q_list[:3])}

Write the 3-paragraph brief now. Be concise (under 200 words total)."""

    try:
        client = _Groq(api_key=groq_key)
        resp = client.chat.completions.create(
            model=groq_model,
            messages=[{"role": "user", "content": prompt}],
            max_tokens=350,
            temperature=0.5,
        )
        return resp.choices[0].message.content.strip()
    except Exception as e:
        return f"_AI summary unavailable: {e}_"


# =============================================================================
# PLAIN-TEXT EXPORT
# =============================================================================

def brief_to_text(brief: dict) -> str:
    """Convert a brief dict to a clean plain-text string for download."""
    bg    = brief["background"]
    sc    = brief["scores"]
    sk    = brief["skills"]
    notes = brief["last_notes"]
    hist  = brief["stage_history"]
    qs    = brief["open_questions"]
    gen   = _fmt_ts(brief.get("generated_at", ""))

    lines = [
        "=" * 66,
        f"  PRE-INTERVIEW BRIEF — {bg['name'].upper()}",
        f"  {bg['job_title']}  |  {bg['school']}",
        "=" * 66,
        "",
        f"Interviewer   : {bg['interviewer']}",
        f"Interview Time: {bg['interview_dt'].strftime('%d %b %Y, %I:%M %p') if isinstance(bg['interview_dt'], datetime.datetime) else _safe(str(bg['interview_dt']))}",
        f"Current Stage : {bg['current_stage']}",
        f"Generated     : {gen}",
        "",
        "── SCORES ──────────────────────────────────────────────────────",
        f"  Overall Score   : {sc['overall']}/100  (Rank #{sc.get('rank','N/A')})",
        f"  Keyword Match   : {sc['keyword_match']}%",
        f"  Experience      : {sc['experience']}%",
        f"  Education       : {sc['education']}%",
        f"  Certifications  : {sc['certifications']}%",
        "",
        "── SKILLS ──────────────────────────────────────────────────────",
        f"  Matched  : {', '.join(sk['matched'][:10]) or '—'}",
        f"  Missing  : {', '.join(sk['missing'][:10]) or '—'}",
        "",
    ]

    if notes:
        lines.append("── LAST CALL / MEETING NOTES ───────────────────────────────")
        for n in notes:
            lines.append(f"  [{_fmt_ts(n.get('ts',''))}] {n.get('author','?')}: {n.get('text','')}")
        lines.append("")

    if hist:
        lines.append("── PIPELINE HISTORY ────────────────────────────────────────")
        for h in hist:
            lines.append(f"  [{_fmt_ts(h.get('ts',''))}] → {h.get('stage','?')}  {h.get('note','')}")
        lines.append("")

    lines.append("── OPEN QUESTIONS FOR HIRING MANAGER ───────────────────────")
    for i, q in enumerate(qs, 1):
        lines.append(f"  {i}. {q}")
    lines.append("")
    lines.append("=" * 66)

    return "\n".join(lines)
