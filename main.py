import os


import sqlite3
import uuid
import json
from datetime import datetime
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, HTMLResponse
from pydantic import BaseModel
from typing import Optional, List
import uvicorn

from diagnose import diagnose as run_diagnose

# Optional google_calendar import
try:
    from google_calendar import find_and_create_meeting
    GOOGLE_CALENDAR_AVAILABLE = True
except Exception:
    GOOGLE_CALENDAR_AVAILABLE = False
    find_and_create_meeting = None

app = FastAPI(title="Green Light API")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

BASE_DIR = os.path.dirname(__file__)
DB_PATH = os.environ.get("DATABASE_URL", os.path.join(BASE_DIR, "greenlight.db"))
PORT = int(os.environ.get("PORT", 8080))


def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    conn = get_db()
    c = conn.cursor()
    c.execute("""CREATE TABLE IF NOT EXISTS initiatives (
        id TEXT PRIMARY KEY, qc_id TEXT, title TEXT, description TEXT,
        owner_name TEXT, owner_email TEXT, team TEXT, status TEXT DEFAULT 'under_review',
        created_at TEXT, updated_at TEXT, diagnosis_json TEXT, risk_level TEXT DEFAULT 'low',
        product_area TEXT, has_partners INTEGER DEFAULT 0, financial_impact TEXT DEFAULT 'minimal',
        affects_checkout INTEGER DEFAULT 0, accounting_treatment TEXT,
        has_security_risk INTEGER DEFAULT 0, new_business_line INTEGER DEFAULT 0,
        capex_or_opex TEXT DEFAULT 'unknown')""")
    c.execute("""CREATE TABLE IF NOT EXISTS validations (
        id TEXT PRIMARY KEY, initiative_id TEXT, team_name TEXT,
        validator_name TEXT, validator_email TEXT,
        status TEXT DEFAULT 'pending', comment TEXT,
        created_at TEXT, updated_at TEXT, meeting_id TEXT)""")
    c.execute("""CREATE TABLE IF NOT EXISTS meetings (
        id TEXT PRIMARY KEY, initiative_id TEXT, validation_id TEXT,
        google_event_id TEXT, google_meet_link TEXT, scheduled_at TEXT,
        duration_minutes INTEGER DEFAULT 30,
        requester_name TEXT, requester_email TEXT, owner_name TEXT, owner_email TEXT,
        status TEXT DEFAULT 'scheduled', transcript_text TEXT, minutes_summary TEXT,
        created_at TEXT)""")
    c.execute("""CREATE TABLE IF NOT EXISTS memory_entries (
        id TEXT PRIMARY KEY, initiative_id TEXT, entry_type TEXT,
        content_json TEXT, created_at TEXT, tags TEXT)""")
    conn.commit()
    conn.close()


@app.on_event("startup")
def startup():
    init_db()


# ── Models ───────────────────────────────────────────────────────────────────

class CreateInitiative(BaseModel):
    title: str
    description: Optional[str] = ""
    owner_name: str
    owner_email: str
    team: Optional[str] = ""
    product_area: Optional[str] = ""
    has_partners: Optional[bool] = False
    financial_impact: Optional[str] = "minimal"
    affects_checkout: Optional[bool] = False
    accounting_treatment: Optional[str] = ""
    has_security_risk: Optional[bool] = False
    new_business_line: Optional[bool] = False
    capex_or_opex: Optional[str] = "unknown"
    diagnosis_json: Optional[str] = None
    risk_level: Optional[str] = "low"

class UpdateInitiative(BaseModel):
    title: Optional[str] = None
    description: Optional[str] = None
    status: Optional[str] = None
    risk_level: Optional[str] = None
    diagnosis_json: Optional[str] = None

class CreateValidations(BaseModel):
    team_names: List[str]
    validator_name: Optional[str] = ""
    validator_email: Optional[str] = ""

class UpdateValidation(BaseModel):
    status: str
    comment: Optional[str] = ""
    validator_name: Optional[str] = None
    validator_email: Optional[str] = None

class CreateMeeting(BaseModel):
    initiative_id: str
    validation_id: Optional[str] = None
    requester_name: str
    requester_email: str
    owner_name: str
    owner_email: str
    scheduled_at: Optional[str] = None
    google_meet_link: Optional[str] = None
    google_event_id: Optional[str] = None

