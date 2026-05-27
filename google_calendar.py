import requests
import uuid
from datetime import datetime, timedelta
import pytz

TIMEZONE = "America/Argentina/Buenos_Aires"
BUSINESS_START = 9
BUSINESS_END = 18
SLOT_DURATION = 30
CALENDAR_API = "https://www.googleapis.com/calendar/v3"

def find_and_create_meeting(requester_email, owner_email, initiative_title, initiative_id, team_name, reason):
    tz = pytz.timezone(TIMEZONE)
    now = datetime.now(tz)
    time_max = now + timedelta(days=7)

    fb_body = {
        "timeMin": now.isoformat(),
        "timeMax": time_max.isoformat(),
        "timeZone": TIMEZONE,
        "items": [{"id": requester_email}, {"id": owner_email}]
    }
    resp = requests.post(f"{CALENDAR_API}/freeBusy", json=fb_body)
    if resp.status_code == 407:
        raise Exception("google_calendar_not_configured")
    if resp.status_code != 200:
        raise Exception(f"Calendar API error: {resp.status_code} {resp.text[:200]}")

    busy_data = resp.json().get("calendars", {})
    busy_slots = []
    for cal in busy_data.values():
        for b in cal.get("busy", []):
            s = datetime.fromisoformat(b["start"].replace("Z", "+00:00")).astimezone(tz)
            e = datetime.fromisoformat(b["end"].replace("Z", "+00:00")).astimezone(tz)
            busy_slots.append((s, e))

    slot_start = None
    for day_offset in range(8):
        candidate_day = (now + timedelta(days=day_offset)).date()
        weekday = datetime.combine(candidate_day, datetime.min.time()).weekday()
        if weekday >= 5:
            continue
        start_h = max(BUSINESS_START, now.hour + 1) if day_offset == 0 else BUSINESS_START
        for hour in range(start_h, BUSINESS_END):
            for minute in [0, 30]:
                slot_s = tz.localize(datetime.combine(candidate_day, datetime.min.time()).replace(hour=hour, minute=minute))
                slot_e = slot_s + timedelta(minutes=SLOT_DURATION)
                if slot_s <= now:
                    continue
                overlap = any(not (slot_e <= b[0] or slot_s >= b[1]) for b in busy_slots)
                if not overlap:
                    slot_start = slot_s
                    break
            if slot_start:
                break
        if slot_start:
            break

    if not slot_start:
        raise Exception("No free slot found in the next 7 business days")

    slot_end = slot_start + timedelta(minutes=SLOT_DURATION)

    event_body = {
        "summary": f"Quick Check: {initiative_title} — Clarificación {team_name}",
        "description": (
            f"Reunión solicitada por Quick Check\n\n"
            f"Iniciativa ID: {initiative_id}\n"
            f"Motivo: {reason}\n\n"
            f"Esta reunión debe ser grabada con transcripción activada para el registro institucional de Quick Check."
        ),
        "start": {"dateTime": slot_start.isoformat(), "timeZone": TIMEZONE},
        "end": {"dateTime": slot_end.isoformat(), "timeZone": TIMEZONE},
        "attendees": [{"email": requester_email}, {"email": owner_email}],
        "conferenceData": {
            "createRequest": {
                "requestId": str(uuid.uuid4()),
                "conferenceSolutionKey": {"type": "hangoutsMeet"}
            }
        },
        "reminders": {
            "useDefault": False,
            "overrides": [
                {"method": "email", "minutes": 60},
                {"method": "popup", "minutes": 15}
            ]
        }
    }
    create_resp = requests.post(
        f"{CALENDAR_API}/calendars/primary/events?conferenceDataVersion=1&sendUpdates=all",
        json=event_body
    )
    if create_resp.status_code not in (200, 201):
        raise Exception(f"Failed to create event: {create_resp.status_code} {create_resp.text[:200]}")

    ev = create_resp.json()
    meet_link = ""
    for ep in ev.get("conferenceData", {}).get("entryPoints", []):
        if ep.get("entryPointType") == "video":
            meet_link = ep.get("uri", "")
            break

    return {
        "google_event_id": ev.get("id"),
        "google_meet_link": meet_link,
        "scheduled_at": slot_start.isoformat()
    }
