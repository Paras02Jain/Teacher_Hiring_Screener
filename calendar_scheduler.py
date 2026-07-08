"""
calendar_scheduler.py — EduHire Google Calendar Integration
=============================================================
Auto-schedules interview slots for shortlisted candidates on Google Calendar.

BUG FIX (v2):
  Root cause of wrong times: generate_slots() was producing NAIVE datetimes
  (no tzinfo). Calling .isoformat() on a naive datetime gives e.g.
  "2026-04-29T09:00:00" with NO UTC offset, so Google Calendar treated it as
  UTC and displayed 09:00 UTC → 03:30 IST (visible as 3:30 AM on the calendar).
  Fix: all slot datetimes are now timezone-AWARE in the user's chosen timezone
  (default Asia/Kolkata = IST = UTC+05:30). .isoformat() now produces
  "2026-04-29T09:00:00+05:30" which Google Calendar interprets correctly.

Features:
  - OAuth2 flow via credentials.json (desktop flow) with saved token
  - Auto-detects free/busy slots using Calendar API freebusy query
  - Creates calendar events with Google Meet link (conferencing)
  - Sends email invitations directly from Calendar
  - Supports bulk scheduling of all shortlisted candidates
  - Returns event links for display in the UI

Dependencies (add to requirements.txt):
  google-auth>=2.28.0
  google-auth-oauthlib>=1.2.0
  google-api-python-client>=2.120.0
  (zoneinfo is stdlib Python 3.9+; no extra install needed)

Usage:
  scheduler = CalendarScheduler(credentials_json_path="credentials.json")
  results   = scheduler.schedule_interviews(candidates, config)
"""

from __future__ import annotations

import datetime
import json
import os
import pickle
from typing import Any

# -- zoneinfo (stdlib Python 3.9+) -------------------------------------------
try:
    from zoneinfo import ZoneInfo
    _ZONEINFO_AVAILABLE = True
except ImportError:
    try:
        from backports.zoneinfo import ZoneInfo   # pip install backports.zoneinfo
        _ZONEINFO_AVAILABLE = True
    except ImportError:
        _ZONEINFO_AVAILABLE = False
        ZoneInfo = None  # type: ignore

# -- Google API imports (graceful fallback if not installed) ------------------
try:
    from google.oauth2.credentials import Credentials
    from google.auth.transport.requests import Request
    from google_auth_oauthlib.flow import InstalledAppFlow
    from googleapiclient.discovery import build
    from googleapiclient.errors import HttpError
    _GOOGLE_AVAILABLE = True
except ImportError:
    _GOOGLE_AVAILABLE = False

SCOPES = [
    "https://www.googleapis.com/auth/calendar",
    "https://www.googleapis.com/auth/calendar.events",
]

TOKEN_FILE   = "calendar_token.pkl"
TIMEZONE_IST = "Asia/Kolkata"


# =============================================================================
# INTERNAL HELPERS
# =============================================================================

def _get_tz(tz_name: str):
    """
    Return a timezone object for the given IANA name.
    Uses zoneinfo (stdlib 3.9+). Falls back to a fixed UTC+5:30 offset.
    """
    if _ZONEINFO_AVAILABLE and ZoneInfo is not None:
        try:
            return ZoneInfo(tz_name)
        except Exception:
            pass
    # Fallback: fixed IST offset — correct for Asia/Kolkata
    return datetime.timezone(datetime.timedelta(hours=5, minutes=30))


def _localize(dt: datetime.datetime, tz_name: str) -> datetime.datetime:
    """
    Attach timezone info to a naive datetime (makes it timezone-aware).
    If dt is already aware, return it unchanged.

    This is the KEY FIX for the UTC bug: naive datetimes have no UTC offset,
    so Google Calendar treats them as UTC. Attaching the local tz produces
    an isoformat() like "2026-04-29T09:00:00+05:30" which is unambiguous.
    """
    if dt.tzinfo is not None:
        return dt
    return dt.replace(tzinfo=_get_tz(tz_name))


