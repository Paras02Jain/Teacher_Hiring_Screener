"""
chatbot.py — EduHire Chatbot Module (Groq-powered + RAG)
=========================================================
Uses Groq's FREE API (Llama 3, Mixtral, Gemma) for general-purpose AI chat.
- Get your free key at: https://console.groq.com
- No credit card required
- Answers ANY question — not just predefined ones
- Falls back to rule-based replies if no key provided

RAG upgrade:
  - Semantic search over all candidate profiles via ChromaDB
  - Detects candidate-search queries automatically
  - Injects semantically matched candidates into LLM context
"""

from __future__ import annotations
import re
import json
import urllib.request
import urllib.error
from typing import Optional

# RAG engine (gracefully optional)
try:
    from rag_engine import (
        RAGIndex,
        build_index,
        query_index,
        format_rag_context,
        is_rag_query,
    )
    _RAG_AVAILABLE = True
except ImportError:
    _RAG_AVAILABLE = False
    RAGIndex = None  # type: ignore

GROQ_API_URL = "https://api.groq.com/openai/v1/chat/completions"

# Free models available on Groq (as of 2025)
GROQ_MODELS = {
    "llama-3.3-70b-versatile": "Llama 3.3 70B (Best quality, free)",
    "llama-3.1-8b-instant":    "Llama 3.1 8B (Fastest, free)",
    "mixtral-8x7b-32768":      "Mixtral 8x7B (Great reasoning, free)",
    "gemma2-9b-it":            "Gemma 2 9B (Google, free)",
}
DEFAULT_MODEL = "llama-3.3-70b-versatile"


def _build_context_summary(screening_results: list[dict], job_description: str) -> str:
    """Build compact screening context for the LLM prompt."""
    if not screening_results:
        return "No screening results available yet."

    qualified = [r for r in screening_results if not r.get("filtered")]
    filtered  = [r for r in screening_results if r.get("filtered")]

    lines = [
        f"JOB DESCRIPTION:\n{job_description[:600]}",
        f"\nSCREENING SUMMARY: {len(screening_results)} total | {len(qualified)} qualified | {len(filtered)} filtered out",
        "\nQUALIFIED CANDIDATES (ranked):"
    ]
    for r in qualified[:15]:
        tags = ", ".join(r.get("tags", [])[:5]) or "none"
        mkw  = ", ".join(r.get("matched_kw", [])[:5]) or "none"
        miss = ", ".join(r.get("missing_kw", [])[:4]) or "none"
        lines.append(
            f"  Rank #{r.get('rank')} — {r.get('name','?')} | Score: {r.get('score',0):.1f}% | "
            f"Exp: {r.get('exp',0)} yr | Degree: {'Yes' if r.get('has_deg') else 'No'} | "
            f"Cert: {'Yes' if r.get('has_cert') else 'No'} | Premier: {'Yes' if r.get('is_premier') else 'No'} | "
            f"Skills: {tags} | Matched KW: {mkw} | Missing KW: {miss}"
        )

    if filtered:
        lines.append("\nFILTERED OUT CANDIDATES:")
        for r in filtered[:10]:
            reasons = "; ".join(r.get("fail_reasons", []))
            lines.append(
                f"  — {r.get('name','?')} | Score: {r.get('score',0):.1f}% | "
                f"Exp: {r.get('exp',0)} yr | Reason: {reasons}"
            )
    return "\n".join(lines)


