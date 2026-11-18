"""Persistent reminder decisions and a retriable Telegram delivery queue."""
from datetime import datetime
import json
import logging
import os
import time
from urllib import request, error
from zoneinfo import ZoneInfo

from . import model as lm
from .store import event, local, now, transaction
from .workflow import enqueue, ingest

def awake(at, zone):
    return 8 <= datetime.fromtimestamp(at, ZoneInfo(zone)).hour < 20

def process_reminders(db, model, at, teacher=None):
    count = 0
    with transaction(db):
        rows = db.execute("""SELECT a.*, c.tz,t.student,t.status,t.touched,t.note,p.name,p.tg
            FROM targets t JOIN assignments a ON a.id=t.assignment JOIN classes c ON c.id=a.class_id
            JOIN people p ON p.id=t.student WHERE a.stage='assigned'
            AND t.status NOT IN ('completed','submitted') AND (? IS NULL OR EXISTS
            (SELECT 1 FROM members m WHERE m.class_id=c.id AND m.teaching=1 AND m.person=?))
            ORDER BY a.id,t.student""",(teacher,teacher)).fetchall()
        for r in rows:
            if not awake(at,r["tz"]):
                continue
            blocked = r["status"]=="blocked"
            if not blocked and (r["tg"] is None or r["due"]-at>48*3600 or at-r["touched"]<3600):
                continue
            day = datetime.fromtimestamp(at,ZoneInfo(r["tz"])).date()
            key = f"rem:{r['id']}:{r['student']}:{day}:{'help' if blocked else 'check'}"
            if db.execute("SELECT 1 FROM outbox WHERE uniq=?",(key,)).fetchone():
                continue
            chat = r["tg"]
            if blocked:
                notify = db.execute("""SELECT p.tg FROM people p JOIN members m ON m.person=p.id
                    WHERE m.class_id=? AND m.teaching=1 AND p.tg IS NOT NULL
                    ORDER BY (p.id=?) DESC,p.id LIMIT 1""",(r["class_id"],r["creator"])).fetchone()
                if not notify:
                    continue
                chat = notify[0]
            facts = dict(title=r["title"],status=r["status"],deadline=local(r["due"],r["tz"]),
                         overdue=r["due"]<at,student=r["name"],note=r["note"])
            try:
                if blocked:
                    facts["audience"]="teacher; ask the teacher to help this blocked learner"
                words = lm.field(lm.ask(model,"reminder",facts),"text",600)
            except (lm.ModelError, ValueError):
                event(db,r["class_id"],r["id"],None,"model failure",at)
                words = "Please help this learner with their blocker." if blocked else "How is your work going? Reply with progress or ask for help."
            detail = f"{r['name']} needs help · {r['note']}" if blocked else f"Due {facts['deadline']}"
            enqueue(db,chat,f"#{r['id']} {r['title']}\n{detail}\n{words}",kind="reminder",key=key,
                    aid=r["id"],ver=r["version"],student=r["student"],status=r["status"],touched=r["touched"])
            count+=1
    return count

class DeliveryError(Exception):
    def __init__(self,code=0,delay=0):
        self.code,self.delay = code,delay

class Telegram:
    def __init__(self):
        self.token = os.getenv("TELEGRAM_BOT_TOKEN")
        if not self.token:
            raise ValueError("Set TELEGRAM_BOT_TOKEN in .env first.")
    def call(self,method,data):
        req = request.Request("https://api.telegram.org/bot"+self.token+"/"+method,
                              data=json.dumps(data).encode(),headers={"Content-Type":"application/json"})
        try:
            with request.urlopen(req,timeout=20) as res:
                payload = json.load(res)
        except error.HTTPError as exc:
            try:
                seconds = json.load(exc).get("parameters",{}).get("retry_after",0)
            except (ValueError,TypeError):
                seconds = 0
            raise DeliveryError(exc.code,seconds) from None
        except (OSError,ValueError):
            raise DeliveryError() from None
        if not payload.get("ok"):
            raise DeliveryError(payload.get("error_code",0))
        return payload["result"]
    def send(self,chat,text):
        self.call("sendMessage",{"chat_id":chat,"text":text})

def drain(db,transport,at,limit=100):
    sent = 0
    for _ in range(limit):
        with transaction(db):
            r = db.execute("SELECT * FROM outbox WHERE state='pending' AND retry_at<=? ORDER BY id LIMIT 1",(at,)).fetchone()
            if not r:
                break
            state = "sent"
            if r["version"] is not None:
                a = db.execute("SELECT a.version,c.tz FROM assignments a JOIN classes c ON a.class_id=c.id WHERE a.id=?",(r["assignment"],)).fetchone()
                if a["version"]!=r["version"]:
                    state = "superseded"
                if state=="sent" and r["kind"]=="reminder":
                    t = db.execute("SELECT status,touched FROM targets WHERE assignment=? AND student=?",(r["assignment"],r["student"])).fetchone()
                    if t["status"]!=r["expected_status"] or t["touched"]!=r["expected_touch"]:
                        state = "superseded"
                    elif not awake(at,a["tz"]):
                        db.execute("UPDATE outbox SET retry_at=? WHERE id=?",(at+600,r["id"]))
                        continue
            if state=="sent":
                try:
                    transport.send(r["chat"],r["body"])
                    sent += 1
                except DeliveryError as exc:
                    tries = r["tries"]+1
                    state = "failed" if exc.code in (400,403,404) or tries>=10 else "pending"
                    wait = max(exc.delay,min(3600,2**tries))
                    db.execute("UPDATE outbox SET state=?,tries=?,retry_at=? WHERE id=?",(state,tries,at+wait,r["id"]))
                    logging.warning("delivery_error id=%s code=%s state=%s",r["id"],exc.code,state)
                    continue
                logging.info("%s sent outbox_id=%s",r["kind"],r["id"])
            db.execute("UPDATE outbox SET state=? WHERE id=?",(state,r["id"]))
    return sent

def run_worker(db,model):
    tg = Telegram()
    last_scan = 0
    while True:
        at = now()
        if at-last_scan>=60:
            process_reminders(db,model,at)
            last_scan=at
        drain(db,tg,at)
        offset = db.execute("SELECT value FROM settings WHERE key='offset'").fetchone()
        try:
            updates = tg.call("getUpdates",{"offset":int(offset[0]) if offset else 0,"timeout":4,"allowed_updates":["message"]})
        except DeliveryError as exc:
            logging.warning("poll failed code=%s; check bot configuration if persistent",exc.code)
            time.sleep(max(5,exc.delay))
            continue
        for entry in updates:
            ingest(db,entry,model,now())
            db.execute("INSERT OR REPLACE INTO settings VALUES('offset',?)",(str(entry["update_id"]+1),))