def _duration_label(minutes: int) -> str:
    if minutes < 60:
        return f"{minutes} min"
    h, m = divmod(minutes, 60)
    return f"{h}h {m}min" if m else f"{h}h"


# =============================================================================
# FREE/BUSY CHECKER
# =============================================================================

def check_free_slots(
    service,
    calendar_id: str,
    slots: list[tuple[datetime.datetime, datetime.datetime]],
) -> list[tuple[datetime.datetime, datetime.datetime]]:
    """
    Given a list of timezone-AWARE (start, end) datetime pairs, return only
    those slots that are FREE on the given calendar.
    Uses the Calendar freebusy API (single batch call).
    """
    if not slots:
        return []

    time_min = min(s[0] for s in slots)
    time_max = max(s[1] for s in slots)

    body = {
        "timeMin": time_min.isoformat(),
        "timeMax": time_max.isoformat(),
        "timeZone": TIMEZONE_IST,
        "items": [{"id": calendar_id}],
    }

    try:
        result       = service.freebusy().query(body=body).execute()
        busy_periods = result.get("calendars", {}).get(calendar_id, {}).get("busy", [])
    except Exception as e:
        print(f"[WARN] freebusy query failed: {e}. Treating all slots as free.")
        return slots

    def _is_busy(start: datetime.datetime, end: datetime.datetime) -> bool:
        for b in busy_periods:
            # Google returns busy times in UTC (RFC3339 with Z suffix)
            b_start = datetime.datetime.fromisoformat(b["start"].replace("Z", "+00:00"))
            b_end   = datetime.datetime.fromisoformat(b["end"].replace("Z", "+00:00"))
            # Both start/end are now timezone-aware; Python compares correctly across zones
            if start < b_end and end > b_start:
                return True
        return False

    return [s for s in slots if not _is_busy(s[0], s[1])]


# =============================================================================
# SLOT GENERATOR  (THE CORE FIX IS HERE)
# =============================================================================

def generate_slots(
    start_date: datetime.date,
    num_slots: int,
    duration_minutes: int = 45,
    working_hours: tuple[int, int] = (9, 17),
    skip_weekends: bool = True,
    gap_minutes: int = 15,
    timezone: str = TIMEZONE_IST,
) -> list[tuple[datetime.datetime, datetime.datetime]]:
    """
    Generate `num_slots` TIMEZONE-AWARE interview time slots starting from
    `start_date`, respecting working hours and weekends.

    THE FIX: datetime.combine(..., tzinfo=tz) creates aware datetimes from
    the start. Previously this used naive datetimes which caused Google
    Calendar to misinterpret the times as UTC instead of IST.

    Returns list of (start_dt, end_dt) timezone-aware datetime pairs.
    """
    tz         = _get_tz(timezone)
    slots      = []

    # Create a TIMEZONE-AWARE start datetime (this is the fix)
    current = datetime.datetime.combine(
        start_date,
        datetime.time(working_hours[0], 0),
        tzinfo=tz,            # <-- KEY: attach timezone here
    )

    slot_delta = datetime.timedelta(minutes=duration_minutes + gap_minutes)
    day_end_h  = working_hours[1]

    while len(slots) < num_slots:
        # Skip weekends
        if skip_weekends and current.weekday() >= 5:   # Sat=5, Sun=6
            next_day = current.date() + datetime.timedelta(days=1)
            current  = datetime.datetime.combine(
                next_day,
                datetime.time(working_hours[0], 0),
                tzinfo=tz,    # <-- KEY: keep timezone-aware when advancing days
            )
            continue

        end_dt = current + datetime.timedelta(minutes=duration_minutes)

        # Check if slot would exceed working hours
        if end_dt.hour > day_end_h or (end_dt.hour == day_end_h and end_dt.minute > 0):
            next_day = current.date() + datetime.timedelta(days=1)
            current  = datetime.datetime.combine(
                next_day,
                datetime.time(working_hours[0], 0),
                tzinfo=tz,    # <-- KEY: keep timezone-aware
            )
            continue

        slots.append((current, end_dt))
        current = current + slot_delta

    return slots