def _call_groq(messages: list[dict], api_key: str, model: str = DEFAULT_MODEL, max_tokens: int = 600) -> Optional[str]:
    """Call Groq's OpenAI-compatible API."""
    payload = json.dumps({
        "model": model,
        "messages": messages,
        "max_tokens": max_tokens,
        "temperature": 0.5,
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
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            return data["choices"][0]["message"]["content"].strip()
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="ignore")
        try:
            msg = json.loads(body).get("error", {}).get("message", body[:200])
        except Exception:
            msg = body[:200]
        if "invalid_api_key" in msg.lower() or "401" in str(e.code):
            return "❌ Invalid Groq API key. Get a free key at https://console.groq.com"
        if "rate_limit" in msg.lower() or "429" in str(e.code):
            return "⏳ Rate limit hit. Wait a moment and try again (Groq free tier has generous limits)."
        return f"⚠️ Groq API error: {msg[:150]}"
    except Exception as e:
        return f"⚠️ Connection error: {str(e)[:150]}"


def get_chatbot_response(
    user_message,
    chat_history,
    screening_results,
    job_description,
    groq_api_key="",
    groq_model=DEFAULT_MODEL,
    rag_index=None,
):
    """
    Main chatbot entry point.

    Parameters
    ----------
    user_message      : The latest user message (any question)
    chat_history      : List of {"role": "user"|"assistant", "content": str}
    screening_results : Full results from ResumeScreener.screen()
    job_description   : JD text used for screening
    groq_api_key      : Free Groq API key (from console.groq.com)
    groq_model        : Groq model to use
    rag_index         : Pre-built RAGIndex from rag_engine.build_index(); built on demand if None
    """
    if not groq_api_key or len(groq_api_key.strip()) < 20:
        return _rule_based_reply(user_message, screening_results, job_description)

    # ── RAG: semantic candidate search ─────────────────────────────────────
    rag_context_block = ""
    if _RAG_AVAILABLE and screening_results:
        try:
            idx = rag_index
            if idx is None:
                idx = build_index(screening_results)
            if idx is not None and is_rag_query(user_message):
                matched_cands = query_index(idx, user_message, top_k=5)
                if matched_cands:
                    rag_context_block = (
                        "\n\n--- SEMANTIC SEARCH RESULTS (most relevant candidates for this query) ---\n"
                        + format_rag_context(matched_cands)
                        + "\n--- END SEMANTIC RESULTS ---"
                    )
        except Exception:
            rag_context_block = ""

    context = _build_context_summary(screening_results, job_description)

    system_prompt = (
        "You are EduBot, an expert AI hiring assistant for EduHire — a teacher resume screening platform.\n\n"
        "You are a GENERAL PURPOSE assistant. You can answer ANY question:\n"
        "- Questions about the screened candidates (use the data below)\n"
        "- Hiring strategy, onboarding tips\n"
        "- HR best practices, salary benchmarks, education policy\n"
        "- Teaching curriculum, school management\n"
        "- General knowledge, writing help, calculations — literally anything\n\n"
        "Guidelines:\n"
        "- Be helpful, friendly, and professional\n"
        "- Use markdown (bold, bullet points) for readability\n"
        "- When discussing candidates, reference actual data from the screening results\n"
        "- If SEMANTIC SEARCH RESULTS are provided below, prioritise those candidates — "
        "they were retrieved by vector similarity specifically for this query\n"
        "- Never refuse to answer general questions — you are a fully capable general assistant\n"
        "- Keep answers concise but complete (3-6 sentences or a short list is ideal)\n\n"
        "--- LIVE SCREENING DATA ---\n"
        + context +
        "\n--- END DATA ---"
        + rag_context_block
    )

    messages = [{"role": "system", "content": system_prompt}]
    for turn in chat_history[-8:]:
        messages.append({"role": turn["role"], "content": turn["content"]})
    messages.append({"role": "user", "content": user_message})

    result = _call_groq(messages, groq_api_key.strip(), model=groq_model, max_tokens=700)
    return result if result else _rule_based_reply(user_message, screening_results, job_description)


def _rule_based_reply(user_message: str, results: list[dict], jd: str) -> str:
    """Fallback rule-based replies when no Groq API key is provided."""
    msg = user_message.lower()
    qualified = [r for r in results if not r.get("filtered")]
    filtered  = [r for r in results if r.get("filtered")]

    def has_word(words):
        return any(re.search(fr'\b{re.escape(w)}\b', msg) for w in words)

    def has_prefix(words):
        return any(re.search(fr'\b{re.escape(w)}', msg) for w in words)

    if has_word(["hello", "hi", "hey", "greet"]):
        return (
            "Hello! 👋 I'm **EduBot**, your AI hiring assistant.\n\n"
            "I can help with:\n"
            "- 🎯 **Candidate analysis** — scores, rankings, skills, experience\n"
            "- 🔍 **Screening insights** — why candidates passed or were filtered\n"
            "- 💼 **Hiring advice** — onboarding, HR tips\n"
            "- 🌐 **General questions** — ask me anything!\n\n"
            "💡 Add your **free Groq API key** in the sidebar (get one at [console.groq.com](https://console.groq.com)) "
            "to unlock full AI-powered responses.\n\nWhat would you like to know?"
        )

    if has_word(["compare"]):
        if len(qualified) >= 2:
            c1, c2 = qualified[0], qualified[1]
            return (
                f"**Comparing top 2 candidates:**\n\n"
                f"🥇 **{c1['name']}** — {c1['score']:.1f}% match\n"
                f"- Experience: {c1.get('exp', 0)} years | Degree: {'✅' if c1.get('has_deg') else '❌'} | Cert: {'✅' if c1.get('has_cert') else '❌'}\n"
                f"- Skills: {', '.join(c1.get('tags', [])[:4]) or 'none'}\n\n"
                f"🥈 **{c2['name']}** — {c2['score']:.1f}% match\n"
                f"- Experience: {c2.get('exp', 0)} years | Degree: {'✅' if c2.get('has_deg') else '❌'} | Cert: {'✅' if c2.get('has_cert') else '❌'}\n"
                f"- Skills: {', '.join(c2.get('tags', [])[:4]) or 'none'}"
            )
        return "Not enough qualified candidates to compare. Run screening first." if not qualified else f"Only one qualified: **{qualified[0]['name']}**."

    if has_word(["best", "top", "recommend", "hire", "who should", "strongest", "number 1", "#1"]):
        if qualified:
            top = qualified[0]
            return (
                f"🏆 **Top recommendation: {top['name']}**\n\n"
                f"- **Score:** {top['score']:.1f}% | **Experience:** {top.get('exp', 0)} yr\n"
                f"- **Skills:** {', '.join(top.get('tags', [])[:4]) or 'none'}\n"
                f"- **Degree:** {'✅' if top.get('has_deg') else '❌'} | **Cert:** {'✅' if top.get('has_cert') else '❌'} | **Premier:** {'✅' if top.get('is_premier') else '❌'}"
            )
        return "No qualified candidates yet. Try lowering threshold sliders and re-running screening."

    if has_word(["score", "match", "percent", "rank"]):
        if qualified:
            rows = [f"**#{r['rank']}** {r['name']} — {r['score']:.1f}%" for r in qualified[:8]]
            return "**Ranked by match score:**\n\n" + "\n".join(rows)
        return "No qualified candidates yet."

    if has_word(["summary", "overview", "total", "how many", "stats", "count"]):
        if results:
            avg = sum(r["score"] for r in qualified) / len(qualified) if qualified else 0
            return (
                f"**Screening Summary:**\n\n"
                f"- 📄 Total resumes: **{len(results)}**\n"
                f"- ✅ Qualified: **{len(qualified)}**\n"
                f"- ❌ Filtered out: **{len(filtered)}**\n"
                f"- 📊 Avg score (qualified): **{avg:.1f}%**"
            )
        return "No screening run yet. Upload resumes and click 'Screen Candidates'."

    if has_word(["experience", "years", "exp"]):
        if qualified:
            top_exp = max(qualified, key=lambda x: x.get("exp", 0))
            avg_exp = sum(r.get("exp", 0) for r in qualified) / len(qualified)
            return (
                f"- Most experienced: **{top_exp['name']}** ({top_exp.get('exp', 0)} yr)\n"
                f"- Average experience: **{avg_exp:.1f} years** across {len(qualified)} qualified candidates"
            )
        return "No qualified candidates."

    if has_prefix(["filter", "reject", "fail", "disqualif", "eliminated"]):
        if filtered:
            reasons: dict[str, int] = {}
            for r in filtered:
                for rsn in r.get("fail_reasons", []):
                    k = rsn.split("(")[0].strip()
                    reasons[k] = reasons.get(k, 0) + 1
            r_str = "\n".join(f"- {k} ({v}x)" for k, v in list(reasons.items())[:5])
            return f"**{len(filtered)} candidate(s) filtered out. Top reasons:**\n\n{r_str}\n\n💡 Lower the sliders to include more candidates."
        return "No candidates were filtered out — all passed."

    if has_prefix(["skill", "competenc", "ability"]):
        if qualified:
            all_tags: dict[str, int] = {}
            for r in qualified:
                for t in r.get("tags", []):
                    all_tags[t] = all_tags.get(t, 0) + 1
            top = sorted(all_tags.items(), key=lambda x: -x[1])[:6]
            return "**Most common skills among qualified candidates:**\n\n" + "\n".join(f"- {s}: {c} candidates" for s, c in top)
        return "No qualified candidates."

    if has_word(["degree", "b.ed", "certified", "certification", "ctet"]):
        if qualified:
            wd = [r['name'] for r in qualified if r.get("has_deg")]
            wc = [r['name'] for r in qualified if r.get("has_cert")]
            return (
                f"**Qualifications (qualified candidates):**\n\n"
                f"- Teaching degree ({len(wd)}): {', '.join(wd) or 'None'}\n"
                f"- Certification ({len(wc)}): {', '.join(wc) or 'None'}"
            )
        return "No qualified candidates."

    if has_word(["premier", "iit", "iim", "harvard", "oxford", "elite"]):
        pq = [r for r in qualified if r.get("is_premier")]
        pf = [r for r in filtered  if r.get("is_premier")]
        if pq or pf:
            return (
                f"**Premier institution candidates:**\n\n"
                f"- ✅ Qualified: {', '.join(r['name'] for r in pq) or 'None'}\n"
                f"- ❌ Filtered: {', '.join(r['name'] for r in pf) or 'None'}"
            )
        return "No premier institution candidates detected."

    if has_word(["keyword", "jd", "gap", "missing"]):
        if qualified:
            miss: dict[str, int] = {}
            for r in qualified:
                for kw in r.get("missing_kw", []):
                    miss[kw] = miss.get(kw, 0) + 1
            top_gaps = sorted(miss.items(), key=lambda x: -x[1])[:5]
            gaps = "\n".join(f"- {k} ({v} missing)" for k, v in top_gaps) if top_gaps else "- None"
            return f"**Most missing JD keywords:**\n\n{gaps}"
        return "Run screening first."

    # Candidate-specific lookup by name
    for r in results:
        name = r.get("name", "").lower()
        if name and len(name) > 2 and name in msg:
            status = "✅ Qualified" if not r.get("filtered") else "❌ Filtered Out"
            return (
                f"**{r['name']}** — {status}\n\n"
                f"- Score: {r['score']:.1f}% | Exp: {r.get('exp',0)} yr\n"
                f"- Degree: {'✅' if r.get('has_deg') else '❌'} | Cert: {'✅' if r.get('has_cert') else '❌'} | Premier: {'✅' if r.get('is_premier') else '❌'}\n"
                f"- Skills: {', '.join(r.get('tags', [])) or 'none'}\n"
                f"- Matched KW: {', '.join(r.get('matched_kw', [])) or 'none'}\n"
                f"- Missing KW: {', '.join(r.get('missing_kw', [])) or 'none'}"
                + (f"\n- Filter Reason: {'; '.join(r.get('fail_reasons', []))}" if r.get("filtered") else "")
            )

    return (
        "I can help with candidate analysis, scores, skills, gaps, hiring tips, and much more.\n\n"
        "💡 **Get a free Groq API key** at [console.groq.com](https://console.groq.com) "
        "and add it in the sidebar to ask me *anything* — salary advice, hiring strategy, general HR tips!\n\n"
        "**Quick questions to try:**\n"
        "- Who is the top candidate?\n"
        "- Give me a screening summary\n"
        "- Why were candidates filtered out?\n"
        "- What skills are most common?"
    )


def get_suggested_questions(screening_results: list[dict]) -> list[str]:
    qualified = [r for r in screening_results if not r.get("filtered")]
    filtered  = [r for r in screening_results if r.get("filtered")]

    # Collect common skill tags to surface a semantic search example
    all_tags: dict[str, int] = {}
    for r in screening_results:
        for t in r.get("tags", []):
            all_tags[t] = all_tags.get(t, 0) + 1
    top_skill = max(all_tags, key=all_tags.get) if all_tags else "Python"

    base = [
        "Who is the top candidate and why?",
        "Give me a summary of the screening results.",
        f"Find candidates with {top_skill} experience who also have education sector exposure.",
        "Which candidates have teaching certification?",
        "What JD keywords are missing in most resumes?",
    ]
    if filtered:
        base.append(f"Why were {len(filtered)} candidates filtered out?")
    if any(r.get("is_premier") for r in screening_results):
        base.append("Which candidates are from premier institutions?")
    if qualified and len(qualified) > 1:
        base.append("Compare the top 2 candidates.")
    if qualified:
        base.append(f"Tell me about {qualified[0].get('name', 'the top candidate')}.")
    return base[:6]
