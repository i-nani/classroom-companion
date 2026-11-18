"""Server-rendered pages. Every work or class lookup starts from the signed-in identity."""
from contextlib import closing
from html import escape
from http.cookies import SimpleCookie
import os
from pathlib import Path
import secrets
from urllib.parse import parse_qs
from wsgiref.simple_server import make_server

from .jobs import process_reminders
from .model import LanguageModel
from .store import connect, digest, local, membership, now, transaction

CSS = Path(__file__).with_name("style.css").read_text()
def h(value):
    return escape(str(value),quote=True)

def pill(status):
    tone = " warn" if status in ("blocked","overdue","needs-revision","awaiting link") else ""
    return f'<span class="pill{tone}">{h(status)}</span>'

def page(title,body,user=None,csrf=""):
    identity = ""
    if user:
        identity = f'<span class="identity right">{h(user["name"])}</span><form method="post" action="/logout"><input type="hidden" name="csrf" value="{h(csrf)}"><button>Sign out</button></form>'
    return f'<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>{h(title)} · Classroom Companion</title><link rel="stylesheet" href="/style.css"></head><body><header><div class="wrap"><span class="mark" aria-hidden="true">cc</span><b>Classroom Companion</b>{identity}</div></header><main>{body}</main></body></html>'

def rows(db,cls,user,aid=0,student=0):
    return db.execute("""SELECT a.*,p.name,t.student,t.status,t.note,t.touched FROM assignments a
        JOIN targets t ON t.assignment=a.id JOIN people p ON p.id=t.student
        WHERE a.class_id=? AND (? OR (t.student=? AND a.stage!='draft'))
        AND (?=0 OR a.id=?) AND (?=0 OR t.student=?) ORDER BY a.due,a.id,p.name""",
        (cls["id"],cls["teaching"],user["id"],aid,aid,student,student)).fetchall()

def badges(r,at):
    if r["stage"]!="assigned":
        return pill(r["stage"])
    extra = pill("overdue") if r["due"]<at and r["status"] not in ("submitted","completed") else ""
    return pill(r["status"])+extra

def dashboard(db,user,classes,cls,session,at,notice):
    is_staff = cls["teaching"]
    data = rows(db,cls,user)
    cards = []
    risk = []
    reviewed = set()
    for r in data:
        url = f"/work/{r['id']}?class={cls['id']}&amp;student={r['student']}"
        title = h(r["title"])
        who = h(r["name"])
        cards.append(f'<div class="row"><a href="{url}">{title}</a><p>{who} · Due {h(local(r["due"],cls["tz"]))}</p>{badges(r,at)}</div>')
        if r["stage"]=="assigned" and r["status"] not in ("submitted","completed"):
            if r["status"] in ("blocked","needs-revision") or r["due"]<at or (r["status"]=="assigned" and r["due"]-at<=86400):
                risk.append(f'<div class="row"><a href="{url}">{who} · {title}</a><p>{h(r["note"] or "No progress update yet.")}</p>{badges(r,at)}</div>')
        if r["status"]=="submitted" and r["stage"]=="assigned":
            reviewed.add((r["id"],r["student"]))
    stats = [(len({r["id"] for r in data}),"Assignments"),(len(risk),"Need attention"),(len(reviewed),"Awaiting review")]
    stat_html = "".join(f'<div class="stat"><b>{n}</b><span>{label}</span></div>' for n,label in stats)
    nav = " · ".join(f'<a href="/?class={c["id"]}">{h(c["name"])}</a>' for c in classes) if len(classes)>1 else ""
    head = f'<div class="eyebrow">{h(user["school_name"])} / {"Teacher" if is_staff else "Student"} workspace</div><h1>{h(cls["name"])}</h1><p class="muted">{h(user["name"])} · {h(cls["tz"])}</p>{nav}'
    if notice:
        head += '<p class="notice">Reminder scan finished. Eligible messages are queued for delivery.</p>'
    panel = lambda name,content: f'<section class="card"><h2>{name}</h2>{content}</section>'
    side = ""
    if is_staff:
        form = f'<form method="post" action="/remind"><input type="hidden" name="csrf" value="{h(session["csrf"])}"><button>Check my classes for reminders</button></form>'
        side += panel("Needs attention","".join(risk) or "<p>No open concerns.</p>")
        roster = db.execute("""SELECT p.name,p.tg FROM members m JOIN people p ON p.id=m.person WHERE m.class_id=? AND m.teaching=0 ORDER BY p.name""",(cls["id"],))
        side += panel("Class roster","".join(f'<div class="row">{h(p["name"])} {pill("linked" if p["tg"] else "awaiting link")}</div>' for p in roster))
        events = db.execute("SELECT kind,actor,at,(SELECT name FROM people WHERE id=actor) name FROM events WHERE class_id=? ORDER BY id DESC LIMIT 6",(cls["id"],))
        side += panel("Recent activity","".join(f'<div class="row"><b>{h(e["kind"])}</b><br><small>{h(e["name"] or "System")} · {h(local(e["at"],cls["tz"]))}</small></div>' for e in events) or "No activity yet.")
    else:
        form = ""
        reminders = db.execute("""SELECT body FROM outbox WHERE kind='reminder' AND state='sent' AND chat=? AND student=?
          AND assignment IN (SELECT id FROM assignments WHERE class_id=?) ORDER BY id DESC LIMIT 5""",(user["tg"],user["id"],cls["id"]))
        side = panel("My reminders","".join(f'<div class="row note">{h(r[0])}</div>' for r in reminders) or "No reminders delivered yet.")
    return head+f'<div class="stats">{stat_html}</div><div class="toolbar">{form}</div><div class="grid"><div>'+panel("Assignment tracker" if is_staff else "My assignments","".join(cards) or "<p>No assignments yet.</p>")+f'</div><aside class="side">{side}</aside></div>'

