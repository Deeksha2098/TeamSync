from __future__ import annotations

import asyncio
import json
import os
import re
import secrets
import smtplib
from datetime import datetime, timedelta, timezone
from email.message import EmailMessage
from typing import Optional, List

from fastapi import FastAPI, Depends, HTTPException, UploadFile, File, WebSocket, WebSocketDisconnect, Header
from fastapi.middleware.cors import CORSMiddleware
import base64
import hmac
import hashlib
from pydantic import BaseModel, Field
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy import create_engine, Column, Integer, String, DateTime, Text, Boolean, ForeignKey, or_, and_
from sqlalchemy.orm import declarative_base, sessionmaker, Session, relationship


class Settings(BaseSettings):
    secret_key: str = "CHANGE_THIS_SECRET_IN_PRODUCTION"
    database_url: str = "sqlite:///./teamsync.db"
    cors_origins: str = "http://localhost:5173,http://127.0.0.1:5173"
    public_app_url: str = "http://localhost:5173"
    turn_url: str = ""
    turn_username: str = ""
    turn_credential: str = ""
    smtp_host: str = ""
    smtp_port: int = 587
    smtp_username: str = ""
    smtp_password: str = ""
    smtp_from: str = ""
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")


settings = Settings()
connect_args = {"check_same_thread": False} if settings.database_url.startswith("sqlite") else {}
engine = create_engine(settings.database_url, connect_args=connect_args)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)
Base = declarative_base()
def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, 120000)
    return f"pbkdf2_sha256$120000${salt.hex()}${digest.hex()}"

def verify_password(password: str, encoded: str) -> bool:
    try:
        scheme, rounds, salt_hex, digest_hex = encoded.split("$")
        if scheme != "pbkdf2_sha256":
            return False
        digest = hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(salt_hex), int(rounds))
        return secrets.compare_digest(digest.hex(), digest_hex)
    except Exception:
        return False
ALGO = "HS256"


class Team(Base):
    __tablename__ = "teams"
    id = Column(Integer, primary_key=True)
    name = Column(String(160), nullable=False)
    invite_code = Column(String(60), unique=True, index=True, nullable=False)
    owner_id = Column(Integer, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)


class User(Base):
    __tablename__ = "users"
    id = Column(Integer, primary_key=True)
    name = Column(String(120), nullable=False)
    email = Column(String(200), unique=True, index=True, nullable=False)
    password = Column(String(255), nullable=False)
    role = Column(String(30), default="member")
    team_id = Column(Integer, ForeignKey("teams.id"), nullable=True, index=True)
    tasks = relationship("Task", back_populates="assignee", foreign_keys="Task.assignee_id")


class Meeting(Base):
    __tablename__ = "meetings"
    id = Column(Integer, primary_key=True)
    team_id = Column(Integer, nullable=True, index=True)
    title = Column(String(200), nullable=False)
    description = Column(Text, default="")
    start = Column(DateTime, nullable=False)
    end = Column(DateTime, nullable=False)
    organizer_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    participants = Column(Text, default="[]")
    status = Column(String(30), default="Scheduled")


class Task(Base):
    __tablename__ = "tasks"
    id = Column(Integer, primary_key=True)
    team_id = Column(Integer, nullable=True, index=True)
    title = Column(String(200), nullable=False)
    description = Column(Text, default="")
    priority = Column(String(20), default="Medium")
    status = Column(String(30), default="Pending")
    due_at = Column(DateTime, nullable=True)
    assignee_id = Column(Integer, ForeignKey("users.id"))
    creator_id = Column(Integer, ForeignKey("users.id"))
    assignee = relationship("User", back_populates="tasks", foreign_keys=[assignee_id])


class Reminder(Base):
    __tablename__ = "reminders"
    id = Column(Integer, primary_key=True)
    title = Column(String(200), nullable=False)
    due_at = Column(DateTime, nullable=False)
    repeat_daily = Column(Boolean, default=False)
    done = Column(Boolean, default=False)
    last_popup_key = Column(String(40), nullable=True)
    user_id = Column(Integer, ForeignKey("users.id"))


class Notification(Base):
    __tablename__ = "notifications"
    id = Column(Integer, primary_key=True)
    message = Column(String(500), nullable=False)
    kind = Column(String(40), default="info")
    read = Column(Boolean, default=False)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    user_id = Column(Integer, ForeignKey("users.id"))


class MeetingDocument(Base):
    __tablename__ = "meeting_documents"
    id = Column(Integer, primary_key=True)
    meeting_id = Column(Integer, nullable=True)
    title = Column(String(200), nullable=False)
    participants = Column(Text, default="[]")
    transcript = Column(Text, default="")
    summary = Column(Text, default="")
    key_points = Column(Text, default="[]")
    decisions = Column(Text, default="[]")
    actions = Column(Text, default="[]")
    risks_or_blockers = Column(Text, default="[]")
    created_at = Column(DateTime, default=datetime.utcnow)
    owner_id = Column(Integer, ForeignKey("users.id"), nullable=False)


Base.metadata.create_all(engine)

