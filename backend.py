"""
backend.py — EduHire Resume Screener v8
========================================
TIER 1 UPGRADES:
  1. LLM-powered scoring via Groq (replaces TF-IDF / sentence-transformers)
     -> Groq returns structured JSON: score, reasoning, strengths, red_flags
     -> Falls back to sentence-transformer -> TF-IDF if no Groq key provided
  2. JD Bias Detection via Groq
     -> Flags gendered language, age bias, unnecessary credentials
     -> Returns a bias_report dict stored in session and shown in UI

Previous changes (v6/v7):
  - Premier institution: strict phrase/regex matching
  - Filtered Out sorted: premier institution first, then by score desc
  - filtered_rank added to each filtered candidate
  - HTML stripped from justifications
"""

from __future__ import annotations
import re, io, os, time, json, urllib.request, urllib.error
from typing import Any

import pdfplumber
import numpy as np

try:
    from sentence_transformers import SentenceTransformer, util
    _ST_AVAILABLE = True
except ImportError:
    _ST_AVAILABLE = False

from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import cosine_similarity as sklearn_cosine

_MODEL_NAME = "all-MiniLM-L6-v2"
_st_model   = None

def _get_st_model():
    global _st_model
    if _st_model is None and _ST_AVAILABLE:
        try:
            _st_model = SentenceTransformer(_MODEL_NAME)
        except Exception as e:
            print(f"[WARN] Could not load sentence-transformer model: {e}")
    return _st_model

# -- Groq API config (same pattern as chatbot.py) ------------------------------
GROQ_API_URL     = "https://api.groq.com/openai/v1/chat/completions"
GROQ_SCORE_MODEL = "llama-3.3-70b-versatile"   # best quality for structured output
GROQ_FAST_MODEL  = "llama-3.1-8b-instant"       # used for justification text


# -- Premier Institution — strict multi-word phrases only ---------------------
PREMIER_INSTITUTION_PHRASES = [
    "indian institute of technology",
    "indian institute of management",
    "delhi university",
    "university of delhi",
    "jawaharlal nehru university",
    "banaras hindu university",
    "hyderabad university",
    "university of hyderabad",
    "jadavpur university",
    "jamia millia islamia",
    "aligarh muslim university",
    "st. stephen's college", "st. stephen's", "st stephens college",
    "miranda house",
    "lady shri ram college",
    "hans raj college",
    "kirori mal college",
    "hindu college",
    "presidency college",
    "st. xavier's college", "st xaviers college",
    "loyola college",
    "christ university",
    "symbiosis international",
    "bits pilani",
    "indian institute of science",
    "tata institute of social sciences",
    "tata institute of fundamental research",
    "lady irwin college",
    "regional institute of education",
    "oxford university", "university of oxford",
    "cambridge university", "university of cambridge",
    "harvard university",
    "massachusetts institute of technology",
    "stanford university",
    "yale university",
    "princeton university",
    "columbia university",
    "london school of economics",
    "imperial college london",
    "university college london",
    "national university of singapore",
    "university of toronto",
    "mcgill university",
]

PREMIER_ABBREVIATION_PATTERNS = [
    r"\biit\b",
    r"\biim\b",
    r"\bjnu\b",
    r"\bbhu\b",
    r"\bamu\b",
    r"\bnit\s+\w+",
    r"\biisc\b",
    r"\btifr\b",
    r"\blsr\b",
]

def detect_premier_institution(text: str) -> bool:
    t = text.lower()
    for phrase in PREMIER_INSTITUTION_PHRASES:
        if phrase in t:
            return True
    for pattern in PREMIER_ABBREVIATION_PATTERNS:
        if re.search(pattern, t, re.IGNORECASE):
            return True
    return False

EDUCATION_KEYWORDS = [
    "b.ed", "bed", "bachelor of education", "master of education", "m.ed", "med",
    "bachelor of arts in education", "bachelor of science in education",
    "education degree", "teaching degree", "pgce",
    "post graduate certificate in education",
    "diploma in education", "b.sc education", "b.a education",
    "bachelor of teaching", "master of teaching", "d.el.ed", "d el ed",
    "bachelor of elementary education", "b.el.ed",
]

CERT_KEYWORDS = [
    "teaching certificate", "teacher certification", "teaching license",
    "state certification", "certified teacher", "licensed teacher",
    "teaching credential", "professional certification", "b.ed", "bed",
    "pedagogy", "pedagogical", "trained teacher", "ctet", "tet",
    "central teacher eligibility", "teacher eligibility test",
]