def detail(db,cls,r,at):
    subs = db.execute("""SELECT s.*,f.body feedback,f.decision,p.name teacher FROM submissions s
        LEFT JOIN feedback f ON f.submission=s.id LEFT JOIN people p ON p.id=f.teacher
        WHERE s.assignment=? AND s.student=? ORDER BY s.id DESC""",(r["id"],r["student"]))
    history = ""
    for s in subs:
        history += f'<div class="row"><b>Submission {s["id"]}</b> · {h(local(s["at"],cls["tz"]))}<p class="note">{h(s["body"])}</p>'
        if s["feedback"]:
            history += f'<b>Feedback from {h(s["teacher"])}</b> {pill(s["decision"])}<p class="note">{h(s["feedback"])}</p>'
        history += '</div>'
    return f'<a href="/?class={cls["id"]}">← {h(cls["name"])}</a><h1>{h(r["title"])}</h1><p>{h(r["name"])} · Due {h(local(r["due"],cls["tz"]))}</p>{badges(r,at)}<div class="grid"><section class="card"><h2>Submissions and feedback</h2>{history or "No submissions yet."}</section><section class="card"><h2>Instructions</h2><p class="note">{h(r["instructions"])}</p><h3>Latest student update</h3><p class="note">{h(r["note"] or "No update yet.")}</p></section></div>'