class UpdateMeeting(BaseModel):
    status: Optional[str] = None
    transcript_text: Optional[str] = None
    minutes_summary: Optional[str] = None

class ScheduleMeeting(BaseModel):
    initiative_id: str
    validation_id: Optional[str] = None
    requester_name: str
    requester_email: str
    owner_name: str
    owner_email: str
    team_name: str
    reason: str

class AddTranscript(BaseModel):
    transcript_text: str
    minutes_summary: Optional[str] = ""

class AddMemory(BaseModel):
    initiative_id: Optional[str] = None
    entry_type: str
    content_json: str
    tags: Optional[str] = ""

class DiagnoseRequest(BaseModel):
    title: Optional[str] = ""
    description: Optional[str] = ""
    product_area: Optional[str] = ""
    team: Optional[str] = ""
    owner_name: Optional[str] = ""
    owner_email: Optional[str] = ""
    has_partners: Optional[bool] = False
    affects_checkout: Optional[bool] = False
    financial_impact: Optional[str] = "minimal"
    new_business_line: Optional[bool] = False
    has_security_risk: Optional[bool] = False
    accounting_treatment: Optional[str] = ""
    capex_or_opex: Optional[str] = "unknown"


def now_iso():
    return datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")


def next_qc_id(conn):
    cur = conn.cursor()
    cur.execute("SELECT COUNT(*) FROM initiatives")
    n = cur.fetchone()[0] + 1
    year = datetime.utcnow().year
    return f"GL-{year}-{n:04d}"


# ── Health ────────────────────────────────────────────────────────────────────

@app.get("/health")
def health():
    return {"status": "ok", "service": "Green Light API"}


# ── Stats ─────────────────────────────────────────────────────────────────────

@app.get("/api/initiatives/stats")
def stats():
    conn = get_db()
    c = conn.cursor()
    c.execute("SELECT COUNT(*) FROM initiatives WHERE status != 'archived'")
    total = c.fetchone()[0]
    c.execute("SELECT status, COUNT(*) FROM initiatives WHERE status != 'archived' GROUP BY status")
    by_status = {r[0]: r[1] for r in c.fetchall()}
    c.execute("SELECT risk_level, COUNT(*) FROM initiatives WHERE status != 'archived' GROUP BY risk_level")
    by_risk = {r[0]: r[1] for r in c.fetchall()}
    c.execute("SELECT COUNT(*) FROM validations WHERE status = 'pending'")
    pending = c.fetchone()[0]
    month = datetime.utcnow().strftime("%Y-%m")
    c.execute("SELECT COUNT(*) FROM initiatives WHERE status = 'approved' AND created_at LIKE ?", (f"{month}%",))
    approved_month = c.fetchone()[0]
    c.execute("SELECT id, qc_id, title, risk_level, diagnosis_json FROM initiatives WHERE status != 'archived'")
    alerts = []
    for row in c.fetchall():
        if row[3] and row[4]:
            try:
                d = json.loads(row[4])
                flags = d.get("authority_flags", [])
                if flags:
                    alerts.append({"id": row[0], "qc_id": row[1], "title": row[2],
                                   "risk_level": row[3], "authority_flags": flags})
            except Exception:
                pass
    conn.close()
    return {
        "total_active": total, "by_status": by_status, "by_risk": by_risk,
        "pending_validations": pending, "approved_this_month": approved_month,
        "authority_alerts": alerts
    }


# ── Initiatives ───────────────────────────────────────────────────────────────