# =============================================================================
# CALENDAR EVENT CREATOR
# =============================================================================

def create_interview_event(
    service,
    calendar_id: str,
    candidate_name: str,
    candidate_email: str | None,
    interviewer_name: str,
    interviewer_email: str,
    start_dt: datetime.datetime,
    end_dt: datetime.datetime,
    job_title: str = "Teacher Position",
    school_name: str = "Our School",
    add_meet: bool = True,
    timezone: str = TIMEZONE_IST,
    notes: str = "",
) -> dict:
    """
    Create a Google Calendar event for one interview slot.

    start_dt / end_dt should be timezone-aware datetimes (from generate_slots).
    If naive datetimes are passed, they are localised here as a safety net.

    .isoformat() on an aware datetime now produces:
        "2026-04-29T09:00:00+05:30"   ← correct: Google sees IST, shows 9 AM
    Previously (naive):
        "2026-04-29T09:00:00"          ← wrong:  Google assumed UTC, showed 2:30 AM IST
    """
    # Safety net: localise naive datetimes before calling .isoformat()
    start_dt = _localize(start_dt, timezone)
    end_dt   = _localize(end_dt,   timezone)

    description_parts = [
        f"Interview for: {job_title}",
        f"School: {school_name}",
        f"Candidate: {candidate_name}",
        f"Interviewer: {interviewer_name}",
        "",
        "Please review the candidate's profile before the meeting.",
    ]
    if notes:
        description_parts += ["", f"Notes: {notes}"]

    attendees = [{"email": interviewer_email, "displayName": interviewer_name}]
    if candidate_email:
        attendees.append({"email": candidate_email, "displayName": candidate_name})

    event_body: dict[str, Any] = {
        "summary": f"Interview: {candidate_name} — {job_title}",
        "description": "\n".join(description_parts),
        "start": {
            "dateTime": start_dt.isoformat(),   # e.g. "2026-04-29T09:00:00+05:30"
            "timeZone": timezone,
        },
        "end": {
            "dateTime": end_dt.isoformat(),
            "timeZone": timezone,
        },
        "attendees": attendees,
        "reminders": {
            "useDefault": False,
            "overrides": [
                {"method": "email",  "minutes": 24 * 60},
                {"method": "popup",  "minutes": 30},
            ],
        },
        "status": "confirmed",
    }

    if add_meet:
        event_body["conferenceData"] = {
            "createRequest": {
                "requestId": (
                    f"eduhire-{candidate_name.replace(' ', '-').lower()}"
                    f"-{start_dt.strftime('%Y%m%d%H%M')}"
                ),
                "conferenceSolutionKey": {"type": "hangoutsMeet"},
            }
        }

    try:
        created = service.events().insert(
            calendarId=calendar_id,
            body=event_body,
            conferenceDataVersion=1 if add_meet else 0,
            sendUpdates="all" if candidate_email else "externalOnly",
        ).execute()
        return {"success": True, "event": created}
    except HttpError as e:
        error_body = json.loads(e.content.decode()) if e.content else {}
        msg = error_body.get("error", {}).get("message", str(e))
        return {"success": False, "error": msg}
    except Exception as e:
        return {"success": False, "error": str(e)}


# =============================================================================
# MAIN SCHEDULER CLASS
# =============================================================================

