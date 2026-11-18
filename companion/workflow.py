from datetime import datetime
from zoneinfo import ZoneInfo
from . import model as lm
from .store import add_class, digest, event, invite, local, membership, person, transaction

STAFF_HELP = ("/classes · /class ID · /newclass NAME · /roster · /student NAME · /invite PERSON\n"
             "Describe work and a due date to draft for the class, or /to 2,3 TASK.\n"
             "/publish ID · /edit ID NEW DUE DATE · /clarify ID TEXT · /cancel ID\n"
             "/summary · /feedback SUBMISSION revise|complete MESSAGE · /list")
STUDENT_HELP = ("Send a progress update, or choose work with /progress ID MESSAGE. "
                "Use /submit ID ANSWER to hand it in. /list shows your assignments.")

def enqueue(db, chat, body, *, kind="reply", key=None, aid=None, ver=None, student=None, status=None, touched=None):
    if chat is not None:
        db.execute("""INSERT INTO outbox(chat,body,uniq,assignment,version,student,expected_status,expected_touch,kind)
        VALUES(?,?,?,?,?,?,?,?,?) ON CONFLICT(uniq) DO NOTHING""",
                   (chat,body[:4000],key,aid,ver,student,status,touched,kind))

def roster(db, cls):
    return db.execute("""SELECT p.* FROM people p JOIN members m ON m.person=p.id
        JOIN classes c ON c.id=m.class_id AND c.school=p.school WHERE c.id=? AND m.teaching=0 ORDER BY p.id""",(cls,)).fetchall()

def items(db,pid,cls):
    return db.execute("""SELECT a.*,t.status,t.touched,t.note,c.tz FROM targets t JOIN assignments a ON a.id=t.assignment
        JOIN classes c ON a.class_id=c.id JOIN members m ON m.class_id=c.id AND m.person=t.student
        WHERE t.student=? AND c.id=? AND a.stage!='draft' ORDER BY a.due,a.id""",(pid,cls)).fetchall()

def format_task(a,tz):
    return f"#{a['id']} {a['title']}\n{a['instructions']}\nDue {local(a['due'],tz)}"

def announce(db,a,cls,prefix):
    for target in db.execute("""SELECT p.tg FROM targets t JOIN people p ON t.student=p.id
        WHERE t.assignment=?""",(a["id"],)):
        enqueue(db,target["tg"],prefix+"\n"+format_task(a,cls["tz"]),aid=a["id"],ver=a["version"],kind="assignment")

def number(value):
    if not value.isascii() or not value.isdigit() or len(value)>12:
        raise ValueError("Please use the numeric ID shown by /list or /roster.")
    return int(value)

def evidence(db,cls,at):
    rows = db.execute("""SELECT a.id,a.title,p.name,t.status,t.note,a.due FROM targets t JOIN people p ON p.id=t.student
        JOIN assignments a ON a.id=t.assignment WHERE a.class_id=? AND a.stage='assigned'
        ORDER BY (t.status='blocked') DESC, a.due""",(cls,)).fetchall()
    return [dict(assignment=r["id"],title=r["title"],student=r["name"],status=r["status"],
                 overdue=r["due"]<at and r["status"] not in ("submitted","completed"),
                 note=r["note"]) for r in rows]

