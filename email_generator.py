"""
email_generator.py — EduHire Auto Email Draft Generator
=========================================================
Generates personalised shortlist / rejection emails for each candidate.

- Uses Groq API (same key as chatbot) when available
- Falls back to a clean rule-based template when no key is provided
- All emails are editable in-app before copy / download
- Sends via Resend API (free tier: 3 000 emails/month, works with Gmail)
"""

from __future__ import annotations
import json
import urllib.request
import urllib.error
from typing import Optional

GROQ_API_URL = "https://api.groq.com/openai/v1/chat/completions"
DEFAULT_MODEL = "llama-3.3-70b-versatile"


# ── Groq call ─────────────────────────────────────────────────────────────────

def _call_groq(prompt: str, api_key: str, model: str = DEFAULT_MODEL) -> Optional[str]:
    payload = json.dumps({
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "max_tokens": 500,
        "temperature": 0.4,
    }).encode("utf-8")

    req = urllib.request.Request(
        GROQ_API_URL,
        data=payload,
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}",
            "User-Agent": "Mozilla/5.0",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            return data["choices"][0]["message"]["content"].strip()
    except Exception:
        return None


# ── Main public function ──────────────────────────────────────────────────────

def generate_email_draft(
    candidate: dict,
    job_description: str,
    school_name: str = "Our School",
    hr_name: str = "The Hiring Team",
    groq_api_key: str = "",
    groq_model: str = DEFAULT_MODEL,
) -> dict:
    """
    Returns a dict:
      {
        "subject": str,
        "body":    str,
        "type":    "shortlist" | "rejection"
      }
    """
    is_filtered = candidate.get("filtered", False)
    email_type  = "rejection" if is_filtered else "shortlist"
    name        = candidate.get("name", "Candidate")
    score       = candidate.get("score", 0)
    exp         = candidate.get("exp", 0)
    tags        = candidate.get("tags", [])
    matched_kw  = candidate.get("matched_kw", [])
    missing_kw  = candidate.get("missing_kw", [])
    fail_reasons= candidate.get("fail_reasons", [])
    has_deg     = candidate.get("has_deg", False)
    has_cert    = candidate.get("has_cert", False)
    is_premier  = candidate.get("is_premier", False)
    rank        = candidate.get("rank")

    # ── Try AI generation ────────────────────────────────────────────────
    if groq_api_key and len(groq_api_key.strip()) > 20:
        prompt = _build_prompt(
            email_type, name, score, exp, tags, matched_kw, missing_kw,
            fail_reasons, has_deg, has_cert, is_premier, rank,
            job_description, school_name, hr_name
        )
        result = _call_groq(prompt, groq_api_key.strip(), groq_model)
        if result:
            subject, body = _parse_ai_response(result, email_type, name, school_name)
            return {"subject": subject, "body": body, "type": email_type}

    # ── Fallback: rule-based template ────────────────────────────────────
    return _rule_based_email(
        email_type, name, score, exp, tags, matched_kw, missing_kw,
        fail_reasons, has_deg, has_cert, is_premier, rank,
        school_name, hr_name
    )


# ── Prompt builder ────────────────────────────────────────────────────────────

def _build_prompt(
    email_type, name, score, exp, tags, matched_kw, missing_kw,
    fail_reasons, has_deg, has_cert, is_premier, rank,
    jd, school_name, hr_name
) -> str:
    skills_str  = ", ".join(tags[:5])    or "Not specified"
    matched_str = ", ".join(matched_kw[:5]) or "None"
    missing_str = ", ".join(missing_kw[:4]) or "None"
    reasons_str = "; ".join(fail_reasons)   or "Did not meet minimum criteria"

    if email_type == "shortlist":
        instruction = (
            f"Write a warm, professional shortlisting email to {name} from {school_name}. "
            f"Inform them they have been shortlisted for a teaching position. "
            f"Mention their match score ({score:.0f}%), {exp} years of experience, "
            f"and their key matching skills: {matched_str}. "
            f"Invite them for the next stage (interview/document verification). "
            f"Keep it friendly, concise (4-5 sentences), and encouraging."
        )
    else:
        instruction = (
            f"Write a polite, empathetic rejection email to {name} from {school_name}. "
            f"Thank them for applying. Mention the rejection reason briefly: {reasons_str}. "
            f"Acknowledge any strengths if they exist: skills {skills_str}, "
            f"matched keywords: {matched_str}. "
            f"Encourage them to apply for future openings. "
            f"Keep it respectful and professional (4-5 sentences). Do NOT be harsh."
        )

    return (
        f"{instruction}\n\n"
        f"JOB DESCRIPTION SUMMARY:\n{jd[:400]}\n\n"
        f"Respond in this EXACT format — two sections, nothing else:\n"
        f"SUBJECT: <email subject line>\n"
        f"BODY:\n<email body>\n\n"
        f"Sign off with: Warm regards,\n{hr_name}\n{school_name}"
    )