EXPERIENCE_PATTERNS = [
    r"(\d+)\+?\s*years?\s+(?:of\s+)?(?:teaching|classroom|instructional|education(?:al)?)\s+experience",
    r"(?:teaching|classroom|instructional)\s+experience\s+(?:of\s+)?(\d+)\+?\s*years?",
    r"(\d+)\+?\s*years?\s+(?:as\s+(?:a\s+)?)?(?:teacher|educator|instructor|lecturer)",
    r"taught\s+for\s+(\d+)\+?\s*years?",
    r"(\d+)\+?\s*years?\s+in\s+(?:the\s+)?(?:education|teaching|classroom)",
    r"experience\s*[:\-]\s*(\d+)\s*(?:\+)?\s*years?",
    r"(\d+)\s*(?:\+)?\s*years?\s+(?:of\s+)?work\s+experience",
]

SKILL_KEYWORDS = {
    "curriculum design":    ["curriculum", "syllabus", "lesson plan", "course design"],
    "classroom management": ["classroom management", "student behaviour", "discipline"],
    "assessment & grading": ["assessment", "grading", "rubric", "evaluation", "feedback"],
    "special education":    ["special education", "iep", "inclusion", "learning disability"],
    "ed-technology":        ["edtech", "lms", "google classroom", "smartboard", "technology"],
    "mentoring":            ["mentor", "coaching", "guidance", "counselling"],
    "stem":                 ["stem", "science", "mathematics", "math", "physics", "chemistry"],
    "language arts":        ["english", "literature", "writing", "reading", "language arts"],
    "early childhood":      ["kindergarten", "preschool", "early childhood", "nursery"],
    "research":             ["research", "publication", "thesis", "dissertation"],
}

JD_MATCH_KEYWORDS = [
    "lesson plan", "curriculum", "assessment", "collaboration", "differentiation",
    "cbse", "icse", "cambridge", "montessori", "stem", "google classroom",
    "smartboard", "inclusive", "feedback", "grading", "rubric", "mentoring",
    "classroom management", "special education", "ed-technology",
]


# ==============================================================================
# TIER 1 UPGRADE 1 — Groq LLM Scoring
# Replaces TF-IDF / sentence-transformer cosine similarity with a real LLM call
# ==============================================================================

def _call_groq_raw(
    messages: list[dict],
    api_key: str,
    model: str = GROQ_SCORE_MODEL,
    max_tokens: int = 800,
    temperature: float = 0.1,
    max_retries: int = 5,
) -> str | None:
    """
    Low-level Groq API call — mirrors the exact pattern used in chatbot.py.
    Returns the raw text content string, or None on any failure.
    Includes retry logic with exponential backoff for HTTP 429 Too Many Requests.
    """
    payload = json.dumps({
        "model": model,
        "messages": messages,
        "max_tokens": max_tokens,
        "temperature": temperature,
    }).encode("utf-8")

    req = urllib.request.Request(
        GROQ_API_URL,
        data=payload,
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}",
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
        },
        method="POST",
    )
    
    import time
    for attempt in range(max_retries):
        try:
            with urllib.request.urlopen(req, timeout=45) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                return data["choices"][0]["message"]["content"].strip()
        except urllib.error.HTTPError as e:
            body = e.read().decode("utf-8", errors="ignore")
            if e.code == 429:
                wait_time = (2 ** attempt) * 2
                print(f"[WARN] Groq Rate limit hit (429). Retrying in {wait_time}s... (Attempt {attempt+1}/{max_retries})")
                time.sleep(wait_time)
                continue
            else:
                print(f"[WARN] Groq HTTP error {e.code}: {body[:200]}")
                return None
        except Exception as e:
            if attempt < max_retries - 1:
                print(f"[WARN] Groq connection error: {e}. Retrying in 2s...")
                time.sleep(2)
                continue
            print(f"[WARN] Groq connection error: {e}")
            return None
            
    print("[WARN] Groq max retries exceeded.")
    return None


def _parse_json_from_llm(raw: str) -> dict | None:
    """
    Robustly extract a JSON object from LLM output.
    Handles markdown code fences and stray text before/after the JSON.
    """
    if not raw:
        return None
    # Strip ```json ... ``` or ``` ... ``` fences
    raw = re.sub(r"```(?:json)?", "", raw).strip().rstrip("`").strip()
    # Find the first { ... } block
    match = re.search(r"\{.*\}", raw, re.DOTALL)
    if not match:
        return None
    try:
        return json.loads(match.group())
    except json.JSONDecodeError:
        return None