def teacher(db,user,cls,cmd,args,raw,model,at):
    pid,cid = user["id"],cls["id"]
    facts = dict(timezone=cls["tz"],local_now=datetime.fromtimestamp(at,ZoneInfo(cls["tz"])).isoformat())
    if cmd=="/roster":
        return "\n".join(f"{p['id']} · {p['name']} · {'linked' if p['tg'] else 'awaiting link'}" for p in roster(db,cid)) or "Empty roster."
    if cmd in ("/teacher","/share"):
        if not user["coordinator"]:
            raise ValueError("A school coordinator must authorize teacher membership.")
        if cmd=="/teacher":
            if not args or len(args)>80:
                raise ValueError("Use /teacher FULL NAME.")
            tid,_ = person(db,user["school"],args,staff=True)
        else:
            tid=number(args)
            if not db.execute("SELECT 1 FROM people WHERE school=? AND id=? AND staff=1",(user["school"],tid)).fetchone():
                raise ValueError("Choose a teacher in this school.")
            if membership(db,tid,cid):
                return "This person already belongs to the class."
        db.execute("INSERT INTO members VALUES(?,?,1)",(cid,tid))
        return f"Teacher {tid} added. Private invite if unlinked: /link "+invite(db,tid,cid,at)
    if cmd=="/student":
        if not args or len(args)>80:
            raise ValueError("Use /student FULL NAME.")
        sid,_ = person(db,user["school"],args)
        db.execute("INSERT INTO members VALUES(?,?,0)",(cid,sid))
        return "Give this student their private one-time invitation: /link "+invite(db,sid,cid,at)
    if cmd=="/invite":
        sid = number(args)
        if sid not in [p["id"] for p in roster(db,cid)]:
            raise ValueError("Choose a student in this class.")
        return "Private replacement invitation: /link "+invite(db,sid,cid,at)
    if cmd=="/summary":
        data = evidence(db,cid,at)
        if not data:
            return "No assigned work yet."
        recent = [dict(r) for r in db.execute("SELECT kind,assignment,at FROM events WHERE class_id=? ORDER BY id DESC LIMIT 6",(cid,))]
        return lm.field(lm.ask(model,"summary",{"work":data[:80],"recent":recent}),"text",2500)
    if cmd=="/list":
        rows = db.execute("SELECT id,title,stage FROM assignments WHERE class_id=? ORDER BY id DESC LIMIT 30",(cid,))
        return "\n".join(f"#{r['id']} {r['title']} · {r['stage']}" for r in rows) or "No assignments."
    ident,_,body = args.partition(" ")
    if cmd=="/feedback":
        decision,_,note = body.partition(" ")
        row = db.execute("""SELECT s.*,a.stage,t.status,p.tg FROM submissions s JOIN assignments a ON a.id=s.assignment
            JOIN targets t ON t.assignment=a.id AND t.student=s.student JOIN people p ON p.id=s.student
            WHERE s.id=? AND a.class_id=? AND s.id=(SELECT max(id) FROM submissions
            WHERE assignment=s.assignment AND student=s.student)""",(number(ident),cid)).fetchone()
        if not row or row["stage"]!="assigned" or row["status"]!="submitted":
            raise ValueError("No reviewable submission with that ID in this class.")
        if decision not in ("revise","complete") or not note.strip():
            raise ValueError("Use /feedback SUBMISSION revise|complete MESSAGE.")
        status = "needs-revision" if decision=="revise" else "completed"
        db.execute("INSERT INTO feedback(submission,teacher,body,decision,at) VALUES(?,?,?,?,?)",(row["id"],pid,note,status,at))
        db.execute("UPDATE targets SET status=?,touched=? WHERE assignment=? AND student=?",(status,at,row["assignment"],row["student"]))
        enqueue(db,row["tg"],f"Feedback on #{row['assignment']} · {status}\n{note}",kind="feedback")
        event(db,cid,row["assignment"],pid,"feedback recorded",at)
        return "Feedback saved for delivery."
    if cmd in ("/publish","/edit","/clarify","/cancel"):
        aid = number(ident)
        a = db.execute("SELECT * FROM assignments WHERE id=? AND class_id=?",(aid,cid)).fetchone()
        if not a or a["stage"]=="cancelled":
            raise ValueError("No editable assignment with that ID in this class.")
        if cmd=="/publish":
            if a["stage"]!="draft":
                return "Already published."
            if a["due"]<=at:
                raise ValueError("Update the deadline before publishing.")
            db.execute("UPDATE assignments SET stage='assigned' WHERE id=?",(aid,))
            db.execute("UPDATE targets SET touched=? WHERE assignment=?",(at,aid))
        elif cmd=="/cancel":
            db.execute("UPDATE assignments SET stage='cancelled',version=version+1 WHERE id=?",(aid,))
        elif cmd=="/edit":
            due = lm.deadline(lm.ask(model,"deadline",dict(facts,message=body)),cls["tz"],at)
            db.execute("UPDATE assignments SET due=?,version=version+1 WHERE id=?",(due,aid))
        else:
            if not body.strip():
                raise ValueError("Use /clarify ID NEW INSTRUCTIONS.")
            db.execute("UPDATE assignments SET instructions=?,version=version+1 WHERE id=?",(body,aid))
        updated = db.execute("SELECT * FROM assignments WHERE id=?",(aid,)).fetchone()
        prefix = {"/publish":"New assignment","/edit":"Deadline updated","/clarify":"Instructions updated","/cancel":"Cancelled"}[cmd]
        if updated["stage"]!="draft" and not (cmd=="/cancel" and a["stage"]=="draft"):
            announce(db,updated,cls,prefix)
        event(db,cid,aid,pid,prefix,at)
        return prefix+" · "+format_task(updated,cls["tz"])
    if cmd.startswith("/") and cmd!="/to":
        return STAFF_HELP
    ids = {p["id"] for p in roster(db,cid)}
    if cmd=="/to":
        chosen = {number(x) for x in ident.split(",")}
        if not chosen.issubset(ids):
            raise ValueError("Every recipient must belong to this class.")
        ids = chosen
    if not ids:
        raise ValueError("Add students before drafting work.")
    data = lm.ask(model,"assignment",dict(facts,message=body if cmd=="/to" else raw))
    due = lm.deadline(data,cls["tz"],at)
    aid = db.execute("INSERT INTO assignments(class_id,creator,title,instructions,due) VALUES(?,?,?,?,?)",
                     (cid,pid,lm.field(data,"title",120),lm.field(data,"instructions"),due)).lastrowid
    db.executemany("INSERT INTO targets(assignment,student,touched) VALUES(?,?,?)",[(aid,s,at) for s in sorted(ids)])
    a = db.execute("SELECT * FROM assignments WHERE id=?",(aid,)).fetchone()
    event(db,cid,aid,pid,"draft created",at)
    return f"Draft for {cls['name']} · {len(ids)} student(s)\n"+format_task(a,cls["tz"])+f"\nConfirm with /publish {aid}"

