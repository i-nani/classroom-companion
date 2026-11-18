import contextlib
import hashlib
import json
import logging
import os
from pathlib import Path
import secrets
import sqlite3
import time
from datetime import datetime
from zoneinfo import ZoneInfo

LOG = logging.getLogger("classroom")
SCHEMA = """
CREATE TABLE IF NOT EXISTS schools(id INTEGER PRIMARY KEY, name TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS people(id INTEGER PRIMARY KEY, school INTEGER NOT NULL REFERENCES schools,
 name TEXT NOT NULL, staff INTEGER NOT NULL DEFAULT 0, coordinator INTEGER NOT NULL DEFAULT 0,
 tg INTEGER UNIQUE, login_hash TEXT NOT NULL UNIQUE);
CREATE TABLE IF NOT EXISTS classes(id INTEGER PRIMARY KEY, school INTEGER NOT NULL REFERENCES schools,
 name TEXT NOT NULL, tz TEXT NOT NULL, UNIQUE(school,name));
CREATE TABLE IF NOT EXISTS members(class_id INTEGER NOT NULL REFERENCES classes, person INTEGER NOT NULL REFERENCES people,
 teaching INTEGER NOT NULL, PRIMARY KEY(class_id,person));
CREATE TABLE IF NOT EXISTS invites(hash TEXT PRIMARY KEY, person INTEGER NOT NULL REFERENCES people,
 class_id INTEGER NOT NULL REFERENCES classes, expires INTEGER NOT NULL, used INTEGER NOT NULL DEFAULT 0);
CREATE TABLE IF NOT EXISTS focus(person INTEGER PRIMARY KEY REFERENCES people, class_id INTEGER REFERENCES classes);
CREATE TABLE IF NOT EXISTS assignments(id INTEGER PRIMARY KEY, class_id INTEGER NOT NULL REFERENCES classes,
 creator INTEGER NOT NULL REFERENCES people, title TEXT NOT NULL, instructions TEXT NOT NULL, due INTEGER NOT NULL,
 stage TEXT NOT NULL DEFAULT 'draft', version INTEGER NOT NULL DEFAULT 1);
CREATE TABLE IF NOT EXISTS targets(assignment INTEGER NOT NULL REFERENCES assignments, student INTEGER NOT NULL REFERENCES people,
 status TEXT NOT NULL DEFAULT 'assigned', note TEXT NOT NULL DEFAULT '', touched INTEGER NOT NULL,
 PRIMARY KEY(assignment,student));
CREATE TABLE IF NOT EXISTS submissions(id INTEGER PRIMARY KEY, assignment INTEGER NOT NULL, student INTEGER NOT NULL,
 body TEXT NOT NULL, at INTEGER NOT NULL, FOREIGN KEY(assignment,student) REFERENCES targets);
CREATE TABLE IF NOT EXISTS feedback(id INTEGER PRIMARY KEY, submission INTEGER NOT NULL REFERENCES submissions,
 teacher INTEGER NOT NULL REFERENCES people, body TEXT NOT NULL, decision TEXT NOT NULL, at INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS updates(id INTEGER PRIMARY KEY);
CREATE TABLE IF NOT EXISTS settings(key TEXT PRIMARY KEY,value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS events(id INTEGER PRIMARY KEY, class_id INTEGER REFERENCES classes, assignment INTEGER REFERENCES assignments,
 actor INTEGER REFERENCES people, kind TEXT NOT NULL, at INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS outbox(id INTEGER PRIMARY KEY, chat INTEGER NOT NULL, body TEXT NOT NULL,
 uniq TEXT UNIQUE, assignment INTEGER REFERENCES assignments, version INTEGER, student INTEGER REFERENCES people,
 expected_status TEXT, expected_touch INTEGER, kind TEXT NOT NULL, state TEXT NOT NULL DEFAULT 'pending', tries INTEGER DEFAULT 0, retry_at INTEGER DEFAULT 0);
CREATE TABLE IF NOT EXISTS sessions(hash TEXT PRIMARY KEY, person INTEGER NOT NULL REFERENCES people, expires INTEGER NOT NULL, csrf TEXT NOT NULL);
"""
def load_env():
    if Path(".env").is_file():
        for line in Path(".env").read_text().splitlines():
            if line.strip() and not line.lstrip().startswith("#") and "=" in line:
                key, value = line.split("=", 1)
                os.environ.setdefault(key.strip(), value.strip().strip("\"'"))