def groq_score_resume(
    jd: str,
    resume_text: str,
    candidate_name: str,
    groq_api_key: str,
) -> dict | None:
    """
    TIER 1 UPGRADE 1 — LLM-powered resume scoring via Groq.

    Sends JD + resume to Llama 3.3 70B and requests structured JSON:
    {
        "score": <0-100 int>,
        "reasoning": "<2-3 sentence explanation>",
        "strengths": ["strength 1", "strength 2", ...],
        "red_flags": ["concern 1", "concern 2", ...]
    }

    Returns the parsed dict on success, or None on failure.
    Caller should then fall back to sentence-transformer / TF-IDF scoring.
    """
    if not groq_api_key or len(groq_api_key.strip()) < 20:
        return None

    # Truncate to avoid token limits while keeping the most relevant content
    jd_snippet     = jd[:1500].strip()
    resume_snippet = resume_text[:2500].strip()

    system_prompt = (
        "You are an expert school HR consultant specialising in teacher hiring. "
        "Evaluate how well the teacher's resume matches the job description. "
        "You MUST respond with ONLY a valid JSON object — no preamble, no markdown, no extra text.\n\n"
        "Required JSON format:\n"
        "{\n"
        '  "score": <integer 0-100>,\n'
        '  "reasoning": "<2-3 sentence explanation of the score>",\n'
        '  "strengths": ["<strength 1>", "<strength 2>", ...],\n'
        '  "red_flags": ["<concern 1>", "<concern 2>", ...]\n'
        "}\n\n"
        "Scoring rubric:\n"
        "80-100: Excellent match — strong experience, right qualifications, high JD alignment\n"
        "60-79 : Good match — most requirements met, minor gaps\n"
        "40-59 : Partial match — relevant background but notable gaps\n"
        "20-39 : Weak match — some teaching background but poor JD fit\n"
        "0-19  : Poor match — significant misalignment or missing core requirements\n"
        "Respond with ONLY the JSON object. No other text."
    )

    user_prompt = (
        f"CANDIDATE NAME: {candidate_name}\n\n"
        f"JOB DESCRIPTION:\n{jd_snippet}\n\n"
        f"RESUME:\n{resume_snippet}\n\n"
        "Score this candidate. Respond with ONLY the JSON object."
    )

    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user",   "content": user_prompt},
    ]

    raw    = _call_groq_raw(messages, groq_api_key.strip(),
                            model=GROQ_SCORE_MODEL, max_tokens=600, temperature=0.1)
    parsed = _parse_json_from_llm(raw)

    if parsed and isinstance(parsed.get("score"), (int, float)):
        parsed["score"] = round(max(0.0, min(100.0, float(parsed["score"]))), 1)
        parsed.setdefault("reasoning", "")
        parsed.setdefault("strengths", [])
        parsed.setdefault("red_flags", [])
        return parsed

    print(f"[WARN] Could not parse Groq score JSON for {candidate_name}. Raw: {str(raw)[:200]}")
    return None


# ==============================================================================
# TIER 1 UPGRADE 2 — JD Bias Detection via Groq
# ==============================================================================

def detect_jd_bias(jd: str, groq_api_key: str) -> dict:
    """
    TIER 1 UPGRADE 2 — Bias detection on the Job Description.

    Sends the JD to Groq and returns a structured bias report:
    {
        "overall_risk": "Low" | "Medium" | "High",
        "issues": [
            {"type": "Gendered language", "example": "...", "suggestion": "..."},
            ...
        ],
        "summary": "One-line verdict for the hiring manager.",
        "checked": True | False
    }

    Returns a safe default dict if Groq key is missing or the call fails.
    Call this once per screening run (after job description is confirmed).
    """
    if not groq_api_key or len(groq_api_key.strip()) < 20:
        return {
            "overall_risk": "Unknown",
            "issues": [],
            "summary": "Add your Groq API key to enable JD bias detection.",
            "checked": False,
        }

    system_prompt = (
        "You are an HR equity specialist and responsible AI auditor. "
        "Review job descriptions for language that could exclude qualified candidates. "
        "You MUST respond with ONLY a valid JSON object — no preamble, no markdown, no extra text.\n\n"
        "Required JSON format:\n"
        "{\n"
        '  "overall_risk": "<Low | Medium | High>",\n'
        '  "issues": [\n'
        '    {"type": "<bias category>", "example": "<exact phrase from JD>", "suggestion": "<improved wording>"}\n'
        '  ],\n'
        '  "summary": "<1-2 sentence verdict for the hiring manager>"\n'
        "}\n\n"
        "Bias categories to check:\n"
        "1. Gendered language — e.g. 'manpower', 'nurturing (gendered)', 'he/she', male/female pronouns\n"
        "2. Age bias — e.g. 'young and energetic', 'fresh graduate only', excessive experience caps\n"
        "3. Unnecessary credentials — degree required where experience should suffice\n"
        "4. Cultural / religious exclusion — implicit assumptions about background or appearance\n"
        "5. Ability bias — physical requirements irrelevant to the teaching role\n"
        "6. Exclusionary tone — corporate jargon or language that discourages diverse applicants\n\n"
        "If no issues are found return an empty issues array and Low risk.\n"
        "Respond with ONLY the JSON object."
    )

    user_prompt = (
        f"Analyse this job description for bias and return the JSON report:\n\n{jd[:2000]}"
    )

    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user",   "content": user_prompt},
    ]

    raw    = _call_groq_raw(messages, groq_api_key.strip(),
                            model=GROQ_SCORE_MODEL, max_tokens=700, temperature=0.2)
    parsed = _parse_json_from_llm(raw)

    if parsed and "overall_risk" in parsed:
        parsed.setdefault("issues", [])
        parsed.setdefault("summary", "")
        parsed["checked"] = True
        return parsed

    print(f"[WARN] Could not parse JD bias report. Raw: {str(raw)[:200]}")
    return {
        "overall_risk": "Unknown",
        "issues": [],
        "summary": "Bias check could not be completed. Check your Groq API key.",
        "checked": False,
    }


