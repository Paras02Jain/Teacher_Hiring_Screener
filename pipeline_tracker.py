"""
pipeline_tracker.py — EduHire Pipeline Tracker v2
===================================================
Features:
  1. Candidate pipeline stage tracker (Applied → Screened → Interview Scheduled → Feedback Pending → Offer/Reject)
  2. Stall detection — flags candidates with no activity for X days
  3. Cold candidate monitor — alerts before candidates go dark
  4. Call/meeting notes logger with timestamps
  5. Gmail notification — sends a stage-change email via Gmail SMTP whenever
     a candidate is moved, containing candidate name, old → new stage, and any note.

All data is stored in Streamlit session_state (no DB required).
"""

from __future__ import annotations
import datetime
import smtplib
import ssl
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from typing import Optional

# ── Pipeline Stages ───────────────────────────────────────────────────────────
PIPELINE_STAGES = [
    "Applied",
    "Screened",
    "Interview Scheduled",
    "Feedback Pending",
    "Offer",
    "Rejected",
]

STAGE_COLORS = {
    "Applied":              ("#1E3A5F", "#7DD3FC"),
    "Screened":             ("#1A3A2A", "#6EE7B7"),
    "Interview Scheduled":  ("#3B2F0A", "#FCD34D"),
    "Feedback Pending":     ("#2D1A5E", "#C4B5FD"),
    "Offer":                ("#065F46", "#34D399"),
    "Rejected":             ("#3D0A14", "#FDA4AF"),
}

STAGE_ICONS = {
    "Applied":              "📥",
    "Screened":             "✅",
    "Interview Scheduled":  "🗓️",
    "Feedback Pending":     "⏳",
    "Offer":                "🎉",
    "Rejected":             "❌",
}

STAGE_EMAIL_COLOR = {
    "Applied":              "#0EA5E9",
    "Screened":             "#10B981",
    "Interview Scheduled":  "#F59E0B",
    "Feedback Pending":     "#8B5CF6",
    "Offer":                "#10B981",
    "Rejected":             "#EF4444",
}


# ── Gmail Stage-Change Notification ──────────────────────────────────────────