def student(db,user,cls,cmd,args,raw,model,at):
    work = items(db,user["id"],cls["id"])
    if cmd=="/list":
        return "\n".join(f"#{a['id']} {a['title']} · {a['stage'] if a['stage']=='cancelled' else a['status']} · {local(a['due'],a['tz'])}" for a in work) or "No assignments."
    active = [a for a in work if a["stage"]=="assigned" and a["status"] not in ("completed","submitted")]
    if cmd in ("/submit","/progress"):
        ident,_,message = args.partition(" ")
        aid = number(ident)
        a = next((r for r in active if r["id"]==aid),None)
        if not a or not message.strip():
            raise ValueError("Choose open work from /list and include your message.")
    elif cmd.startswith("/"):
        return STUDENT_HELP
    elif len(active)==1:
        a,message = active[0],raw
    else:
        return "Use /list, then /progress ID MESSAGE or /submit ID ANSWER."
    pid,aid = user["id"],a["id"]
    if cmd=="/submit":
        status,note = "submitted",message
        sid = db.execute("INSERT INTO submissions(assignment,student,body,at) VALUES(?,?,?,?)",(aid,pid,note,at)).lastrowid
        for staff in db.execute("SELECT p.tg FROM members m JOIN people p ON m.person=p.id WHERE m.class_id=? AND m.teaching=1",(cls["id"],)):
            enqueue(db,staff["tg"],f"{user['name']} submitted #{aid}. Review submission {sid}:\n{note}\n/feedback {sid} complete MESSAGE")
    else:
        data = lm.ask(model,"progress",{"message":message,"previous":a["status"],"assignment":a["title"]})
        status,note = lm.field(data,"status",30),lm.field(data,"note",500)
        if status not in ("acknowledged","in-progress","blocked"):
            raise lm.ModelError()
        if status=="acknowledged" and a["status"]!="assigned":
            return "Acknowledged. Your existing progress is saved."
    db.execute("UPDATE targets SET status=?,note=?,touched=? WHERE assignment=? AND student=?",(status,note,at,aid,pid))
    event(db,cls["id"],aid,pid,"submission received" if cmd=="/submit" else status,at)
    return f"Saved · #{aid} {a['title']} · {status}"