# ==============================================================================
# SCORING PIPELINE — Groq-first with graceful fallback
# ==============================================================================

# Module-level cache so screen() can read Groq metadata after compute_similarity_score()
_last_groq_result: dict | None = None

def _set_last_groq_result(result: dict | None):
    global _last_groq_result
    _last_groq_result = result

def get_last_groq_result() -> dict | None:
    return _last_groq_result


def compute_similarity_score(
    jd: str,
    resume: str,
    candidate_name: str = "",
    groq_api_key: str = "",
) -> tuple[float, str]:
    """
    Score priority:
      1. Groq LLM scoring  (when groq_api_key is provided)
      2. Sentence-transformer semantic similarity
      3. TF-IDF cosine similarity (last resort)

    Returns (score: float, method: str).
    The full Groq metadata dict (strengths, red_flags, reasoning) is stored
    in the module cache and retrieved via get_last_groq_result().
    """
    _set_last_groq_result(None)

    # -- 1. Groq LLM scoring -------------------------------------------------
    if groq_api_key and len(groq_api_key.strip()) >= 20:
        groq_result = groq_score_resume(jd, resume, candidate_name, groq_api_key)
        if groq_result is not None:
            _set_last_groq_result(groq_result)
            return groq_result["score"], "groq_llm"

    # -- 2. Sentence-transformer ---------------------------------------------
    model = _get_st_model()
    if model is not None:
        try:
            emb_jd  = model.encode(jd[:2000],    convert_to_tensor=True)
            emb_res = model.encode(resume[:2000], convert_to_tensor=True)
            sim     = float(util.cos_sim(emb_jd, emb_res)[0][0])
            score   = round(max(0.0, min(100.0, sim * 100)), 1)
            return score, "sentence_transformer"
        except Exception as e:
            print(f"[WARN] sentence-transformer scoring failed: {e}")

    # -- 3. TF-IDF fallback --------------------------------------------------
    try:
        vec   = TfidfVectorizer(stop_words="english", ngram_range=(1, 2), max_features=5000)
        tfidf = vec.fit_transform([jd[:3000], resume[:3000]])
        sim   = float(sklearn_cosine(tfidf[0], tfidf[1])[0][0])
        score = round(max(0.0, min(100.0, sim * 100)), 1)
        return score, "tfidf"
    except Exception as e:
        print(f"[WARN] TF-IDF scoring failed: {e}")
        return 0.0, "failed"


# ==============================================================================
# EXISTING HELPER FUNCTIONS (unchanged from v6/v7)
# ==============================================================================

def extract_text_from_file(uploaded_file) -> str:
    name = uploaded_file.name.lower()
    raw  = uploaded_file.read()
    uploaded_file.seek(0)
    if name.endswith(".pdf"):
        try:
            with pdfplumber.open(io.BytesIO(raw)) as pdf:
                pages = [p.extract_text() or "" for p in pdf.pages]
            text = "\n".join(pages).strip()
            if text:
                return text
        except Exception:
            pass
        return raw.decode("utf-8", errors="replace")
    else:
        for enc in ("utf-8", "latin-1", "cp1252"):
            try:
                return raw.decode(enc)
            except UnicodeDecodeError:
                continue
        return raw.decode("utf-8", errors="replace")

def extract_candidate_name(text: str, filename: str) -> str:
    lines = [l.strip() for l in text.split("\n") if l.strip()]
    for line in lines[:10]:
        parts = line.split()
        if (2 <= len(parts) <= 4
                and all(p[0].isupper() for p in parts if p)
                and not any(c.isdigit() for c in line)
                and len(line) < 55
                and "@" not in line
                and "+" not in line):
            return line
    stem = os.path.splitext(filename)[0].replace("_", " ").replace("-", " ").title()
    return stem

def detect_education_degree(text: str) -> bool:
    return any(kw in text.lower() for kw in EDUCATION_KEYWORDS)

def detect_certification(text: str) -> bool:
    return any(kw in text.lower() for kw in CERT_KEYWORDS)

def extract_experience_years(text: str) -> int:
    years_found = []
    for pattern in EXPERIENCE_PATTERNS:
        for m in re.finditer(pattern, text, re.IGNORECASE):
            try:
                years_found.append(int(m.group(1)))
            except (IndexError, ValueError):
                pass
    if years_found:
        return max(years_found)
    job_blocks = re.findall(
        r"(?:teacher|instructor|lecturer|educator|tutor)\s*[,|.\-]",
        text, re.IGNORECASE
    )
    return max(0, len(job_blocks) - 1)