def send_gmail_stage_notification(
    gmail_app_password: str,
    sender_email: str,
    recipient_email: str,
    candidate_name: str,
    old_stage: str,
    new_stage: str,
    school_name: str = "EduHire",
    hr_name: str = "The Hiring Team",
    note: str = "",
) -> dict:
    """
    Sends a stage-change notification email via Gmail SMTP (App Password auth).
    Returns {"success": bool, "message": str}
    """
    app_password = gmail_app_password.replace(" ", "").strip() if gmail_app_password else ""
    if len(app_password) != 16:
        return {"success": False, "message": "App Password must be 16 characters. Get one at myaccount.google.com/apppasswords."}
    if not sender_email or "@gmail.com" not in sender_email.lower():
        return {"success": False, "message": "Sender must be a Gmail address."}
    if not recipient_email or "@" not in recipient_email:
        return {"success": False, "message": "Recipient email is invalid."}

    icon     = STAGE_ICONS.get(new_stage, "📋")
    old_icon = STAGE_ICONS.get(old_stage, "📋")
    accent   = STAGE_EMAIL_COLOR.get(new_stage, "#0EA5E9")
    now_str  = datetime.datetime.now().strftime("%d %b %Y at %I:%M %p")
    subject  = f"[{school_name}] {icon} {candidate_name} moved to {new_stage}"

    note_block = ""
    if note.strip():
        note_block = f"""
  <!-- Note Section -->
  <tr>
    <td style="padding:0 0 8px;">
      <table width="100%" cellpadding="0" cellspacing="0">
        <tr>
          <td style="padding:0 40px;">
            <div style="background:linear-gradient(135deg,#EFF6FF,#F0F9FF);border-left:4px solid #0EA5E9;border-radius:0 12px 12px 0;padding:16px 20px;">
              <table width="100%" cellpadding="0" cellspacing="0">
                <tr>
                  <td>
                    <p style="margin:0 0 6px;font-size:10px;font-weight:800;color:#0369A1;text-transform:uppercase;letter-spacing:1.5px;">📌 Recruiter Note</p>
                    <p style="margin:0;font-size:14px;color:#1E293B;line-height:1.75;font-style:italic;">"{note}"</p>
                  </td>
                </tr>
              </table>
            </div>
          </td>
        </tr>
      </table>
    </td>
  </tr>"""

    # Build stage timeline dots
    stage_list = ["Applied", "Screened", "Interview Scheduled", "Feedback Pending", "Offer"]
    stage_colors_map = {
        "Applied": "#0EA5E9", "Screened": "#10B981",
        "Interview Scheduled": "#F59E0B", "Feedback Pending": "#8B5CF6",
        "Offer": "#10B981", "Rejected": "#EF4444",
    }
    new_stage_idx = stage_list.index(new_stage) if new_stage in stage_list else -1

    timeline_dots = ""
    for si, s in enumerate(stage_list):
        dot_color = stage_colors_map.get(s, "#94A3B8")
        if si < new_stage_idx:
            dot_bg = dot_color
            dot_border = dot_color
            dot_text_color = "#FFFFFF"
        elif si == new_stage_idx:
            dot_bg = dot_color
            dot_border = dot_color
            dot_text_color = "#FFFFFF"
        else:
            dot_bg = "#F1F5F9"
            dot_border = "#CBD5E1"
            dot_text_color = "#94A3B8"
        connector = f'<td width="24" style="vertical-align:middle;"><div style="height:2px;background:{"#CBD5E1" if si >= new_stage_idx else dot_color};"></div></td>' if si < len(stage_list) - 1 else ""
        dot_label = s[:3].upper()
        timeline_dots += f"""
          <td align="center" style="vertical-align:top;padding:0 2px;">
            <div style="width:28px;height:28px;border-radius:50%;background:{dot_bg};border:2px solid {dot_border};
                        display:flex;align-items:center;justify-content:center;margin:0 auto 4px;">
              <span style="font-size:8px;font-weight:800;color:{dot_text_color};">{"✓" if si < new_stage_idx else ("●" if si == new_stage_idx else "○")}</span>
            </div>
            <div style="font-size:7px;color:{"#475569" if si <= new_stage_idx else "#94A3B8"};font-weight:{"700" if si == new_stage_idx else "500"};text-align:center;max-width:44px;">{dot_label}</div>
          </td>"""
        if si < len(stage_list) - 1:
            timeline_dots += connector

    html_body = f"""<!DOCTYPE html>
<html>
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width,initial-scale=1">
  <title>EduHire Pipeline Update</title>
</head>
<body style="margin:0;padding:0;background:#0F172A;font-family:'Segoe UI',system-ui,Arial,sans-serif;">

<!-- Outer wrapper -->
<table width="100%" cellpadding="0" cellspacing="0" style="background:#0F172A;padding:40px 16px;">
<tr><td align="center">

<!-- Card -->
<table width="600" cellpadding="0" cellspacing="0"
       style="background:#1E293B;border-radius:20px;overflow:hidden;
              box-shadow:0 20px 60px rgba(0,0,0,.5);max-width:600px;width:100%;">

  <!-- ═══ HEADER BANNER ═══ -->
  <tr>
    <td style="background:linear-gradient(135deg,#0D1B2A 0%,#0F3460 60%,#1a1a5e 100%);
               padding:0;position:relative;">
      <!-- Top accent line -->
      <div style="height:4px;background:linear-gradient(90deg,{accent},{accent}88,transparent);"></div>
      <table width="100%" cellpadding="0" cellspacing="0">
        <tr>
          <td style="padding:28px 36px 24px;">
            <!-- Logo row -->
            <table cellpadding="0" cellspacing="0">
              <tr>
                <td style="padding-right:12px;">
                  <div style="width:42px;height:42px;background:{accent}22;border-radius:10px;
                              border:1.5px solid {accent}44;text-align:center;line-height:42px;
                              font-size:20px;">🎓</div>
                </td>
                <td>
                  <p style="margin:0;font-size:10px;font-weight:700;color:{accent};
                             letter-spacing:2.5px;text-transform:uppercase;">EduHire Pipeline Tracker</p>
                  <p style="margin:2px 0 0;font-size:16px;font-weight:800;color:#FFFFFF;">{school_name}</p>
                </td>
              </tr>
            </table>
          </td>
          <td style="padding:28px 36px 24px;" align="right">
            <div style="background:{accent}15;border:1px solid {accent}33;border-radius:100px;
                        padding:6px 16px;display:inline-block;">
              <span style="font-size:11px;font-weight:700;color:{accent};">{now_str}</span>
            </div>
          </td>
        </tr>
      </table>
    </td>
  </tr>

  <!-- ═══ STATUS BADGE ═══ -->
  <tr>
    <td style="background:{accent}0D;padding:28px 36px 20px;text-align:center;
               border-bottom:1px solid #334155;">
      <!-- Badge pill -->
      <div style="display:inline-block;background:{accent}18;border:2px solid {accent};
                  border-radius:100px;padding:12px 32px;margin-bottom:20px;">
        <span style="font-size:16px;font-weight:800;color:{accent};">{icon} Candidate Moved to {new_stage}</span>
      </div>
      <!-- Candidate name -->
      <h1 style="margin:0 0 4px;font-size:30px;font-weight:900;color:#F8FAFC;
                 letter-spacing:-0.5px;">{candidate_name}</h1>
      <p style="margin:0;font-size:13px;color:#94A3B8;font-weight:400;">
        Stage update as of {now_str}
      </p>
    </td>
  </tr>

  <!-- ═══ STAGE TRANSITION ═══ -->
  <tr>
    <td style="padding:28px 36px 24px;border-bottom:1px solid #334155;">
      <table width="100%" cellpadding="0" cellspacing="0">
        <tr>
          <!-- FROM box -->
          <td width="44%" align="center">
            <div style="background:#0F172A;border:1.5px solid #334155;border-radius:14px;padding:18px 20px;">
              <p style="margin:0 0 4px;font-size:9px;font-weight:800;color:#64748B;
                         text-transform:uppercase;letter-spacing:2px;">FROM</p>
              <div style="width:36px;height:2px;background:#334155;margin:6px auto 10px;border-radius:2px;"></div>
              <p style="margin:0;font-size:17px;font-weight:700;color:#94A3B8;">{old_icon}</p>
              <p style="margin:4px 0 0;font-size:14px;font-weight:700;color:#CBD5E1;">{old_stage}</p>
            </div>
          </td>

          <!-- Arrow -->
          <td width="12%" align="center">
            <div style="text-align:center;">
              <div style="width:32px;height:32px;background:{accent}22;border-radius:50%;
                          border:2px solid {accent}55;margin:0 auto;line-height:30px;text-align:center;">
                <span style="color:{accent};font-size:16px;font-weight:700;">→</span>
              </div>
            </div>
          </td>

          <!-- TO box -->
          <td width="44%" align="center">
            <div style="background:{accent}12;border:2px solid {accent};border-radius:14px;padding:18px 20px;
                        box-shadow:0 0 24px {accent}22;">
              <p style="margin:0 0 4px;font-size:9px;font-weight:800;color:{accent};
                         text-transform:uppercase;letter-spacing:2px;">TO</p>
              <div style="width:36px;height:2px;background:{accent}66;margin:6px auto 10px;border-radius:2px;"></div>
              <p style="margin:0;font-size:17px;font-weight:700;color:{accent};">{icon}</p>
              <p style="margin:4px 0 0;font-size:14px;font-weight:800;color:{accent};">{new_stage}</p>
            </div>
          </td>
        </tr>
      </table>
    </td>
  </tr>

  <!-- ═══ PIPELINE PROGRESS ═══ -->
  <tr>
    <td style="padding:22px 36px;border-bottom:1px solid #334155;">
      <p style="margin:0 0 14px;font-size:10px;font-weight:800;color:#64748B;
                 text-transform:uppercase;letter-spacing:2px;">📍 Pipeline Progress</p>
      <table width="100%" cellpadding="0" cellspacing="0">
        <tr>{timeline_dots}</tr>
      </table>
    </td>
  </tr>

  {note_block}

  <!-- ═══ DECORATIVE DIVIDER ═══ -->
  <tr>
    <td style="padding:0 36px;">
      <div style="height:1px;background:linear-gradient(90deg,transparent,{accent}44,transparent);"></div>
    </td>
  </tr>

  <!-- ═══ ACTION CTA ═══ -->
  <tr>
    <td style="padding:24px 36px;text-align:center;">
      <p style="margin:0 0 14px;font-size:13px;color:#94A3B8;line-height:1.6;">
        This is an automated stage-change alert from your hiring pipeline.<br>
        Log in to <strong style="color:{accent};">EduHire</strong> to take action on this candidate.
      </p>
      <div style="display:inline-block;background:{accent};border-radius:10px;padding:12px 32px;">
        <span style="color:#FFFFFF;font-size:14px;font-weight:700;">View in EduHire →</span>
      </div>
    </td>
  </tr>

  <!-- ═══ FOOTER ═══ -->
  <tr>
    <td style="background:#0D1B2A;padding:20px 36px;border-top:1px solid #334155;">
      <table width="100%" cellpadding="0" cellspacing="0">
        <tr>
          <td>
            <p style="margin:0;font-size:11px;color:#475569;line-height:1.6;">
              Sent by <strong style="color:#64748B;">{hr_name}</strong> via EduHire Pipeline Tracker
            </p>
            <p style="margin:2px 0 0;font-size:10px;color:#374151;">
              Automated notification — do not reply to this email.
            </p>
          </td>
          <td align="right">
            <div style="width:32px;height:32px;background:{accent}15;border-radius:8px;
                        text-align:center;line-height:32px;font-size:16px;
                        border:1px solid {accent}22;">🎓</div>
          </td>
        </tr>
      </table>
    </td>
  </tr>

  <!-- Bottom accent bar -->
  <tr>
    <td style="height:4px;background:linear-gradient(90deg,transparent,{accent},{accent}88,transparent);"></td>
  </tr>

</table>
<!-- End card -->

</td></tr>
</table>
<!-- End wrapper -->
</body>
</html>"""

    plain_body = (
        f"[{school_name}] Candidate Update\n\n"
        f"Candidate : {candidate_name}\n"
        f"Stage     : {old_stage} → {new_stage}\n"
        f"Date      : {now_str}\n"
    )
    if note.strip():
        plain_body += f"\nNote: {note}\n"
    plain_body += f"\n— {hr_name} via EduHire Pipeline Tracker"

    try:
        msg = MIMEMultipart("alternative")
        msg["Subject"] = subject
        msg["From"]    = f"{hr_name} <{sender_email}>"
        msg["To"]      = recipient_email
        msg.attach(MIMEText(plain_body, "plain", "utf-8"))
        msg.attach(MIMEText(html_body,  "html",  "utf-8"))

        context = ssl.create_default_context()
        with smtplib.SMTP_SSL("smtp.gmail.com", 465, context=context, timeout=20) as server:
            server.login(sender_email, app_password)
            server.sendmail(sender_email, recipient_email, msg.as_string())

        return {"success": True, "message": f"Notification sent to {recipient_email}"}

    except smtplib.SMTPAuthenticationError:
        return {"success": False, "message": "Gmail auth failed. Use a 16-char App Password from myaccount.google.com/apppasswords."}
    except smtplib.SMTPException as e:
        return {"success": False, "message": f"SMTP error: {e}"}
    except Exception as e:
        return {"success": False, "message": f"Unexpected error: {e}"}