def route(db,chat,raw,model,at):
    cmd,_,args = raw.strip().partition(" ")
    args = args.strip()
    usr = db.execute("SELECT * FROM people WHERE tg=?",(chat,)).fetchone()
    if cmd in ("/start","/link") and args:
        if usr:
            return "This Telegram account is already linked. Use /classes."
        row = db.execute("SELECT * FROM invites WHERE hash=? AND used=0 AND expires>?",(digest(args),at)).fetchone()
        if not row or not membership(db,row["person"],row["class_id"]):
            return "Invalid or expired invitation. Ask your teacher for a fresh code."
        if db.execute("SELECT tg FROM people WHERE id=?",(row["person"],)).fetchone()[0] is not None:
            return "This roster account is already linked."
        db.execute("UPDATE people SET tg=? WHERE id=?",(chat,row["person"]))
        db.execute("UPDATE invites SET used=1 WHERE hash=?",(digest(args),))
        db.execute("INSERT OR REPLACE INTO focus VALUES(?,?)",(row["person"],row["class_id"]))
        for a in items(db,row["person"],row["class_id"]):
            if a["stage"]=="assigned" and a["status"]!="completed":
                enqueue(db,chat,format_task(a,a["tz"]),kind="assignment",aid=a["id"],ver=a["version"])
        return "Account linked. Use /help for commands and /list for work."
    if not usr:
        return "Use /link CODE from your private invitation."
    pid = usr["id"]
    if cmd=="/web":
        import secrets
        key = secrets.token_urlsafe(24)
        db.execute("UPDATE people SET login_hash=? WHERE id=?",(digest(key),pid))
        return "Private web sign-in key (replaces your previous key): "+key
    if cmd=="/newclass":
        if not args or len(args)>80:
            raise ValueError("Use /newclass NAME.")
        cid = add_class(db,pid,args)
        db.execute("INSERT OR REPLACE INTO focus VALUES(?,?)",(pid,cid))
        return f"Created class {cid}. Use /student NAME to invite a learner."
    if cmd=="/classes":
        rows = db.execute("SELECT c.id,c.name FROM classes c JOIN members m ON m.class_id=c.id WHERE m.person=? AND c.school=?",(pid,usr["school"]))
        return "\n".join(f"{r['id']} · {r['name']}" for r in rows)
    if cmd=="/class":
        cid = number(args)
        if not membership(db,pid,cid):
            return "Access denied for that class."
        db.execute("INSERT OR REPLACE INTO focus VALUES(?,?)",(pid,cid))
        return "Selected class. Use /list."
    focus = db.execute("SELECT class_id FROM focus WHERE person=?",(pid,)).fetchone()
    cls = membership(db,pid,focus[0]) if focus else None
    if not cls:
        return "Select an authorized class with /classes and /class ID."
    handler = teacher if cls["teaching"] else student
    if cmd in ("/help","/start"):
        help_text=STAFF_HELP if cls["teaching"] else STUDENT_HELP
        if usr["coordinator"]:
            help_text+="\n/teacher NAME · /share EXISTING_TEACHER_ID"
        return help_text+"\n/web · private web sign-in key"
    return handler(db,usr,cls,cmd,args,raw,model,at)

def ingest(db,update,model,at):
    """A Telegram message and its resulting delivery intents commit together."""
    uid = update["update_id"]
    msg = update.get("message") or {}
    chat = msg.get("chat") or {}
    with transaction(db):
        if not db.execute("INSERT OR IGNORE INTO updates VALUES(?)",(uid,)).rowcount:
            return False
        if chat.get("type")!="private" or chat.get("id")!=msg.get("from",{}).get("id"):
            return True
        text = msg.get("text","").strip()
        db.execute("SAVEPOINT action")
        try:
            reply = route(db,chat["id"],text,model,at) if text and len(text)<=4000 else "Please send text (up to 4000 characters). Use /submit ID ANSWER for work."
        except (ValueError,lm.ModelError) as exc:
            db.execute("ROLLBACK TO action")
            if isinstance(exc,lm.ModelError):
                event(db,None,None,None,"model failure",at)
                reply = "I couldn't interpret that reliably. No change was saved. Try again with an explicit task and date or use /help."
            else:
                reply = str(exc)
        db.execute("RELEASE action")
        enqueue(db,chat["id"],reply,key=f"reply:{uid}")
    return True