def extract_skill_tags(text: str) -> list[str]:
    t = text.lower()
    return [skill for skill, kws in SKILL_KEYWORDS.items() if any(kw in t for kw in kws)]

def extract_jd_matched_keywords(jd: str, resume: str) -> list[str]:
    jd_l, res_l = jd.lower(), resume.lower()
    return [kw for kw in JD_MATCH_KEYWORDS if kw in jd_l and kw in res_l]

def extract_jd_missing_keywords(jd: str, resume: str) -> list[str]:
    jd_l, res_l = jd.lower(), resume.lower()
    return [kw for kw in JD_MATCH_KEYWORDS if kw in jd_l and kw not in res_l]


# ==============================================================================
# ATS SCORE COMPUTATION
# Breaks the match score into weighted sub-categories for transparency
# ==============================================================================

ATS_WEIGHTS = {
    "education":     0.25,
    "experience":    0.25,
    "skills":        0.20,
    "keywords":      0.20,
    "certification": 0.10,
}

ATS_EDUCATION_KEYWORDS = [
    "b.ed", "bed", "bachelor of education", "master of education", "m.ed", "med",
    "bachelor of arts", "bachelor of science", "b.sc", "b.a.", "pgce",
    "post graduate certificate", "diploma in education", "d.el.ed", "b.el.ed",
    "bachelor of teaching", "master of teaching", "phd", "m.phil",
]

ATS_SKILL_KEYWORDS = {
    "curriculum design":    ["curriculum", "syllabus", "lesson plan", "course design", "unit plan"],
    "classroom management": ["classroom management", "student behaviour", "discipline", "class control"],
    "assessment & grading": ["assessment", "grading", "rubric", "evaluation", "feedback", "test"],
    "special education":    ["special education", "iep", "inclusion", "learning disability", "sen"],
    "ed-technology":        ["edtech", "lms", "google classroom", "smartboard", "e-learning", "zoom"],
    "mentoring":            ["mentor", "coaching", "guidance", "counselling", "advising"],
    "stem":                 ["stem", "science", "mathematics", "math", "physics", "chemistry", "biology"],
    "language arts":        ["english", "literature", "writing", "reading", "language arts", "grammar"],
    "early childhood":      ["kindergarten", "preschool", "early childhood", "nursery", "primary"],
    "research":             ["research", "publication", "thesis", "dissertation", "journal"],
    "communication":        ["communication", "presentation", "parent meetings", "stakeholder"],
    "leadership":           ["leadership", "head of department", "coordinator", "team lead"],
}

ATS_CERT_KEYWORDS = [
    "teaching certificate", "teacher certification", "teaching license",
    "state certification", "certified teacher", "licensed teacher",
    "teaching credential", "b.ed", "bed", "ctet", "tet",
    "central teacher eligibility", "teacher eligibility test",
    "trained teacher", "pedagogical", "pedagogy",
]