# ── Pipeline State Helpers ────────────────────────────────────────────────────

def _cand_key(candidate: dict) -> str:
    return f"{candidate.get('name', 'unknown')}_{candidate.get('upload_pos', 0)}"


def init_pipeline(candidates: list[dict], session_state) -> None:
    if "pipeline" not in session_state:
        session_state["pipeline"] = {}
    now_iso = datetime.datetime.now().isoformat()
    for cand in candidates:
        key = _cand_key(cand)
        if key not in session_state["pipeline"]:
            default_stage = "Rejected" if cand.get("filtered") else "Screened"
            session_state["pipeline"][key] = {
                "stage":        default_stage,
                "history":      [{"stage": default_stage, "ts": now_iso, "note": "Auto-initialised from screening"}],
                "notes":        [],
                "last_contact": now_iso,
                "name":         cand.get("name", "Unknown"),
                "score":        cand.get("score", 0),
                "filtered":     cand.get("filtered", False),
                "rank":         cand.get("rank"),
            }


def get_stage(key: str, session_state) -> str:
    return session_state.get("pipeline", {}).get(key, {}).get("stage", "Applied")


def set_stage(
    key: str,
    new_stage: str,
    session_state,
    note: str = "",
    gmail_app_password: str = "",
    sender_email: str = "",
    notify_email: str = "",
    school_name: str = "EduHire",
    hr_name: str = "The Hiring Team",
) -> Optional[dict]:
    """Move a candidate to a new stage. Fires Gmail notification if credentials provided."""
    pipeline = session_state.get("pipeline", {})
    if key not in pipeline:
        return None

    old_stage = pipeline[key]["stage"]
    pipeline[key]["stage"]        = new_stage
    pipeline[key]["last_contact"] = datetime.datetime.now().isoformat()
    pipeline[key]["history"].append({
        "stage": new_stage,
        "ts":    datetime.datetime.now().isoformat(),
        "note":  note or f"Moved to {new_stage}",
    })

    gmail_result = None
    if notify_email and gmail_app_password and sender_email and old_stage != new_stage:
        gmail_result = send_gmail_stage_notification(
            gmail_app_password=gmail_app_password,
            sender_email=sender_email,
            recipient_email=notify_email,
            candidate_name=pipeline[key]["name"],
            old_stage=old_stage,
            new_stage=new_stage,
            school_name=school_name,
            hr_name=hr_name,
            note=note,
        )

    return gmail_result