# ── Parse AI response ─────────────────────────────────────────────────────────

def _parse_ai_response(text: str, email_type: str, name: str, school_name: str) -> tuple[str, str]:
    subject = ""
    body    = ""
    lines   = text.strip().splitlines()

    body_start = None
    for i, line in enumerate(lines):
        if line.strip().upper().startswith("SUBJECT:"):
            subject = line.split(":", 1)[1].strip()
        if line.strip().upper().startswith("BODY:"):
            body_start = i + 1
            break

    if body_start is not None:
        body = "\n".join(lines[body_start:]).strip()

    # Fallbacks if parsing fails
    if not subject:
        subject = (f"Your Application — Shortlisted for Interview at {school_name}"
                   if email_type == "shortlist"
                   else f"Regarding Your Application at {school_name}")
    if not body:
        body = text.strip()

    return subject, body


# ── Rule-based fallback ───────────────────────────────────────────────────────

def _rule_based_email(
    email_type, name, score, exp, tags, matched_kw, missing_kw,
    fail_reasons, has_deg, has_cert, is_premier, rank,
    school_name, hr_name
) -> dict:

    if email_type == "shortlist":
        subject = f"Congratulations! You've been shortlisted — {school_name}"

        strengths = []
        if has_deg:     strengths.append("your teaching degree")
        if has_cert:    strengths.append("your teaching certification")
        if is_premier:  strengths.append("your premier institution background")
        if exp >= 3:    strengths.append(f"your {exp} years of classroom experience")
        if matched_kw:  strengths.append(f"strong alignment with {', '.join(matched_kw[:3])}")

        strengths_line = (
            f"Your profile stood out for {', '.join(strengths[:3])}."
            if strengths else
            f"Your profile showed a {score:.0f}% match with our job requirements."
        )

        rank_line = f"You have been ranked #{rank} out of all applicants." if rank else ""

        body = f"""Dear {name},

We are pleased to inform you that, following a careful review of applications for the teaching position at {school_name}, you have been shortlisted for the next stage of our selection process.

{strengths_line} {rank_line}

We would like to invite you for an interview and document verification. Our team will be in touch shortly with further details regarding the date, time, and format.

Please keep your original qualification certificates, identity proof, and experience letters ready. If you have any questions in the meantime, feel free to reach out to us.

We look forward to speaking with you!

Warm regards,
{hr_name}
{school_name}"""

    else:  # rejection
        subject = f"Regarding Your Application at {school_name}"

        reason_line = ""
        if fail_reasons:
            reason = fail_reasons[0].split("(")[0].strip()
            reason_line = f"After careful review, we found that your profile did not meet our minimum requirements at this time ({reason})."
        else:
            reason_line = "After careful review, we found that other candidates were a closer match for this particular role."

        strength_line = ""
        if matched_kw:
            strength_line = f"We did note your background in {', '.join(matched_kw[:2])}, which reflects a genuine interest in this field."
        elif tags:
            strength_line = f"We appreciated your skills in {', '.join(tags[:2])}."

        body = f"""Dear {name},

Thank you for taking the time to apply for the teaching position at {school_name} and for your interest in joining our team.

{reason_line} {strength_line}

We encourage you to continue developing your skills and to apply for future openings that may be a better fit for your profile. We will be updating our vacancies regularly on our website.

We wish you the very best in your job search and future endeavours.

Warm regards,
{hr_name}
{school_name}"""

    return {"subject": subject, "body": body, "type": email_type}


# ── Bulk generation ───────────────────────────────────────────────────────────