def compute_ats_score(
    jd: str,
    resume_text: str,
    overall_score: float,
    exp: int,
    has_deg: bool,
    has_cert: bool,
    matched_kw: list,
    missing_kw: list,
    tags: list,
) -> dict:
    """
    Compute a multi-category ATS (Applicant Tracking System) score breakdown.

    Returns a dict:
    {
        "education":     {"score": float, "weight": float, "detail": str},
        "experience":    {"score": float, "weight": float, "detail": str},
        "skills":        {"score": float, "weight": float, "detail": str},
        "keywords":      {"score": float, "weight": float, "detail": {"matched": [...], "missing": [...]}},
        "certification": {"score": float, "weight": float, "detail": str},
        "weighted_ats":  float,   # weighted composite ATS score (0-100)
        "overall_score": float,   # the semantic/LLM match score passed in
    }
    """
    txt_lower = resume_text.lower()
    jd_lower  = jd.lower()

    # ── 1. Education Score ────────────────────────────────────────────────────
    edu_hits   = [kw for kw in ATS_EDUCATION_KEYWORDS if kw in txt_lower]
    edu_score  = min(100.0, len(edu_hits) * 20.0) if edu_hits else (60.0 if has_deg else 0.0)
    edu_detail = f"Detected: {', '.join(edu_hits[:4])}" if edu_hits else ("Degree keyword not found" if not has_deg else "Degree detected via pattern")

    # ── 2. Experience Score ───────────────────────────────────────────────────
    # Scale exp linearly; assume 5+ years = 100%, cap at 100
    if exp >= 5:
        exp_score = 100.0
    elif exp >= 3:
        exp_score = 80.0
    elif exp >= 2:
        exp_score = 65.0
    elif exp >= 1:
        exp_score = 45.0
    else:
        exp_score = 10.0
    exp_detail = f"{exp} year(s) of teaching experience detected"

    # ── 3. Skills Score ───────────────────────────────────────────────────────
    skill_hits  = [skill for skill, kws in ATS_SKILL_KEYWORDS.items()
                   if any(kw in txt_lower for kw in kws)]
    jd_skill_hits = [skill for skill, kws in ATS_SKILL_KEYWORDS.items()
                     if any(kw in jd_lower for kw in kws) and skill in skill_hits]
    total_jd_skills = max(1, sum(1 for skill, kws in ATS_SKILL_KEYWORDS.items()
                                  if any(kw in jd_lower for kw in kws)))
    skill_score  = min(100.0, (len(jd_skill_hits) / total_jd_skills) * 100.0)
    skill_detail = f"Matched {len(jd_skill_hits)}/{total_jd_skills} JD skill areas: {', '.join(jd_skill_hits[:4]) or 'None'}"

    # ── 4. Keywords Score ─────────────────────────────────────────────────────
    total_kw    = max(1, len(matched_kw) + len(missing_kw))
    kw_score    = min(100.0, (len(matched_kw) / total_kw) * 100.0) if total_kw else 0.0
    kw_detail   = {"matched": matched_kw, "missing": missing_kw}

    # ── 5. Certification Score ────────────────────────────────────────────────
    cert_hits   = [kw for kw in ATS_CERT_KEYWORDS if kw in txt_lower]
    cert_score  = 100.0 if cert_hits else (50.0 if has_cert else 0.0)
    cert_detail = f"Found: {', '.join(cert_hits[:3])}" if cert_hits else ("Certified via pattern" if has_cert else "No certification detected")

    # ── Weighted composite ────────────────────────────────────────────────────
    weighted_ats = (
        edu_score   * ATS_WEIGHTS["education"] +
        exp_score   * ATS_WEIGHTS["experience"] +
        skill_score * ATS_WEIGHTS["skills"] +
        kw_score    * ATS_WEIGHTS["keywords"] +
        cert_score  * ATS_WEIGHTS["certification"]
    )

    return {
        "education":     {"score": round(edu_score, 1),   "weight": ATS_WEIGHTS["education"],     "detail": edu_detail},
        "experience":    {"score": round(exp_score, 1),   "weight": ATS_WEIGHTS["experience"],    "detail": exp_detail},
        "skills":        {"score": round(skill_score, 1), "weight": ATS_WEIGHTS["skills"],        "detail": skill_detail},
        "keywords":      {"score": round(kw_score, 1),    "weight": ATS_WEIGHTS["keywords"],      "detail": kw_detail},
        "certification": {"score": round(cert_score, 1),  "weight": ATS_WEIGHTS["certification"], "detail": cert_detail},
        "weighted_ats":  round(weighted_ats, 1),
        "overall_score": round(overall_score, 1),
    }


def _sanitize_for_http(text: str) -> str:
    replacements = {
        "\u2022": "-", "\u2013": "-", "\u2014": "-",
        "\u2018": "'", "\u2019": "'", "\u201c": '"', "\u201d": '"',
        "\u2026": "...", "\u00a0": " ",
    }
    for char, repl in replacements.items():
        text = text.replace(char, repl)
    return text.encode("latin-1", errors="ignore").decode("latin-1")

def _strip_html(text: str) -> str:
    return re.sub(r'<[^>]+>', '', text).strip()


# ==============================================================================
# AI JUSTIFICATION — now reads Groq scoring result when available
# ==============================================================================

def generate_ai_justification(
    candidate_name, jd, resume_text, score, rank, is_filtered,
    fail_reasons, tags, exp, has_deg, has_cert, matched_kw, missing_kw,
    is_premier, hf_headers,
    groq_api_key: str = "",
    groq_result: dict | None = None,
) -> str:
    """
    Generate a justification string.

    Priority order:
      1. Reuse Groq scoring result reasoning (free — already computed above)
      2. Call Groq separately for a standalone justification
      3. Rule-based fallback (no API needed)
    """

    # -- 1. Reuse the Groq score reasoning (zero extra API cost) -------------
    if groq_result and groq_result.get("reasoning"):
        reasoning = groq_result["reasoning"].strip()
        strengths = groq_result.get("strengths", [])
        red_flags = groq_result.get("red_flags", [])

        parts = [reasoning]
        if strengths:
            parts.append(f"Key strengths: {'; '.join(strengths[:3])}.")
        if red_flags and is_filtered:
            parts.append(f"Concerns flagged: {'; '.join(red_flags[:2])}.")
        elif red_flags:
            parts.append(f"Areas to probe in interview: {'; '.join(red_flags[:2])}.")

        result = " ".join(parts).strip()
        result = _strip_html(result)
        if result:
            return result

    # -- 2. Dedicated Groq justification call (when no prior Groq result) ---
    if groq_api_key and len(groq_api_key.strip()) >= 20:
        status      = "REJECTED (filtered out)" if is_filtered else f"SELECTED (Rank #{rank})"
        skills_text = ", ".join(tags) if tags else "None detected"
        matched_t   = ", ".join(matched_kw) if matched_kw else "None"
        missing_t   = ", ".join(missing_kw[:6]) if missing_kw else "None"
        filter_t    = "; ".join(fail_reasons) if fail_reasons else "None"

        system_prompt = (
            "You are an expert school HR consultant. "
            "Write a concise 3-4 sentence professional justification for the hiring decision. "
            "Be direct and specific. Do not repeat the numeric score."
        )
        user_prompt = (
            f"JOB DESCRIPTION (excerpt):\n{jd[:500]}\n\n"
            f"CANDIDATE: {candidate_name}\n"
            f"STATUS: {status}\n"
            f"MATCH SCORE: {score:.1f}%\n"
            f"EXPERIENCE: {exp} year(s) teaching\n"
            f"EDUCATION DEGREE: {'Yes' if has_deg else 'No'}\n"
            f"CERTIFICATION: {'Yes' if has_cert else 'No'}\n"
            f"PREMIER INSTITUTION: {'Yes' if is_premier else 'No'}\n"
            f"SKILLS FOUND: {skills_text}\n"
            f"JD KEYWORDS MATCHED: {matched_t}\n"
            f"JD KEYWORDS MISSING: {missing_t}\n"
            f"FILTER REASONS (if rejected): {filter_t}\n\n"
            "Write a professional 3-4 sentence justification."
        )

        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user",   "content": user_prompt},
        ]
        raw = _call_groq_raw(messages, groq_api_key.strip(),
                             model=GROQ_FAST_MODEL, max_tokens=250, temperature=0.4)
        if raw:
            raw = _strip_html(raw)
            raw = re.sub(r'\s+', ' ', raw).strip()
            if raw:
                return raw

    # -- 3. Rule-based fallback ----------------------------------------------
    return _rule_based_justification(
        rank, score, is_filtered, fail_reasons, tags, exp,
        has_deg, has_cert, matched_kw, missing_kw, is_premier
    )