def add_note(key: str, text: str, author: str, session_state) -> None:
    pipeline = session_state.get("pipeline", {})
    if key not in pipeline:
        return
    pipeline[key]["notes"].append({
        "text":   text,
        "ts":     datetime.datetime.now().isoformat(),
        "author": author or "Recruiter",
    })
    pipeline[key]["last_contact"] = datetime.datetime.now().isoformat()


def update_last_contact(key: str, session_state) -> None:
    pipeline = session_state.get("pipeline", {})
    if key in pipeline:
        pipeline[key]["last_contact"] = datetime.datetime.now().isoformat()


# ── Stall & Cold Detection ────────────────────────────────────────────────────

def days_since(iso_ts: str) -> float:
    try:
        dt = datetime.datetime.fromisoformat(iso_ts)
        return (datetime.datetime.now() - dt).total_seconds() / 86400
    except Exception:
        return 0.0


def get_stalled_candidates(session_state, stall_days: int = 0) -> list[dict]:
    pipeline = session_state.get("pipeline", {})
    stalled  = []
    for key, data in pipeline.items():
        if data["stage"] in ("Offer", "Rejected"):
            continue
        age = days_since(data.get("last_contact", datetime.datetime.now().isoformat()))
        if age >= stall_days:
            stalled.append({**data, "_key": key, "_days_stalled": round(age, 1)})
    return sorted(stalled, key=lambda x: -x["_days_stalled"])


def get_cold_candidates(session_state, cold_days: int = 0) -> list[dict]:
    pipeline      = session_state.get("pipeline", {})
    cold          = []
    active_stages = {"Screened", "Interview Scheduled", "Feedback Pending"}
    for key, data in pipeline.items():
        if data["stage"] not in active_stages:
            continue
        age = days_since(data.get("last_contact", datetime.datetime.now().isoformat()))
        if age >= cold_days:
            cold.append({**data, "_key": key, "_days_cold": round(age, 1)})
    return sorted(cold, key=lambda x: -x["_days_cold"])


# ── Pipeline Summary ──────────────────────────────────────────────────────────

def get_pipeline_summary(session_state) -> dict:
    pipeline = session_state.get("pipeline", {})
    summary  = {s: 0 for s in PIPELINE_STAGES}
    for data in pipeline.values():
        stage = data.get("stage", "Applied")
        if stage in summary:
            summary[stage] += 1
    return summary