class Web:
    def __init__(self,path,model=None,clock=now):
        self.path,self.model,self.clock=path,model or LanguageModel(),clock
    def __call__(self,env,start):
        def reply(status,content="",extra=(),ctype="text/html; charset=utf-8"):
            data=content.encode()
            start(status,[("Content-Type",ctype),("Content-Length",str(len(data))),("Cache-Control","no-store"),
                          ("X-Content-Type-Options","nosniff"),("Referrer-Policy","no-referrer"),
                          ("Content-Security-Policy","default-src 'self'; frame-ancestors 'none'; form-action 'self'"),*extra])
            return [data]
        def redirect(path,*headers):
            return reply("303 See Other","",[("Location",path),*headers])
        path,method=env["PATH_INFO"],env["REQUEST_METHOD"]
        if path=="/style.css":
            return reply("200 OK",CSS,ctype="text/css")
        if method not in ("GET","POST"):
            return reply("405 Method Not Allowed")
        at=self.clock()
        try:
            args=parse_qs(env.get("QUERY_STRING",""),max_num_fields=20) if len(env.get("QUERY_STRING",""))<2048 else {}
        except ValueError:
            return reply("400 Bad Request")
        q=lambda name: args.get(name,[""])[0]
        cookie=SimpleCookie()
        try:
            cookie.load(env.get("HTTP_COOKIE",""))
        except Exception:
            pass
        token=cookie["cc_session"].value if "cc_session" in cookie else ""
        with closing(connect(self.path)) as db:
            session=db.execute("SELECT * FROM sessions WHERE hash=? AND expires>?",(digest(token),at)).fetchone()
            user=db.execute("SELECT p.*,s.name school_name FROM people p JOIN schools s ON s.id=p.school WHERE p.id=?",(session["person"],)).fetchone() if session else None
            if method=="POST":
                try:
                    length=int(env.get("CONTENT_LENGTH") or 0)
                    if not 0<=length<=4096:
                        raise ValueError
                    args=parse_qs(env["wsgi.input"].read(length).decode(),max_num_fields=8)
                except (ValueError,UnicodeError):
                    return reply("400 Bad Request")
                if path=="/login":
                    account=db.execute("SELECT id FROM people WHERE login_hash=?",(digest(q("key")),)).fetchone()
                    if not account:
                        return reply("403 Forbidden",page("Sign in",'<p>Invalid sign-in key.</p><a href="/login">Try again</a>'))
                    fresh=secrets.token_urlsafe(32)
                    db.execute("INSERT INTO sessions VALUES(?,?,?,?)",(digest(fresh),account[0],at+28800,secrets.token_urlsafe(24)))
                    secure="; Secure" if os.getenv("COOKIE_SECURE")=="1" else ""
                    return redirect("/",("Set-Cookie","cc_session="+fresh+"; HttpOnly; SameSite=Strict; Path=/; Max-Age=28800"+secure))
                if not session or not secrets.compare_digest(session["csrf"],q("csrf")):
                    return reply("403 Forbidden")
                if path=="/logout":
                    db.execute("DELETE FROM sessions WHERE hash=?",(session["hash"],))
                    return redirect("/login",("Set-Cookie","cc_session=; Path=/; Max-Age=0"))
                if path=="/remind" and db.execute("SELECT 1 FROM members WHERE teaching=1 AND person=?",(user["id"],)).fetchone():
                    process_reminders(db,self.model,at,teacher=user["id"])
                    return redirect("/?scanned=1")
                return reply("403 Forbidden")
            if path=="/login":
                return reply("200 OK",page("Sign in",'<section class="entry card"><div class="eyebrow">Classroom workspace</div><h1>Sign in</h1><p>Get your private sign-in key with /web in Telegram, or use your key from setup.</p><form method="post"><label>Sign-in key<input name="key" type="password" required autocomplete="current-password"></label><button>Open workspace</button></form></section>'))
            if not user:
                return redirect("/login")
            classes=db.execute("SELECT c.* FROM classes c JOIN members m ON m.class_id=c.id WHERE m.person=? AND c.school=? ORDER BY c.id",(user["id"],user["school"])).fetchall()
            if not classes:
                return reply("403 Forbidden")
            try:
                cls=membership(db,user["id"],int(q("class") or classes[0]["id"]))
                if not cls:
                    raise ValueError
                if path=="/":
                    content=dashboard(db,user,classes,cls,session,at,q("scanned")=="1")
                elif path.startswith("/work/"):
                    aid=int(path.removeprefix("/work/"))
                    sid=int(q("student") or user["id"])
                    if aid<=0 or sid<=0:
                        raise ValueError
                    data=rows(db,cls,user,aid,sid)
                    if not data:
                        raise ValueError
                    content=detail(db,cls,data[0],at)
                else:
                    raise ValueError
            except (ValueError,OverflowError):
                return reply("404 Not Found",page("Unavailable",'<h1>Work unavailable</h1><p>This item is outside your workspace or no longer exists.</p><a href="/">Return to workspace</a>',user,session["csrf"]))
            return reply("200 OK",page(cls["name"],content,user,session["csrf"]))

def serve(path,port):
    with make_server("127.0.0.1",port,Web(path)) as server:
        print(f"Workspace: http://127.0.0.1:{port}",flush=True)
        server.serve_forever()