def _rule_based_justification(
    rank, score, is_filtered, fail_reasons, tags, exp,
    has_deg, has_cert, matched_kw, missing_kw, is_premier
) -> str:
    parts = []
    if is_filtered:
        parts.append(f"Candidate did not meet the minimum requirements: {'; '.join(fail_reasons)}.")
        if is_premier:
            parts.append(" Despite attending a premier institution, the hard requirements were not satisfied.")
        if matched_kw:
            parts.append(f" Partial JD alignment found ({', '.join(matched_kw[:3])}) but insufficient to proceed.")
        parts.append(" Recommend reconsidering if requirements are relaxed.")
    else:
        if rank == 1:
            parts.append(f"Top-ranked candidate with {score:.1f}% match to the job description.")
        else:
            parts.append(f"Ranked #{rank} with a {score:.1f}% match score.")
        if has_deg:    parts.append(" Holds a recognised teaching degree.")
        if has_cert:   parts.append(" Teaching certification verified.")
        if is_premier: parts.append(" Graduated from a premier institution.")
        if exp >= 4:   parts.append(f" Brings {exp} years of strong teaching experience.")
        elif exp >= 1: parts.append(f" Has {exp} year(s) of teaching experience.")
        if matched_kw: parts.append(f" Key JD matches: {', '.join(matched_kw[:4])}.")
        if missing_kw: parts.append(f" Gaps: {', '.join(missing_kw[:3])} not evidenced in resume.")
    return "".join(parts).strip()


# ==============================================================================
# ResumeScreener CLASS
# Updated: accepts groq_api_key, passes it through scoring and justification
# ==============================================================================

