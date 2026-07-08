# EduHire — Teacher Resume Screener

A Streamlit app that screens teacher resumes against a job description, scores and ranks candidates, and helps a school hiring team move candidates through the pipeline — from JD bias checks to shortlisting, interview scheduling, and offer/reject emails.

## Features

- **Resume Screening & Ranking** (`backend.py`) — Parses PDF resumes with `pdfplumber` and scores them against a job description. Uses Groq's LLM API for structured scoring (score, reasoning, strengths, red flags) when a Groq API key is provided, falling back to sentence-transformer embeddings, then TF-IDF, if not.
- **JD Bias Detection** (`backend.py`) — Flags gendered language, age bias, and unnecessary credential requirements in a job description via Groq.
- **JD A/B Tester** (`jd_ab_tester.py`) — Scores the same resumes against two different job descriptions side by side, useful for comparing JD wording or role scope.
- **Fairness & Equity Audit** (`fairness_audit.py`) — Statistical/keyword-based audit of ranked results across institution tier (premier vs. non-premier) and degree type.
- **Semantic Candidate Search / RAG Chatbot** (`chatbot.py`, `rag_engine.py`) — A Groq-powered chatbot with retrieval-augmented search over all screened candidate profiles (via an in-memory ChromaDB index), so a recruiter can ask natural-language questions like "who has PySpark and education-sector experience?"
- **Auto Email Draft Generator** (`email_generator.py`) — Generates personalized shortlist/rejection emails (Groq-powered with a rule-based fallback), sendable via the Resend API.
- **Candidate Pipeline Tracker** (`pipeline_tracker.py`) — Tracks each candidate through stages (Applied → Screened → Interview Scheduled → Feedback Pending → Offer/Reject), flags stalled or "going cold" candidates, logs timestamped call/meeting notes, and emails the hiring manager on stage changes via Gmail SMTP.
- **Google Calendar Interview Scheduling** (`calendar_scheduler.py`) — OAuth2 integration with Google Calendar to find free/busy slots and auto-schedule interviews with a Google Meet link, using timezone-aware datetimes (default `Asia/Kolkata`).
- **Pre-Interview Brief Generator** (`interview_brief.py`) — Compiles a one-page brief before each interview: resume summary, prior call notes, and suggested/manual interview questions, with an optional Groq-polished narrative version.
- **Executive Summary PDF** (`executive_pdf.py`) — Generates a print-ready PDF report (via `reportlab`) for non-technical stakeholders (e.g. a school principal): ranked candidate cards, filtered candidates table, and a fairness audit snapshot.
- **Visual Analytics Dashboard** (`visualizations.py`) — Plotly charts summarizing screening results for the recruiter dashboard.

## Project Structure

```
teacher_screener_v4/
├── app.py                  # Streamlit entry point / UI
├── backend.py               # Core resume screening, scoring, JD bias detection
├── chatbot.py                # Groq-powered chatbot with RAG
├── rag_engine.py              # ChromaDB semantic index over candidate profiles
├── fairness_audit.py           # Equity/fairness audit of ranked results
├── jd_ab_tester.py              # Side-by-side JD comparison
├── email_generator.py            # Shortlist/rejection email drafts + sending
├── pipeline_tracker.py             # Candidate stage tracking, stall/cold alerts, notes
├── calendar_scheduler.py            # Google Calendar interview scheduling
├── interview_brief.py                # Pre-interview brief generator
├── executive_pdf.py                   # Executive summary PDF export
├── visualizations.py                   # Plotly dashboard charts
├── requirements.txt                     # Python dependencies
├── credentials.json                      # Google OAuth client credentials (keep private)
└── calendar_token.pkl                     # Cached Google OAuth token (keep private)
```

## Setup

1. **Install dependencies**
   ```bash
   pip install -r requirements.txt
   ```

2. **Get a free Groq API key** (used for scoring, bias detection, chatbot, and email generation)
   - Sign up at https://console.groq.com — no credit card required.
   - Enter the key in the app sidebar, or set it as an environment variable, depending on how `app.py` is configured.

3. **(Optional) Set up Google Calendar integration**
   - Create OAuth credentials in Google Cloud Console → APIs & Services → Credentials.
   - Save them as `credentials.json` in the project root.
   - On first use, the OAuth flow will create/refresh `calendar_token.pkl` automatically.

4. **(Optional) Set up email sending**
   - Shortlist/rejection emails send via the Resend API (free tier: 3,000 emails/month).
   - Pipeline stage-change notifications send via Gmail SMTP.

5. **Run the app**
   ```bash
   streamlit run app.py
   ```