def generate_all_emails(
    results: list[dict],
    job_description: str,
    school_name: str = "Our School",
    hr_name: str = "The Hiring Team",
    groq_api_key: str = "",
    groq_model: str = DEFAULT_MODEL,
) -> list[dict]:
    """Generate email drafts for ALL candidates (qualified + filtered)."""
    emails = []
    for candidate in results:
        draft = generate_email_draft(
            candidate, job_description, school_name, hr_name,
            groq_api_key, groq_model
        )
        emails.append({
            "name":     candidate.get("name", "Unknown"),
            "filtered": candidate.get("filtered", False),
            "rank":     candidate.get("rank"),
            "score":    candidate.get("score", 0),
            "subject":  draft["subject"],
            "body":     draft["body"],
            "type":     draft["type"],
        })
    return emails


# ==============================================================================
# EMAIL SENDER — sends email via Gmail SMTP with an App Password
#
#  Setup (one-time, ~2 minutes):
#    1. Go to myaccount.google.com → Security → 2-Step Verification  (enable it)
#    2. Then go to myaccount.google.com/apppasswords
#    3. Name it "EduHire" → click Create → copy the 16-character password
#    4. Paste it (with or without spaces) into the sidebar field below
#
#  Sends to ANYONE — no domain, no third-party service, no credit card.
# ==============================================================================

import smtplib
import ssl
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

GMAIL_SMTP_HOST = "smtp.gmail.com"
GMAIL_SMTP_PORT = 465   # SSL


def send_email(
    gmail_app_password: str,
    sender_email: str,
    recipient_email: str,
    subject: str,
    body: str,
    sender_name: str = "EduHire Screener",
) -> dict:
    """
    Send a single email via Gmail SMTP using an App Password.

    No third-party service needed — sends directly through your Gmail account
    to any recipient worldwide.

    Args:
        gmail_app_password : 16-character Google App Password (spaces ignored)
        sender_email       : Your Gmail address (e.g. you@gmail.com)
        recipient_email    : Candidate's email address
        subject            : Email subject line
        body               : Plain-text email body
        sender_name        : Display name shown in the From field

    Returns:
        {"success": True,  "message": "✅ Email sent to ..."}
        {"success": False, "message": "❌ <reason>"}
    """
    # ── Validate inputs ──────────────────────────────────────────────────
    app_password = gmail_app_password.replace(" ", "").strip() if gmail_app_password else ""
    if len(app_password) != 16:
        return {
            "success": False,
            "message": (
                "❌ App Password must be exactly 16 characters. "
                "Generate one at myaccount.google.com/apppasswords."
            ),
        }

    if not sender_email or "@gmail.com" not in sender_email.lower():
        return {
            "success": False,
            "message": "❌ Sender email must be a Gmail address (e.g. you@gmail.com).",
        }

    if not recipient_email or "@" not in recipient_email:
        return {
            "success": False,
            "message": "❌ Please enter a valid recipient email address.",
        }

    # ── Build the MIME message ───────────────────────────────────────────
    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"]    = f"{sender_name} <{sender_email}>" if sender_name else sender_email
    msg["To"]      = recipient_email
    msg.attach(MIMEText(body, "plain", "utf-8"))

    # ── Send via Gmail SMTP over SSL ─────────────────────────────────────
    try:
        context = ssl.create_default_context()
        with smtplib.SMTP_SSL(GMAIL_SMTP_HOST, GMAIL_SMTP_PORT, context=context, timeout=20) as server:
            server.login(sender_email.strip(), app_password)
            server.sendmail(sender_email.strip(), recipient_email.strip(), msg.as_string())
        return {
            "success": True,
            "message": f"✅ Email sent to {recipient_email}",
        }

    except smtplib.SMTPAuthenticationError:
        return {
            "success": False,
            "message": (
                "❌ Authentication failed. Check that:\n"
                "  • The Gmail address is correct\n"
                "  • The App Password is the 16-char code (not your Gmail login password)\n"
                "  • 2-Step Verification is enabled on your Google account"
            ),
        }
    except smtplib.SMTPRecipientsRefused:
        return {"success": False, "message": f"❌ Recipient address rejected: {recipient_email}"}
    except smtplib.SMTPException as e:
        return {"success": False, "message": f"❌ SMTP error: {str(e)}"}
    except OSError as e:
        return {"success": False, "message": f"❌ Network error: {str(e)}"}