class ResumeScreener:
    def __init__(self, api_key: str = "", groq_api_key: str = ""):
        """
        api_key      : HuggingFace token (legacy, kept for backwards compatibility)
        groq_api_key : Groq API key — used for LLM scoring AND justification (Tier 1)
        """
        self.hf_headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }
        self.groq_api_key    = groq_api_key.strip()
        self.st_model_loaded = _get_st_model() is not None

    def screen(self, job_description, uploaded_files, config, use_ai_justification=True):
        min_exp       = config.get("min_experience_years", 1)
        min_score_thr = config.get("min_similarity_score", 0.20)
        req_degree    = config.get("require_teaching_degree", True)
        req_cert      = config.get("require_certification", False)

        raw_results = []

        for upload_pos, uf in enumerate(uploaded_files, start=1):
            text       = extract_text_from_file(uf)
            name       = extract_candidate_name(text, uf.name)
            tags       = extract_skill_tags(text)
            exp        = extract_experience_years(text)
            has_deg    = detect_education_degree(text)
            has_cert   = detect_certification(text)
            is_premier = detect_premier_institution(text)
            matched_kw = extract_jd_matched_keywords(job_description, text)
            missing_kw = extract_jd_missing_keywords(job_description, text)

            # TIER 1: Groq LLM scoring (falls back automatically if key absent)
            score, score_method = compute_similarity_score(
                job_description, text,
                candidate_name=name,
                groq_api_key=self.groq_api_key if use_ai_justification else "",
            )
            groq_result = get_last_groq_result()   # None when not using Groq

            fail_reasons = []
            if req_degree and not has_deg:
                fail_reasons.append("no recognised teaching/education degree detected")
            if req_cert and not has_cert:
                fail_reasons.append("no teaching certification or B.Ed found")
            if exp < min_exp:
                fail_reasons.append(
                    f"only {exp} yr(s) of teaching experience found "
                    f"(minimum required: {min_exp})"
                )

            is_filtered = bool(fail_reasons)
            if not is_filtered and score < (min_score_thr * 100):
                fail_reasons.append(
                    f"match score ({score:.1f}%) below required threshold "
                    f"({min_score_thr * 100:.0f}%)"
                )
                is_filtered = True

            # Compute ATS breakdown for every candidate
            ats_breakdown = compute_ats_score(
                jd=job_description,
                resume_text=text,
                overall_score=score,
                exp=exp,
                has_deg=has_deg,
                has_cert=has_cert,
                matched_kw=matched_kw,
                missing_kw=missing_kw,
                tags=tags,
            )

            raw_results.append({
                "name": name, "score": score, "score_method": score_method,
                "filtered": is_filtered, "tags": tags, "exp": exp,
                "has_deg": has_deg, "has_cert": has_cert, "is_premier": is_premier,
                "matched_kw": matched_kw, "missing_kw": missing_kw,
                "fail_reasons": fail_reasons, "upload_pos": upload_pos, "text": text,
                "groq_result": groq_result,  # carries strengths / red_flags / reasoning
                "ats_breakdown": ats_breakdown,
            })

        qualified = sorted([r for r in raw_results if not r["filtered"]], key=lambda x: -x["score"])
        filtered_list = sorted(
            [r for r in raw_results if r["filtered"]],
            key=lambda x: (0 if x["is_premier"] else 1, -x["score"])
        )

        final = []

        for rank, res in enumerate(qualified, start=1):
            justification = generate_ai_justification(
                candidate_name=res["name"], jd=job_description, resume_text=res["text"],
                score=res["score"], rank=rank, is_filtered=False, fail_reasons=[],
                tags=res["tags"], exp=res["exp"], has_deg=res["has_deg"], has_cert=res["has_cert"],
                matched_kw=res["matched_kw"], missing_kw=res["missing_kw"], is_premier=res["is_premier"],
                hf_headers=self.hf_headers if use_ai_justification else {},
                groq_api_key=self.groq_api_key if use_ai_justification else "",
                groq_result=res.get("groq_result"),
            )
            final.append({
                "name": res["name"], "score": res["score"], "score_method": res["score_method"],
                "filtered": False, "tags": res["tags"], "exp": res["exp"],
                "has_deg": res["has_deg"], "has_cert": res["has_cert"], "is_premier": res["is_premier"],
                "matched_kw": res["matched_kw"], "missing_kw": res["missing_kw"],
                "justification": justification, "upload_pos": res["upload_pos"], "rank": rank,
                # ATS breakdown
                "ats_breakdown": res.get("ats_breakdown", {}),
                # NEW Groq-specific fields exposed for app.py UI
                "groq_strengths": res["groq_result"].get("strengths", []) if res.get("groq_result") else [],
                "groq_red_flags": res["groq_result"].get("red_flags", []) if res.get("groq_result") else [],
                "groq_reasoning": res["groq_result"].get("reasoning", "") if res.get("groq_result") else "",
            })

        for filtered_rank, res in enumerate(filtered_list, start=1):
            justification = generate_ai_justification(
                candidate_name=res["name"], jd=job_description, resume_text=res["text"],
                score=res["score"], rank=None, is_filtered=True, fail_reasons=res["fail_reasons"],
                tags=res["tags"], exp=res["exp"], has_deg=res["has_deg"], has_cert=res["has_cert"],
                matched_kw=res["matched_kw"], missing_kw=res["missing_kw"], is_premier=res["is_premier"],
                hf_headers=self.hf_headers if use_ai_justification else {},
                groq_api_key=self.groq_api_key if use_ai_justification else "",
                groq_result=res.get("groq_result"),
            )
            final.append({
                "name": res["name"], "score": res["score"], "score_method": res["score_method"],
                "filtered": True, "tags": res["tags"], "exp": res["exp"],
                "has_deg": res["has_deg"], "has_cert": res["has_cert"], "is_premier": res["is_premier"],
                "matched_kw": res["matched_kw"], "missing_kw": res["missing_kw"],
                "justification": justification, "upload_pos": res["upload_pos"],
                "rank": None, "filtered_rank": filtered_rank,
                # ATS breakdown
                "ats_breakdown": res.get("ats_breakdown", {}),
                "groq_strengths": res["groq_result"].get("strengths", []) if res.get("groq_result") else [],
                "groq_red_flags": res["groq_result"].get("red_flags", []) if res.get("groq_result") else [],
                "groq_reasoning": res["groq_result"].get("reasoning", "") if res.get("groq_result") else "",
            })

        return final