@app.post("/api/initiatives", status_code=201)
def create_initiative(body: CreateInitiative):
    conn = get_db()
    initiative_id = str(uuid.uuid4())
    qc_id = next_qc_id(conn)
    ts = now_iso()
    conn.execute("""INSERT INTO initiatives
        (id, qc_id, title, description, owner_name, owner_email, team, status,
         created_at, updated_at, diagnosis_json, risk_level, product_area,
         has_partners, financial_impact, affects_checkout, accounting_treatment,
         has_security_risk, new_business_line, capex_or_opex)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (initiative_id, qc_id, body.title, body.description, body.owner_name,
         body.owner_email, body.team, "under_review", ts, ts,
         body.diagnosis_json, body.risk_level, body.product_area,
         int(body.has_partners), body.financial_impact, int(body.affects_checkout),
         body.accounting_treatment, int(body.has_security_risk),
         int(body.new_business_line), body.capex_or_opex))
    conn.commit()
    conn.close()
    return {"id": initiative_id, "qc_id": qc_id, "status": "under_review"}


@app.get("/api/initiatives")
def list_initiatives(status: Optional[str] = None, risk_level: Optional[str] = None,
                     team: Optional[str] = None, search: Optional[str] = None):
    conn = get_db()
    q = "SELECT * FROM initiatives WHERE 1=1"
    params = []
    if status:
        q += " AND status = ?"; params.append(status)
    if risk_level:
        q += " AND risk_level = ?"; params.append(risk_level)
    if team:
        q += " AND team = ?"; params.append(team)
    if search:
        q += " AND (title LIKE ? OR description LIKE ?)"; params += [f"%{search}%", f"%{search}%"]
    q += " ORDER BY created_at DESC"
    rows = [dict(r) for r in conn.execute(q, params).fetchall()]
    for r in rows:
        if r.get("diagnosis_json"):
            try:
                r["diagnosis"] = json.loads(r["diagnosis_json"])
            except Exception:
                pass
    conn.close()
    return rows


@app.get("/api/initiatives/{initiative_id}")
def get_initiative(initiative_id: str):
    conn = get_db()
    row = conn.execute("SELECT * FROM initiatives WHERE id = ?", (initiative_id,)).fetchone()
    conn.close()
    if not row:
        raise HTTPException(404, "Initiative not found")
    r = dict(row)
    if r.get("diagnosis_json"):
        try:
            r["diagnosis"] = json.loads(r["diagnosis_json"])
        except Exception:
            pass
    return r


@app.put("/api/initiatives/{initiative_id}")
def update_initiative(initiative_id: str, body: UpdateInitiative):
    conn = get_db()
    fields, params = [], []
    if body.title is not None: fields.append("title = ?"); params.append(body.title)
    if body.description is not None: fields.append("description = ?"); params.append(body.description)
    if body.status is not None: fields.append("status = ?"); params.append(body.status)
    if body.risk_level is not None: fields.append("risk_level = ?"); params.append(body.risk_level)
    if body.diagnosis_json is not None: fields.append("diagnosis_json = ?"); params.append(body.diagnosis_json)
    if not fields:
        conn.close()
        return {"updated": False}
    fields.append("updated_at = ?"); params.append(now_iso())
    params.append(initiative_id)
    conn.execute(f"UPDATE initiatives SET {', '.join(fields)} WHERE id = ?", params)
    conn.commit()
    conn.close()
    return {"updated": True}


# ── Validations ───────────────────────────────────────────────────────────────

@app.post("/api/initiatives/{initiative_id}/validations", status_code=201)
def create_validations(initiative_id: str, body: CreateValidations):
    conn = get_db()
    ts = now_iso()
    created = []
    for team in body.team_names:
        vid = str(uuid.uuid4())
        conn.execute("""INSERT INTO validations
            (id, initiative_id, team_name, validator_name, validator_email, status, created_at, updated_at)
            VALUES (?,?,?,?,?,?,?,?)""",
            (vid, initiative_id, team, body.validator_name, body.validator_email, "pending", ts, ts))
        created.append({"id": vid, "team_name": team})
    conn.commit()
    conn.close()
    return {"created": created}


@app.get("/api/initiatives/{initiative_id}/validations")
def list_validations(initiative_id: str):
    conn = get_db()
    rows = [dict(r) for r in conn.execute(
        "SELECT * FROM validations WHERE initiative_id = ? ORDER BY created_at", (initiative_id,)).fetchall()]
    conn.close()
    return rows


@app.put("/api/validations/{validation_id}")
def update_validation(validation_id: str, body: UpdateValidation):
    conn = get_db()
    ts = now_iso()
    fields = ["status = ?", "comment = ?", "updated_at = ?"]
    params = [body.status, body.comment, ts]
    if body.validator_name: fields.append("validator_name = ?"); params.append(body.validator_name)
    if body.validator_email: fields.append("validator_email = ?"); params.append(body.validator_email)
    params.append(validation_id)
    conn.execute(f"UPDATE validations SET {', '.join(fields)} WHERE id = ?", params)
    conn.commit()
    conn.close()
    return {"updated": True}


# ── Meetings ──────────────────────────────────────────────────────────────────

@app.post("/api/meetings", status_code=201)
def create_meeting(body: CreateMeeting):
    conn = get_db()
    mid = str(uuid.uuid4())
    ts = now_iso()
    conn.execute("""INSERT INTO meetings
        (id, initiative_id, validation_id, requester_name, requester_email,
         owner_name, owner_email, scheduled_at, google_meet_link, google_event_id,
         status, created_at)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
        (mid, body.initiative_id, body.validation_id, body.requester_name,
         body.requester_email, body.owner_name, body.owner_email,
         body.scheduled_at, body.google_meet_link, body.google_event_id,
         "scheduled", ts))
    conn.commit()
    conn.close()
    return {"id": mid}