# Lightweight SQLite migration for databases created by earlier TeamSync builds.
try:
    with engine.begin() as conn:
        if str(settings.database_url).startswith("sqlite"):
            cols = {row[1] for row in conn.exec_driver_sql("PRAGMA table_info(meeting_documents)").fetchall()}
            if "risks_or_blockers" not in cols:
                conn.exec_driver_sql("ALTER TABLE meeting_documents ADD COLUMN risks_or_blockers TEXT DEFAULT '[]'")
except Exception:
    pass


def db():
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()

def make_token(user: User) -> str:
    header = _b64(json.dumps({"alg":"HS256","typ":"JWT"}, separators=(",",":")).encode())
    payload = _b64(json.dumps({"sub":str(user.id),"exp":int((datetime.now(timezone.utc)+timedelta(days=7)).timestamp())}, separators=(",",":")).encode())
    signing = f"{header}.{payload}".encode()
    sig = _b64(hmac.new(settings.secret_key.encode(), signing, "sha256").digest())
    return f"{header}.{payload}.{sig}"

def decode_token(token: str) -> dict:
    try:
        header, payload, signature = token.split(".")
        signing = f"{header}.{payload}".encode()
        expected = _b64(hmac.new(settings.secret_key.encode(), signing, "sha256").digest())
        if not hmac.compare_digest(signature, expected):
            raise ValueError
        raw = base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4))
        data = json.loads(raw.decode())
        if int(data.get("exp", 0)) < int(datetime.now(timezone.utc).timestamp()):
            raise ValueError
        return data
    except Exception:
        raise HTTPException(401, "Invalid or expired token")

def current_user(authorization: str = Header(default=""), session: Session = Depends(db)) -> User:
    if not authorization.startswith("Bearer "):
        raise HTTPException(401, "Authentication required")
    payload = decode_token(authorization[7:].strip())
    try:
        uid = int(payload["sub"])
    except (ValueError, KeyError):
        raise HTTPException(401, "Invalid token")
    user = session.get(User, uid)
    if not user:
        raise HTTPException(401, "User not found")
    return user


def user_payload(user: User):
    return {"id": user.id, "name": user.name, "email": user.email, "role": user.role, "team_id": user.team_id}


def team_member_ids(session: Session, team_id: Optional[int]):
    if not team_id:
        return []
    return [x.id for x in session.query(User).filter(User.team_id == team_id).all()]


class LoginIn(BaseModel):
    email: str
    password: str


class RegisterIn(BaseModel):
    name: str = Field(min_length=2, max_length=120)
    email: str
    password: str = Field(min_length=6)
    role: str = "member"


class TeamIn(BaseModel):
    name: str = Field(min_length=2, max_length=160)


class MeetingIn(BaseModel):
    title: str = Field(min_length=2, max_length=200)
    description: str = ""
    start: datetime
    end: datetime
    participants: List[int] = []


class TaskIn(BaseModel):
    title: str = Field(min_length=2, max_length=200)
    description: str = ""
    priority: str = "Medium"
    due_at: Optional[datetime] = None
    assignee_id: int


class StatusIn(BaseModel):
    status: str


class ReminderIn(BaseModel):
    title: str = Field(min_length=2, max_length=200)
    due_at: datetime
    repeat_daily: bool = False


class AiNotes(BaseModel):
    title: str = "Meeting Notes"
    text: str = ""
    participants: List[int] = []
    meeting_id: Optional[int] = None


class SendMomIn(BaseModel):
    document_id: int
    emails: List[str] = []


class ConnectionManager:
    def __init__(self):
        self.connections: dict[int, set[WebSocket]] = {}
        self.lock = asyncio.Lock()

    async def connect(self, uid: int, ws: WebSocket):
        await ws.accept()
        async with self.lock:
            self.connections.setdefault(uid, set()).add(ws)

    async def disconnect(self, uid: int, ws: WebSocket):
        async with self.lock:
            self.connections.get(uid, set()).discard(ws)

    async def send_user(self, uid: int, payload: dict):
        async with self.lock:
            sockets = list(self.connections.get(uid, set()))
        for ws in sockets:
            try:
                await ws.send_json(payload)
            except Exception:
                await self.disconnect(uid, ws)

    async def broadcast(self, uids, payload):
        await asyncio.gather(*(self.send_user(uid, payload) for uid in set(uids) if uid))