def connect(path=None):
    path = Path(path or os.getenv("CLASSROOM_DB", ".state/classroom.sqlite3"))
    path.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(path, timeout=30, isolation_level=None)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA foreign_keys=ON")
    db.execute("PRAGMA journal_mode=WAL")
    db.executescript(SCHEMA)
    return db

@contextlib.contextmanager
def transaction(db):
    db.execute("BEGIN IMMEDIATE")
    try:
        yield
        db.commit()
    except BaseException:
        db.rollback()
        raise

def now():
    clock = os.getenv("DEMO_CLOCK_FILE")
    if clock and Path(clock).is_file():
        return int(Path(clock).read_text().strip())
    return int(time.time())

def local(ts, zone):
    return datetime.fromtimestamp(ts, ZoneInfo(zone)).strftime("%d %b %Y, %H:%M %Z")

def digest(value):
    return hashlib.sha256(value.encode()).hexdigest()

def event(db, cls, assignment, actor, kind, at):
    db.execute("INSERT INTO events(class_id,assignment,actor,kind,at) VALUES(?,?,?,?,?)",
               (cls,assignment,actor,kind,at))
    LOG.info(json.dumps(dict(event=kind, class_id=cls, assignment=assignment, actor=actor)))

def person(db, school, name, staff=False, coordinator=False):
    key = secrets.token_urlsafe(24)
    pid = db.execute("INSERT INTO people(school,name,staff,coordinator,login_hash) VALUES(?,?,?,?,?)",
                     (school,name,staff,coordinator,digest(key))).lastrowid
    return pid, key

def membership(db, pid, cls):
    return db.execute("""SELECT c.*,m.teaching FROM members m JOIN classes c ON c.id=m.class_id
        JOIN people p ON p.id=m.person AND p.school=c.school WHERE c.id=? AND p.id=?""",(cls,pid)).fetchone()

def add_class(db, pid, name):
    user = db.execute("SELECT * FROM people WHERE id=?",(pid,)).fetchone()
    if not user or not (user["staff"] or user["coordinator"]):
        raise ValueError("Only staff can create a class.")
    zone = os.getenv("CLASS_TIMEZONE","Asia/Kolkata")
    ZoneInfo(zone)
    if db.execute("SELECT 1 FROM classes WHERE school=? AND name=?",(user["school"],name)).fetchone():
        raise ValueError("A class with that name already exists.")
    cls = db.execute("INSERT INTO classes(school,name,tz) VALUES(?,?,?)",(user["school"],name,zone)).lastrowid
    db.execute("INSERT INTO members VALUES(?,?,1)",(cls,pid))
    return cls

def invite(db, pid, cls, at):
    if not membership(db,pid,cls):
        raise ValueError("This account is outside the class.")
    key = secrets.token_urlsafe(18)
    db.execute("UPDATE invites SET used=1 WHERE person=? AND class_id=?",(pid,cls))
    db.execute("INSERT INTO invites VALUES(?,?,?,?,0)",(digest(key),pid,cls,at+86400))
    return key

def seed(db, at):
    if db.execute("SELECT 1 FROM schools").fetchone():
        raise ValueError("Database already set up. Use a fresh path to start another demo.")
    school = db.execute("INSERT INTO schools(name) VALUES('Riverbend School')").lastrowid
    teacher, login = person(db,school,"Mira Shah",True,True)
    cls = add_class(db,teacher,"8A · Mathematics")
    accounts = {"Mira":{"id":teacher,"login":login,"invite":invite(db,teacher,cls,at)}}
    for name in ("Asha","Ravi"):
        pid, login = person(db,school,name)
        db.execute("INSERT INTO members VALUES(?,?,0)",(cls,pid))
        accounts[name] = dict(id=pid,login=login,invite=invite(db,pid,cls,at))
    return accounts