@app.post("/api/meetings/schedule", status_code=201)
def schedule_meeting(body: ScheduleMeeting):
    if not GOOGLE_CALENDAR_AVAILABLE:
        raise HTTPException(503, {"error": "google_calendar_not_configured",
            "message": "Configurá Google Calendar en Settings > Integrations"})
    conn = get_db()
    row = conn.execute("SELECT title FROM initiatives WHERE id = ?", (body.initiative_id,)).fetchone()
    conn.close()
    if not row:
        raise HTTPException(404, "Initiative not found")
    initiative_title = row["title"]
    try:
        result = find_and_create_meeting(
            body.requester_email, body.owner_email,
            initiative_title, body.initiative_id,
            body.team_name, body.reason
        )
    except Exception as e:
        err = str(e)
        if "google_calendar_not_configured" in err:
            raise HTTPException(503, {"error": "google_calendar_not_configured",
                "message": "Configurá Google Calendar en Settings > Integrations"})
        raise HTTPException(500, {"error": err})
    conn = get_db()
    mid = str(uuid.uuid4())
    ts = now_iso()
    conn.execute("""INSERT INTO meetings
        (id, initiative_id, validation_id, requester_name, requester_email,
         owner_name, owner_email, scheduled_at, google_meet_link, google_event_id,
         duration_minutes, status, created_at)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (mid, body.initiative_id, body.validation_id, body.requester_name,
         body.requester_email, body.owner_name, body.owner_email,
         result["scheduled_at"], result["google_meet_link"], result["google_event_id"],
         30, "scheduled", ts))
    if body.validation_id:
        conn.execute("UPDATE validations SET status='needs_clarification', updated_at=? WHERE id=?",
                     (ts, body.validation_id))
    conn.commit()
    conn.close()
    return {"id": mid, **result}


@app.get("/api/initiatives/{initiative_id}/meetings")
def list_meetings(initiative_id: str):
    conn = get_db()
    rows = [dict(r) for r in conn.execute(
        "SELECT * FROM meetings WHERE initiative_id = ? ORDER BY created_at DESC", (initiative_id,)).fetchall()]
    conn.close()
    return rows


@app.put("/api/meetings/{meeting_id}")
def update_meeting(meeting_id: str, body: UpdateMeeting):
    conn = get_db()
    fields, params = [], []
    if body.status: fields.append("status = ?"); params.append(body.status)
    if body.transcript_text: fields.append("transcript_text = ?"); params.append(body.transcript_text)
    if body.minutes_summary: fields.append("minutes_summary = ?"); params.append(body.minutes_summary)
    if not fields:
        conn.close()
        return {"updated": False}
    params.append(meeting_id)
    conn.execute(f"UPDATE meetings SET {', '.join(fields)} WHERE id = ?", params)
    conn.commit()
    conn.close()
    return {"updated": True}


@app.post("/api/meetings/{meeting_id}/transcript")
def add_transcript(meeting_id: str, body: AddTranscript):
    conn = get_db()
    ts = now_iso()
    row = conn.execute("SELECT initiative_id FROM meetings WHERE id = ?", (meeting_id,)).fetchone()
    if not row:
        conn.close()
        raise HTTPException(404, "Meeting not found")
    conn.execute("UPDATE meetings SET transcript_text=?, minutes_summary=?, status='completed' WHERE id=?",
                 (body.transcript_text, body.minutes_summary, meeting_id))
    mem_id = str(uuid.uuid4())
    content = json.dumps({"meeting_id": meeting_id, "transcript": body.transcript_text,
                          "summary": body.minutes_summary})
    conn.execute("INSERT INTO memory_entries (id, initiative_id, entry_type, content_json, created_at, tags) VALUES (?,?,?,?,?,?)",
                 (mem_id, row["initiative_id"], "meeting_minute", content, ts, "meeting,transcript"))
    conn.commit()
    conn.close()
    return {"updated": True, "memory_entry_id": mem_id}


# ── Memory ─────────────────────────────────────────────────────────────────────

@app.post("/api/memory", status_code=201)
def add_memory(body: AddMemory):
    conn = get_db()
    mid = str(uuid.uuid4())
    conn.execute("INSERT INTO memory_entries (id, initiative_id, entry_type, content_json, created_at, tags) VALUES (?,?,?,?,?,?)",
                 (mid, body.initiative_id, body.entry_type, body.content_json, now_iso(), body.tags))
    conn.commit()
    conn.close()
    return {"id": mid}


@app.get("/api/memory")
def list_memory(initiative_id: Optional[str] = None, entry_type: Optional[str] = None,
                tags: Optional[str] = None):
    conn = get_db()
    q = "SELECT * FROM memory_entries WHERE 1=1"
    params = []
    if initiative_id: q += " AND initiative_id = ?"; params.append(initiative_id)
    if entry_type: q += " AND entry_type = ?"; params.append(entry_type)
    if tags: q += " AND tags LIKE ?"; params.append(f"%{tags}%")
    q += " ORDER BY created_at DESC LIMIT 100"
    rows = [dict(r) for r in conn.execute(q, params).fetchall()]
    conn.close()
    return rows


@app.get("/api/memory/patterns")
def memory_patterns():
    conn = get_db()
    top_teams = [dict(r) for r in conn.execute(
        "SELECT team_name, COUNT(*) as count FROM validations GROUP BY team_name ORDER BY count DESC LIMIT 10"
    ).fetchall()]
    risks = [dict(r) for r in conn.execute(
        "SELECT product_area, risk_level, COUNT(*) as count FROM initiatives WHERE product_area != '' GROUP BY product_area, risk_level ORDER BY count DESC LIMIT 20"
    ).fetchall()]
    recent = [dict(r) for r in conn.execute(
        "SELECT * FROM memory_entries WHERE entry_type='decision' ORDER BY created_at DESC LIMIT 10"
    ).fetchall()]
    conn.close()
    return {"top_teams_by_frequency": top_teams, "risks_by_product_area": risks, "recent_decisions": recent}


# ── Diagnose ───────────────────────────────────────────────────────────────────

@app.post("/api/diagnose")
def diagnose_endpoint(body: DiagnoseRequest):
    result = run_diagnose(body.dict(), DB_PATH)
    return result


# ── Static files: agent at /agent, dashboard at / ─────────────────────────────

FRONTEND_DIR = os.path.join(BASE_DIR, "frontend")
AGENT_DIR = os.path.join(FRONTEND_DIR, "agent")
DASHBOARD_DIR = os.path.join(FRONTEND_DIR, "dashboard")


@app.get("/agent")
@app.get("/agent/")
def serve_agent():
    return FileResponse(os.path.join(AGENT_DIR, "index.html"))


@app.get("/agent/{path:path}")
def serve_agent_path(path: str):
    full_path = os.path.join(AGENT_DIR, path)
    if os.path.isfile(full_path):
        return FileResponse(full_path)
    return FileResponse(os.path.join(AGENT_DIR, "index.html"))


# Mount dashboard static files last (catches everything else)
app.mount("/", StaticFiles(directory=DASHBOARD_DIR, html=True), name="dashboard")


if __name__ == "__main__":
    uvicorn.run("main:app", host="0.0.0.0", port=PORT, reload=False)