class CalendarScheduler:
    """
    High-level interface for scheduling candidate interviews on Google Calendar.
    Instantiate once per Streamlit session, then call schedule_interviews().
    """

    def __init__(
        self,
        credentials_json_path: str = "credentials.json",
        calendar_id: str = "primary",
    ):
        self.credentials_json_path = credentials_json_path
        self.calendar_id           = calendar_id
        self._service              = None
        self._creds                = None
        self.error                 = None if _GOOGLE_AVAILABLE else (
            "Google API libraries not installed. "
            "Run: pip install google-auth google-auth-oauthlib google-api-python-client"
        )

    # -- Authentication -------------------------------------------------------

    def authenticate(self) -> bool:
        """
        Authenticate using saved token or OAuth2 browser flow.
        Returns True if successful.
        """
        if not _GOOGLE_AVAILABLE:
            return False

        creds = None

        if os.path.exists(TOKEN_FILE):
            try:
                with open(TOKEN_FILE, "rb") as f:
                    creds = pickle.load(f)
            except Exception:
                creds = None

        if creds and creds.expired and creds.refresh_token:
            try:
                creds.refresh(Request())
            except Exception:
                creds = None

        if not creds or not creds.valid:
            if not os.path.exists(self.credentials_json_path):
                self.error = (
                    f"credentials.json not found at '{self.credentials_json_path}'. "
                    "Download it from Google Cloud Console → APIs & Services → Credentials."
                )
                return False
            try:
                flow  = InstalledAppFlow.from_client_secrets_file(
                    self.credentials_json_path, SCOPES
                )
                creds = flow.run_local_server(port=0)
            except Exception as e:
                self.error = f"OAuth2 authentication failed: {e}"
                return False

            try:
                with open(TOKEN_FILE, "wb") as f:
                    pickle.dump(creds, f)
            except Exception:
                pass

        try:
            self._service = build("calendar", "v3", credentials=creds)
            self._creds   = creds
            self.error    = None
            return True
        except Exception as e:
            self.error = f"Could not build Calendar service: {e}"
            return False

    @property
    def is_authenticated(self) -> bool:
        return self._service is not None and self.error is None

    def get_calendar_list(self) -> list[dict]:
        if not self.is_authenticated:
            return []
        try:
            result = self._service.calendarList().list().execute()
            return result.get("items", [])
        except Exception:
            return []

    # -- Bulk scheduler -------------------------------------------------------

    def schedule_interviews(
        self,
        candidates: list[dict],
        config: dict,
    ) -> list[dict]:
        """
        Schedule interview slots for all shortlisted candidates.

        config keys:
          start_date, duration_minutes, working_hours, skip_weekends,
          gap_minutes, interviewer_name, interviewer_email, school_name,
          job_title, add_meet, check_availability, timezone

        Returns list of result dicts, one per candidate.
        """
        if not self.is_authenticated:
            return [{"name": c.get("name", "?"), "success": False,
                     "error": self.error or "Not authenticated"} for c in candidates]

        start_date        = config.get("start_date",        datetime.date.today() + datetime.timedelta(days=1))
        duration_minutes  = config.get("duration_minutes",  45)
        working_hours     = config.get("working_hours",     (9, 17))
        skip_weekends     = config.get("skip_weekends",     True)
        gap_minutes       = config.get("gap_minutes",       15)
        interviewer_name  = config.get("interviewer_name",  "Hiring Manager")
        interviewer_email = config.get("interviewer_email", "")
        school_name       = config.get("school_name",       "Our School")
        job_title         = config.get("job_title",         "Teacher Position")
        add_meet          = config.get("add_meet",          True)
        check_avail       = config.get("check_availability",True)
        timezone          = config.get("timezone",          TIMEZONE_IST)

        n_candidates = len(candidates)
        buffer_size  = max(n_candidates * 2, n_candidates + 5)

        raw_slots = generate_slots(
            start_date       = start_date,
            num_slots        = buffer_size,
            duration_minutes = duration_minutes,
            working_hours    = working_hours,
            skip_weekends    = skip_weekends,
            gap_minutes      = gap_minutes,
            timezone         = timezone,      # <-- timezone passed through
        )

        free_slots = (
            check_free_slots(self._service, self.calendar_id, raw_slots)
            if check_avail else raw_slots
        )

        if len(free_slots) < n_candidates:
            free_slots = raw_slots

        free_slots = free_slots[:n_candidates]

        results = []

        for i, candidate in enumerate(candidates):
            name  = candidate.get("name",  f"Candidate {i+1}")
            email = candidate.get("email", "")

            if i >= len(free_slots):
                results.append({"name": name, "email": email,
                                 "success": False, "error": "No available slot found"})
                continue

            slot_start, slot_end = free_slots[i]

            result = create_interview_event(
                service           = self._service,
                calendar_id       = self.calendar_id,
                candidate_name    = name,
                candidate_email   = email or None,
                interviewer_name  = interviewer_name,
                interviewer_email = interviewer_email,
                start_dt          = slot_start,
                end_dt            = slot_end,
                job_title         = job_title,
                school_name       = school_name,
                add_meet          = add_meet,
                timezone          = timezone,
            )

            if result["success"]:
                ev        = result["event"]
                meet_link = ""
                for ep in ev.get("conferenceData", {}).get("entryPoints", []):
                    if ep.get("entryPointType") == "video":
                        meet_link = ep.get("uri", "")
                        break

                results.append({
                    "name":       name,
                    "email":      email,
                    "start":      slot_start,
                    "end":        slot_end,
                    "event_link": ev.get("htmlLink", ""),
                    "meet_link":  meet_link,
                    "event_id":   ev.get("id", ""),
                    "success":    True,
                    "error":      "",
                })
            else:
                results.append({
                    "name":    name,
                    "email":   email,
                    "start":   slot_start,
                    "end":     slot_end,
                    "success": False,
                    "error":   result.get("error", "Unknown error"),
                })

        return results

    # -- Single-candidate convenience -----------------------------------------

    def schedule_one(
        self,
        candidate_name: str,
        candidate_email: str,
        slot_start: datetime.datetime,
        slot_end: datetime.datetime,
        config: dict,
    ) -> dict:
        """Schedule a single candidate at a manually chosen slot."""
        if not self.is_authenticated:
            return {"success": False, "error": self.error or "Not authenticated"}

        tz = config.get("timezone", TIMEZONE_IST)
        return create_interview_event(
            service           = self._service,
            calendar_id       = self.calendar_id,
            candidate_name    = candidate_name,
            candidate_email   = candidate_email or None,
            interviewer_name  = config.get("interviewer_name",  "Hiring Manager"),
            interviewer_email = config.get("interviewer_email", ""),
            start_dt          = _localize(slot_start, tz),
            end_dt            = _localize(slot_end,   tz),
            job_title         = config.get("job_title",  "Teacher Position"),
            school_name       = config.get("school_name","Our School"),
            add_meet          = config.get("add_meet",   True),
            timezone          = tz,
        )


# =============================================================================
# AVAILABILITY PREVIEW (no events created)
# =============================================================================

def preview_slots(
    service,
    calendar_id: str,
    config: dict,
    num_candidates: int,
) -> list[dict]:
    """
    Preview available slots without creating events.
    Returns list of {start, end, available} dicts (datetimes timezone-aware).
    """
    tz = config.get("timezone", TIMEZONE_IST)
    raw_slots  = generate_slots(
        start_date       = config.get("start_date",       datetime.date.today() + datetime.timedelta(days=1)),
        num_slots        = num_candidates * 2,
        duration_minutes = config.get("duration_minutes", 45),
        working_hours    = config.get("working_hours",    (9, 17)),
        skip_weekends    = config.get("skip_weekends",    True),
        gap_minutes      = config.get("gap_minutes",      15),
        timezone         = tz,
    )
    free_slots = check_free_slots(service, calendar_id, raw_slots)
    free_set   = set((s, e) for s, e in free_slots)

    return [
        {"start": s, "end": e, "available": (s, e) in free_set}
        for s, e in raw_slots[:num_candidates * 2]
    ]