manager = ConnectionManager()
app = FastAPI(title="TeamSync API", version="2.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=[x.strip() for x in settings.cors_origins.split(",") if x.strip()],
    allow_origin_regex=r"https?://(localhost|127\.0\.0\.1)(:\d+)?$",
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


def seed():
    session = SessionLocal()
    try:
        lead = session.query(User).filter(User.email == "lead@teamsync.local").first()
        member = session.query(User).filter(User.email == "member@teamsync.local").first()
        if not lead:
            lead = User(name="Team Lead", email="lead@teamsync.local", password=hash_password("password123"), role="lead")
            session.add(lead)
            session.commit()
        if not member:
            member = User(name="Team Member", email="member@teamsync.local", password=hash_password("password123"), role="member")
            session.add(member)
            session.commit()
        team = session.query(Team).filter(Team.name == "Demo Team").first()
        if not team:
            team = Team(name="Demo Team", invite_code=secrets.token_urlsafe(10), owner_id=lead.id)
            session.add(team)
            session.commit()
        if not lead.team_id:
            lead.team_id = team.id
        if not member.team_id:
            member.team_id = team.id
        session.commit()
    finally:
        session.close()


seed()


@app.get("/api/health")
def health():
    return {"status": "ok", "service": "TeamSync", "time": datetime.now(timezone.utc).isoformat()}


@app.get("/api/public-url")
def public_url():
    """Return the current public HTTPS URL used for shareable meeting links.
    run_public.ps1 updates backend/public_url.txt when a temporary public tunnel starts.
    """
    value = (settings.public_app_url or "").strip().rstrip("/")
    marker = os.path.join(os.path.dirname(__file__), "..", "public_url.txt")
    marker = os.path.abspath(marker)
    try:
        if os.path.exists(marker):
            candidate = open(marker, "r", encoding="utf-8").read().strip().rstrip("/")
            if candidate:
                value = candidate
    except Exception:
        pass
    return {"public_url": value if value.startswith("http") else ""}


@app.post("/api/auth/register")
def register(data: RegisterIn, session: Session = Depends(db)):
    email = data.email.strip().lower()
    if session.query(User).filter(User.email == email).first():
        raise HTTPException(400, "Email already registered")
    role = "lead" if data.role.lower() == "lead" else "member"
    user = User(name=data.name.strip(), email=email, password=hash_password(data.password), role=role)
    session.add(user)
    session.commit()
    session.refresh(user)
    return {"access_token": make_token(user), "token_type": "bearer", "user": user_payload(user)}


@app.post("/api/auth/login")
def login(data: LoginIn, session: Session = Depends(db)):
    user = session.query(User).filter(User.email == data.email.strip().lower()).first()
    if not user or not verify_password(data.password, user.password):
        raise HTTPException(401, "Incorrect email or password")
    return {"access_token": make_token(user), "token_type": "bearer", "user": user_payload(user)}


@app.get("/api/me")
def me(user: User = Depends(current_user)):
    return user_payload(user)


@app.get("/api/team")
def get_team(user: User = Depends(current_user), session: Session = Depends(db)):
    if not user.team_id:
        return {"team": None, "members": [], "invite_code": None}
    team = session.get(Team, user.team_id)
    members = session.query(User).filter(User.team_id == user.team_id).order_by(User.name).all()
    return {
        "team": {"id": team.id, "name": team.name, "invite_code": team.invite_code} if team else None,
        "members": [user_payload(x) for x in members],
    }


@app.post("/api/team")
def create_team(data: TeamIn, user: User = Depends(current_user), session: Session = Depends(db)):
    if user.team_id:
        raise HTTPException(400, "You already belong to a team")
    team = Team(name=data.name.strip(), invite_code=secrets.token_urlsafe(10), owner_id=user.id)
    session.add(team)
    session.commit()
    session.refresh(team)
    user.team_id = team.id
    user.role = "lead"
    session.commit()
    return {"id": team.id, "name": team.name, "invite_code": team.invite_code}


@app.post("/api/team/regenerate-invite")
def regenerate_invite(user: User = Depends(current_user), session: Session = Depends(db)):
    if user.role != "lead" or not user.team_id:
        raise HTTPException(403, "Only the Team Lead can regenerate the invite")
    team = session.get(Team, user.team_id)
    team.invite_code = secrets.token_urlsafe(10)
    session.commit()
    return {"invite_code": team.invite_code}


@app.get("/api/team/invite/{code}")
def preview_invite(code: str, session: Session = Depends(db)):
    team = session.query(Team).filter(Team.invite_code == code).first()
    if not team:
        raise HTTPException(404, "Invite link is invalid or expired")
    return {"team_name": team.name}


@app.post("/api/team/join/{code}")
async def join_team(code: str, user: User = Depends(current_user), session: Session = Depends(db)):
    team = session.query(Team).filter(Team.invite_code == code).first()
    if not team:
        raise HTTPException(404, "Invite link is invalid or expired")
    user.team_id = team.id
    user.role = "member"
    session.commit()
    await manager.send_user(team.owner_id, {"type": "member_joined", "message": f"{user.name} joined {team.name}"})
    return {"team_name": team.name, "message": f"You joined {team.name}"}


@app.get("/api/users")
def users(user: User = Depends(current_user), session: Session = Depends(db)):
    if not user.team_id:
        return []
    return [user_payload(x) for x in session.query(User).filter(User.team_id == user.team_id).order_by(User.name).all()]


@app.get("/api/meetings")
def meetings(user: User = Depends(current_user), session: Session = Depends(db)):
    q = session.query(Meeting).order_by(Meeting.start)
    if user.team_id:
        q = q.filter(Meeting.team_id == user.team_id)
    rows = q.all()
    out = []
    for x in rows:
        participants = json.loads(x.participants or "[]")
        if user.id != x.organizer_id and user.id not in participants:
            continue
        member_rows = session.query(User).filter(User.id.in_(list(set(participants) | {x.organizer_id}))).all() if participants else [session.get(User, x.organizer_id)]
        out.append({"id": x.id, "title": x.title, "description": x.description, "start": x.start.isoformat(), "end": x.end.isoformat(), "participants": [user_payload(m) for m in member_rows if m], "organizer_id": x.organizer_id, "status": x.status})
    return out


@app.get("/api/meetings/{mid}/invite")
def meeting_invite(mid: int, session: Session = Depends(db)):
    x = session.get(Meeting, mid)
    if not x:
        raise HTTPException(404, "Meeting not found")
    people = json.loads(x.participants or "[]")
    return {
        "id": x.id, "title": x.title, "description": x.description,
        "start": x.start.isoformat(), "end": x.end.isoformat(),
        "status": x.status, "participant_count": len(set(people) | {x.organizer_id}),
        "invite_url": f"{settings.public_app_url.rstrip('/')}/meeting/join/{x.id}",
    }

@app.get("/api/meetings/{mid}")
def meeting_detail(mid: int, user: User = Depends(current_user), session: Session = Depends(db)):
    x = session.get(Meeting, mid)
    if not x:
        raise HTTPException(404, "Meeting not found")
    people = set(json.loads(x.participants or "[]")) | {x.organizer_id}
    if user.id not in people:
        raise HTTPException(403, "You are not a participant in this meeting")
    members = session.query(User).filter(User.id.in_(list(people))).order_by(User.name).all()
    return {"id": x.id, "title": x.title, "description": x.description, "start": x.start.isoformat(), "end": x.end.isoformat(), "organizer_id": x.organizer_id, "status": x.status, "participants": [user_payload(m) for m in members], "invite_path": f"/meeting/join/{x.id}", "invite_url": f"{settings.public_app_url.rstrip('/')}/meeting/join/{x.id}"}

@app.post("/api/meetings")
async def create_meeting(data: MeetingIn, user: User = Depends(current_user), session: Session = Depends(db)):
    if data.end <= data.start:
        raise HTTPException(400, "End time must be after start time")
    participant_ids = set(data.participants)
    participant_ids.add(user.id)
    if user.team_id:
        valid = {x.id for x in session.query(User).filter(User.team_id == user.team_id).all()}
        if not participant_ids.issubset(valid):
            raise HTTPException(400, "All participants must belong to your team")
        base = session.query(Meeting).filter(Meeting.team_id == user.team_id)
    else:
        base = session.query(Meeting).filter(Meeting.organizer_id == user.id)
    conflicts = base.filter(Meeting.start < data.end, Meeting.end > data.start).all()
    for existing in conflicts:
        existing_people = set(json.loads(existing.participants or "[]")) | {existing.organizer_id}
        if existing_people & participant_ids:
            raise HTTPException(409, f"Calendar conflict: {existing.title} overlaps a participant's existing meeting")
    meeting = Meeting(team_id=user.team_id, title=data.title.strip(), description=data.description.strip(), start=data.start, end=data.end, organizer_id=user.id, participants=json.dumps(sorted(participant_ids)))
    session.add(meeting)
    for uid in participant_ids:
        session.add(Notification(user_id=uid, kind="meeting", message=f"Meeting scheduled: {meeting.title} ({data.start.strftime('%d %b, %I:%M %p')})"))
    session.commit()
    session.refresh(meeting)
    await manager.broadcast(participant_ids, {"type": "meeting_created", "message": f"Meeting scheduled: {meeting.title}", "meeting_id": meeting.id})
    return {"id": meeting.id, "invite_path": f"/meeting/join/{meeting.id}", "invite_url": f"{settings.public_app_url.rstrip('/')}/meeting/join/{meeting.id}", "message": "Meeting created and calendar time blocked"}


@app.delete("/api/meetings/{mid}")
async def delete_meeting(mid: int, user: User = Depends(current_user), session: Session = Depends(db)):
    meeting = session.get(Meeting, mid)
    if not meeting:
        raise HTTPException(404, "Meeting not found")
    people = set(json.loads(meeting.participants or "[]")) | {meeting.organizer_id}
    if user.id != meeting.organizer_id and user.id not in people:
        raise HTTPException(403, "Not allowed")
    if user.id != meeting.organizer_id:
        raise HTTPException(403, "Only the organizer can cancel this meeting")
    title = meeting.title
    session.delete(meeting)
    for uid in people:
        session.add(Notification(user_id=uid, kind="meeting", message=f"Meeting cancelled: {title}"))
    session.commit()
    await manager.broadcast(people, {"type": "meeting_cancelled", "message": f"Meeting cancelled: {title}"})
    return {"ok": True}


@app.get("/api/tasks")
def tasks(user: User = Depends(current_user), session: Session = Depends(db)):
    q = session.query(Task)
    if user.team_id:
        q = q.filter(Task.team_id == user.team_id)
    if user.role != "lead":
        q = q.filter(or_(Task.assignee_id == user.id, Task.creator_id == user.id))
    rows = q.order_by(Task.due_at.is_(None), Task.due_at).all()
    return [{"id": x.id, "title": x.title, "description": x.description, "priority": x.priority, "status": x.status, "due_at": x.due_at.isoformat() if x.due_at else None, "assignee_id": x.assignee_id, "assignee": x.assignee.name if x.assignee else "", "creator_id": x.creator_id} for x in rows]


@app.post("/api/tasks")
async def create_task(data: TaskIn, user: User = Depends(current_user), session: Session = Depends(db)):
    if user.role != "lead":
        raise HTTPException(403, "Only Team Lead can assign tasks")
    assignee = session.get(User, data.assignee_id)
    if not assignee or assignee.team_id != user.team_id:
        raise HTTPException(400, "Invalid team member")
    task = Task(**data.model_dump(), creator_id=user.id, team_id=user.team_id)
    session.add(task)
    session.flush()
    session.add(Notification(user_id=assignee.id, kind="task", message=f"New task assigned: {task.title}"))
    session.add(Notification(user_id=user.id, kind="task", message=f"Task assigned to {assignee.name}: {task.title}"))
    session.commit()
    session.refresh(task)
    await manager.broadcast([assignee.id, user.id], {"type": "task_assigned", "message": f"New task assigned: {task.title}", "task_id": task.id})
    return {"id": task.id, "message": "Task assigned and notifications sent"}


@app.patch("/api/tasks/{tid}")
async def update_task(tid: int, data: StatusIn, user: User = Depends(current_user), session: Session = Depends(db)):
    task = session.get(Task, tid)
    if not task:
        raise HTTPException(404, "Task not found")
    if user.role != "lead" and task.assignee_id != user.id:
        raise HTTPException(403, "Not allowed")
    if data.status not in ["Pending", "In Progress", "Completed", "Done"]:
        raise HTTPException(400, "Invalid status")
    task.status = "Completed" if data.status == "Done" else data.status
    recipients = {user.id, task.creator_id, task.assignee_id}
    for uid in recipients:
        if uid != user.id:
            session.add(Notification(user_id=uid, kind="task", message=f"{user.name} updated '{task.title}' to {data.status}"))
    session.commit()
    await manager.broadcast(recipients, {"type": "task_updated", "message": f"{user.name} updated '{task.title}' to {data.status}", "task_id": task.id, "status": data.status})
    return {"ok": True}


@app.get("/api/reminders")
def reminders(user: User = Depends(current_user), session: Session = Depends(db)):
    rows = session.query(Reminder).filter(Reminder.user_id == user.id).order_by(Reminder.due_at).all()
    return [{"id": x.id, "title": x.title, "due_at": x.due_at.isoformat(), "repeat_daily": x.repeat_daily, "done": x.done} for x in rows]


@app.post("/api/reminders")
def add_reminder(data: ReminderIn, user: User = Depends(current_user), session: Session = Depends(db)):
    r = Reminder(**data.model_dump(), user_id=user.id)
    session.add(r)
    session.commit()
    session.refresh(r)
    return {"id": r.id, "message": "Reminder saved"}


@app.patch("/api/reminders/{rid}")
def reminder_update(rid: int, data: StatusIn, user: User = Depends(current_user), session: Session = Depends(db)):
    r = session.get(Reminder, rid)
    if not r or r.user_id != user.id:
        raise HTTPException(404, "Reminder not found")
    if data.status not in ["done", "pending"]:
        raise HTTPException(400, "Invalid reminder status")
    if data.status == "done" and r.repeat_daily:
        r.due_at = r.due_at + timedelta(days=1)
        r.done = False
        r.last_popup_key = None
    else:
        r.done = data.status == "done"
    session.commit()
    return {"ok": True}


@app.delete("/api/reminders/{rid}")
def reminder_delete(rid: int, user: User = Depends(current_user), session: Session = Depends(db)):
    r = session.get(Reminder, rid)
    if not r or r.user_id != user.id:
        raise HTTPException(404, "Reminder not found")
    session.delete(r)
    session.commit()
    return {"ok": True}


@app.get("/api/reminders/due")
def due_reminders(user: User = Depends(current_user), session: Session = Depends(db)):
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    window_end = now + timedelta(minutes=15)
    rows = session.query(Reminder).filter(Reminder.user_id == user.id, Reminder.done == False, Reminder.due_at >= now, Reminder.due_at <= window_end).all()
    due = []
    for r in rows:
        key = r.due_at.strftime("%Y-%m-%dT%H:%M")
        if r.last_popup_key != key:
            r.last_popup_key = key
            due.append({"id": r.id, "title": r.title, "due_at": r.due_at.isoformat(), "minutes_left": max(0, int((r.due_at - now).total_seconds() // 60))})
    if due:
        session.commit()
    return due


@app.get("/api/notifications")
def notifications(user: User = Depends(current_user), session: Session = Depends(db)):
    rows = session.query(Notification).filter(Notification.user_id == user.id).order_by(Notification.created_at.desc()).limit(50).all()
    return [{"id": x.id, "message": x.message, "kind": x.kind, "read": x.read, "created_at": x.created_at.isoformat()} for x in rows]


@app.patch("/api/notifications/{nid}")
def read_notification(nid: int, user: User = Depends(current_user), session: Session = Depends(db)):
    n = session.get(Notification, nid)
    if not n or n.user_id != user.id:
        raise HTTPException(404, "Notification not found")
    n.read = True
    session.commit()
    return {"ok": True}


def ai_fallback(text: str):
    """Offline meeting intelligence; no API key is required."""
    cleaned = re.sub(r"\s+", " ", (text or "").strip())
    sentences = [s.strip() for s in re.split(r"(?<=[.!?])\s+|\n+", cleaned) if s.strip()]
    actions = []
    decisions = []
    for sentence in sentences:
        low = sentence.lower()
        if any(k in low for k in ["decided", "decision", "agreed"]):
            decisions.append(sentence)
        if any(k in low for k in ["will ", "must ", "action", "todo", "task", "need to", "deadline"]):
            assignee = "Unassigned"
            match = re.search(r"(?:by|for|assigned to)\s+([A-Z][a-zA-Z]+)", sentence)
            if match:
                assignee = match.group(1)
            deadline = "Not specified"
            dmatch = re.search(r"\b(?:by|before)\s+([^,.]+)", sentence, flags=re.I)
            if dmatch:
                deadline = dmatch.group(1).strip()
            actions.append({"task": sentence, "assignee": assignee, "deadline": deadline})
    return {
        "summary": " ".join(sentences[:3]) or "No discussion text supplied.",
        "key_points": sentences[:10],
        "decisions": decisions[:6],
        "important_points": sentences[:15],
        "actions": actions[:10],
        "transcript": text.strip(),
        "ai_provider": "offline-fallback",
    }


def ai_analyze_text(text: str, participants: List[dict] | None = None):
    """Offline meeting intelligence. No API key or internet service is required."""
    cleaned = re.sub(r"\s+", " ", (text or "").strip())
    sentences = [s.strip() for s in re.split(r"(?<=[.!?])\s+|\n+", cleaned) if s.strip()]
    people = participants or []
    names = [str(p.get("name", "")).strip() for p in people if p.get("name")]
    decisions, actions, risks = [], [], []
    action_words = ["will ", "must ", "need to ", "needs to ", "assigned", "action item", "todo", "task", "complete ", "finish ", "prepare ", "send ", "review ", "follow up"]
    decision_words = ["decided", "decision", "agreed", "approved", "confirmed", "finalized", "we will"]
    risk_words = ["risk", "blocked", "blocker", "issue", "problem", "delay", "pending", "cannot", "can't", "unable"]
    for sentence in sentences:
        low = sentence.lower()
        if any(k in low for k in decision_words): decisions.append(sentence)
        if any(k in low for k in action_words):
            assignee = "Unassigned"
            for name in names:
                if name.lower() in low:
                    assignee = name; break
            deadline = "Not specified"
            dm = re.search(r"\b(?:by|before|on|due)\s+([^,.]+)", sentence, re.I)
            if dm: deadline = dm.group(1).strip()
            priority = "High" if any(k in low for k in ["urgent", "asap", "critical", "high priority"]) else "Medium"
            actions.append({"task": sentence, "assignee": assignee, "deadline": deadline, "priority": priority})
        if any(k in low for k in risk_words): risks.append(sentence)
    summary = " ".join(sentences[:4]) if sentences else "No discussion text supplied."
    return {
        "summary": summary,
        "key_points": sentences[:12],
        "decisions": decisions[:8],
        "important_points": sentences[:18],
        "actions": actions[:12],
        "risks_or_blockers": risks[:8],
        "transcript": text.strip(),
        "ai_provider": "TeamSync Local Meeting Intelligence (offline)",
    }


async def transcribe_audio_bytes(content: bytes, filename: str):
    """API-free compatibility endpoint. Audio is recorded locally; speech recognition is performed in the browser."""
    raise HTTPException(422, "This API-key-free build uses browser Live Transcription. Start recording in Chrome/Edge, then analyze the captured transcript.")


@app.post("/api/ai/analyze")
def ai_analyze(data: AiNotes, user: User = Depends(current_user), session: Session = Depends(db)):
    if not data.text.strip():
        raise HTTPException(400, "Please provide a transcript or meeting notes")
    participants = []
    if data.meeting_id:
        meeting = session.get(Meeting, data.meeting_id)
        if meeting:
            ids = set(json.loads(meeting.participants or "[]")) | {meeting.organizer_id}
            participants = [user_payload(x) for x in session.query(User).filter(User.id.in_(list(ids))).all()]
    result = ai_analyze_text(data.text, participants)
    doc = MeetingDocument(meeting_id=data.meeting_id, title=data.title.strip() or "Meeting Minutes", participants=json.dumps(data.participants), transcript=result["transcript"], summary=result["summary"], key_points=json.dumps(result.get("key_points", [])), decisions=json.dumps(result.get("decisions", [])), actions=json.dumps(result.get("actions", [])), risks_or_blockers=json.dumps(result.get("risks_or_blockers", [])), owner_id=user.id)
    session.add(doc)
    session.commit()
    session.refresh(doc)
    result["document_id"] = doc.id
    return result


@app.post("/api/ai/followups")
async def create_followups(data: AiNotes, user: User = Depends(current_user), session: Session = Depends(db)):
    if not data.meeting_id:
        raise HTTPException(400, "meeting_id is required")
    meeting = session.get(Meeting, data.meeting_id)
    if not meeting:
        raise HTTPException(404, "Meeting not found")
    people = set(json.loads(meeting.participants or "[]")) | {meeting.organizer_id}
    if user.id not in people:
        raise HTTPException(403, "Not allowed")
    members = [user_payload(x) for x in session.query(User).filter(User.id.in_(list(people))).all()]
    result = ai_analyze_text(data.text, members)
    valid_ids = {x.id for x in session.query(User).filter(User.team_id == meeting.team_id).all()} if meeting.team_id else people
    created = []
    for action in result.get("actions", []):
        assignee = None
        raw = str(action.get("assignee") or "").strip().lower()
        for uid in people:
            member = session.get(User, uid)
            if member and (member.name.lower() == raw or member.email.lower() == raw or raw in member.name.lower()):
                assignee = member
                break
        if assignee is None:
            assignee = session.get(User, user.id)
        if assignee.id not in valid_ids:
            assignee = session.get(User, user.id)
        due_at = None
        deadline = str(action.get("deadline") or "").strip()
        if deadline:
            try:
                due_at = datetime.fromisoformat(deadline.replace("Z", "+00:00")).replace(tzinfo=None)
            except Exception:
                due_at = None
        priority = str(action.get("priority") or "Medium")
        description = f"Created from AI MOM for: {meeting.title}"
        if deadline:
            description += f"\nAI-suggested deadline: {deadline}"
        task = Task(team_id=meeting.team_id, title=str(action.get("task") or "Follow-up task"), description=description, priority=priority if priority in {"Low","Medium","High","Urgent"} else "Medium", due_at=due_at, assignee_id=assignee.id, creator_id=user.id)
        session.add(task)
        session.flush()
        created.append({"id": task.id, "title": task.title, "assignee": user_payload(assignee)})
        recipient_ids = set(people) | {assignee.id}
        for uid in recipient_ids:
            session.add(Notification(user_id=uid, kind="task", message=f"AI follow-up task assigned: {task.title} → {assignee.name}"))
        await manager.broadcast(recipient_ids, {"type":"task_created", "task_id":task.id, "message":f"AI follow-up task assigned: {task.title}"})
    session.commit()
    return {"created": created, "count": len(created)}


@app.post("/api/ai/transcribe")
async def transcribe(file: UploadFile = File(...), user: User = Depends(current_user)):
    content = await file.read()
    if not content:
        raise HTTPException(400, "Empty audio file")
    raise HTTPException(422, "Use Live Transcription in Chrome/Edge for API-free transcription, then submit the transcript for local analysis.")


@app.post("/api/ai/transcribe-and-analyze")
async def transcribe_and_analyze(file: UploadFile = File(...), meeting_id: Optional[int] = None, user: User = Depends(current_user), session: Session = Depends(db)):
    content = await file.read()
    if not content:
        raise HTTPException(400, "Empty audio file")
    raise HTTPException(422, "Automatic audio-to-text conversion is not enabled in the API-key-free build. Use Live Transcription in Chrome/Edge and click Analyze transcript + generate MOM.")
    meeting = session.get(Meeting, meeting_id) if meeting_id else None
    participants = []
    title = "Meeting Minutes"
    participant_ids = []
    if meeting:
        people = set(json.loads(meeting.participants or "[]")) | {meeting.organizer_id}
        if user.id not in people:
            raise HTTPException(403, "You are not a participant in this meeting")
        participant_ids = list(people)
        participants = [user_payload(x) for x in session.query(User).filter(User.id.in_(list(people))).all()]
        title = meeting.title
    result = ai_analyze_text(text, participants)
    doc = MeetingDocument(meeting_id=meeting_id, title=title, participants=json.dumps(participant_ids), transcript=text, summary=result["summary"], key_points=json.dumps(result.get("key_points", [])), decisions=json.dumps(result.get("decisions", [])), actions=json.dumps(result.get("actions", [])), risks_or_blockers=json.dumps(result.get("risks_or_blockers", [])), owner_id=user.id)
    session.add(doc)
    session.commit()
    session.refresh(doc)
    result["document_id"] = doc.id
    result["filename"] = file.filename
    return result

@app.get("/api/ai/documents")
def ai_documents(user: User = Depends(current_user), session: Session = Depends(db)):
    rows = session.query(MeetingDocument).filter(MeetingDocument.owner_id == user.id).order_by(MeetingDocument.created_at.desc()).limit(30).all()
    return [{"id": x.id, "meeting_id": x.meeting_id, "title": x.title, "created_at": x.created_at.isoformat()} for x in rows]


@app.get("/api/ai/documents/{did}")
def ai_document(did: int, user: User = Depends(current_user), session: Session = Depends(db)):
    x = session.get(MeetingDocument, did)
    if not x or x.owner_id != user.id:
        raise HTTPException(404, "Document not found")
    return {"id": x.id, "meeting_id": x.meeting_id, "title": x.title, "participants": json.loads(x.participants or "[]"), "transcript": x.transcript, "summary": x.summary, "key_points": json.loads(x.key_points or "[]"), "decisions": json.loads(x.decisions or "[]"), "actions": json.loads(x.actions or "[]"), "risks_or_blockers": json.loads(x.risks_or_blockers or "[]"), "created_at": x.created_at.isoformat()}


def send_email(recipients: List[str], subject: str, body: str):
    if not (settings.smtp_host and settings.smtp_username and settings.smtp_password):
        return False, "SMTP is not configured. The MOM was generated successfully; configure backend/.env to send email."
    sender = settings.smtp_from or settings.smtp_username
    msg = EmailMessage()
    msg["From"] = sender
    msg["To"] = ", ".join(recipients)
    msg["Subject"] = subject
    msg.set_content(body)
    with smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=20) as smtp:
        smtp.starttls()
        smtp.login(settings.smtp_username, settings.smtp_password)
        smtp.send_message(msg)
    return True, "MOM sent successfully"


@app.post("/api/ai/send-mom")
def send_mom(data: SendMomIn, user: User = Depends(current_user), session: Session = Depends(db)):
    doc = session.get(MeetingDocument, data.document_id)
    if not doc or doc.owner_id != user.id:
        raise HTTPException(404, "Document not found")
    recipients = [e.strip() for e in data.emails if e.strip()]
    if not recipients:
        raise HTTPException(400, "Add at least one participant email")
    actions = json.loads(doc.actions or "[]")
    body = f"Meeting: {doc.title}\n\nSummary\n{doc.summary}\n\nKey Points\n" + "\n".join(f"- {x}" for x in json.loads(doc.key_points or "[]")) + "\n\nDecisions\n" + "\n".join(f"- {x}" for x in json.loads(doc.decisions or "[]")) + "\n\nFollow-up Tasks\n" + "\n".join(f"- {x.get('task')} | {x.get('assignee')} | {x.get('deadline')}" for x in actions) + "\n\nTranscript\n" + doc.transcript
    try:
        ok, message = send_email(recipients, f"Meeting Minutes – {doc.title}", body)
    except Exception as exc:
        raise HTTPException(500, f"Email could not be sent: {exc}")
    if not ok:
        return {"sent": False, "message": message}
    return {"sent": True, "message": message}


@app.get("/api/meeting-ice")
def meeting_ice(user: User = Depends(current_user)):
    servers = [{"urls": "stun:stun.l.google.com:19302"}]
    if settings.turn_url and settings.turn_username and settings.turn_credential:
        servers.append({"urls": settings.turn_url, "username": settings.turn_username, "credential": settings.turn_credential})
    return {"ice_servers": servers}


class MeetingRoomManager:
    def __init__(self):
        self.rooms: dict[int, dict[str, WebSocket]] = {}
        self.lock = asyncio.Lock()

    async def add(self, meeting_id: int, client_id: str, ws: WebSocket):
        await ws.accept()
        async with self.lock:
            room = self.rooms.setdefault(meeting_id, {})
            existing = list(room.keys())
            room[client_id] = ws
        return existing

    async def remove(self, meeting_id: int, client_id: str):
        async with self.lock:
            room = self.rooms.get(meeting_id, {})
            room.pop(client_id, None)
            if not room:
                self.rooms.pop(meeting_id, None)

    async def send(self, meeting_id: int, target: str, payload: dict):
        async with self.lock:
            ws = self.rooms.get(meeting_id, {}).get(target)
        if ws:
            try:
                await ws.send_json(payload)
            except Exception:
                await self.remove(meeting_id, target)

    async def broadcast(self, meeting_id: int, payload: dict, exclude: str | None = None):
        async with self.lock:
            sockets = [(cid, ws) for cid, ws in self.rooms.get(meeting_id, {}).items() if cid != exclude]
        for cid, ws in sockets:
            try:
                await ws.send_json(payload)
            except Exception:
                await self.remove(meeting_id, cid)

room_manager = MeetingRoomManager()

@app.websocket("/api/ws/meeting/{mid}")
async def meeting_websocket(websocket: WebSocket, mid: int):
    token_value = websocket.query_params.get("token", "")
    client_id = re.sub(r"[^a-zA-Z0-9_-]", "", websocket.query_params.get("client_id", ""))[:80]
    if not client_id:
        await websocket.close(code=1008)
        return
    try:
        uid = int(decode_token(token_value)["sub"])
    except Exception:
        await websocket.close(code=1008)
        return
    session = SessionLocal()
    try:
        meeting = session.get(Meeting, mid)
        if not meeting:
            await websocket.close(code=1008)
            return
        people = set(json.loads(meeting.participants or "[]")) | {meeting.organizer_id}
        if uid not in people:
            await websocket.close(code=1008)
            return
        existing = await room_manager.add(mid, client_id, websocket)
        await websocket.send_json({"type": "room_members", "members": existing})
        await room_manager.broadcast(mid, {"type": "peer_joined", "peer_id": client_id, "user_id": uid}, exclude=client_id)
        while True:
            raw = await websocket.receive_text()
            try:
                msg = json.loads(raw)
            except Exception:
                continue
            msg["from"] = client_id
            if msg.get("type") == "chat":
                msg["sender_name"] = msg.get("sender_name") or f"Participant {uid}"
                await room_manager.broadcast(mid, msg, exclude=client_id)
                continue
            target = msg.get("target")
            if target:
                await room_manager.send(mid, str(target), msg)
            else:
                await room_manager.broadcast(mid, msg, exclude=client_id)
    except WebSocketDisconnect:
        pass
    finally:
        await room_manager.remove(mid, client_id)
        await room_manager.broadcast(mid, {"type": "peer_left", "peer_id": client_id}, exclude=client_id)
        session.close()

@app.websocket("/api/ws")
async def websocket_endpoint(websocket: WebSocket):
    token_value = websocket.query_params.get("token", "")
    try:
        uid = int(decode_token(token_value)["sub"])
    except Exception:
        await websocket.close(code=1008)
        return
    await manager.connect(uid, websocket)
    try:
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        await manager.disconnect(uid, websocket)
    except Exception:
        await manager.disconnect(uid, websocket)
