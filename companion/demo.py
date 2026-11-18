"""Explicit offline replay. Scripted model outputs here are test fixtures, never a live fallback."""
from datetime import datetime,timedelta
from zoneinfo import ZoneInfo
from .jobs import drain,process_reminders
from .store import seed,transaction
from .workflow import ingest

CREATE = "Assign problems 1–5 from chapter 4 by tomorrow evening."
EDIT = "/edit 1 Move the deadline to the following morning at 10 am, two days from today."
PROGRESS = "I've finished the first two problems."
BLOCK = "I'm stuck on the fractions in question 3."
CHATS = {"Mira":11001,"Asha":11002,"Ravi":11003}

class FixtureModel:
    def ask(self,task,facts):
        if task in ("assignment","deadline"):
            base=datetime.fromisoformat(facts["local_now"])
            if task=="assignment" and facts["message"]==CREATE:
                due=(base+timedelta(days=1)).replace(hour=18,minute=0)
                return {"title":"Chapter 4 · Fractions","instructions":"Solve problems 1–5 from chapter 4.","due_local":due.strftime("%Y-%m-%dT%H:%M")}
            if task=="deadline" and facts["message"]==EDIT.split(" ",2)[2]:
                due=(base+timedelta(days=2)).replace(hour=10,minute=0)
                return {"due_local":due.strftime("%Y-%m-%dT%H:%M")}
        if task=="progress":
            if facts["message"]==PROGRESS:
                return {"status":"in-progress","note":"Finished problems 1 and 2."}
            if facts["message"]==BLOCK:
                return {"status":"blocked","note":"Needs help with fractions in question 3."}
        if task=="reminder":
            return {"text":"Can you help Asha with question 3?" if facts["status"]=="blocked" else "How are problems 1–5 going? Reply if you need a hand."}
        if task=="summary":
            return {"text":"Asha needs help with fractions in question 3. Ravi has not reported progress."}
        return {"clarify":"Which assignment and deadline did you mean?"}

class Capture:
    def __init__(self):
        self.messages=[]
    def send(self,chat,text):
        self.messages.append((chat,text))

def base_time():
    zone=ZoneInfo("Asia/Kolkata")
    return int(datetime.now(zone).replace(hour=10,minute=0,second=0,microsecond=0).timestamp())

def telegram_update(uid,chat,text):
    return {"update_id":uid,"message":{"chat":{"type":"private","id":chat},"from":{"id":chat},"text":text}}

class Replay:
    def __init__(self,db,at=None):
        self.db,self.at=db,at or base_time()
        self.model,self.transport,self.uid=FixtureModel(),Capture(),0
        with transaction(db):
            self.accounts=seed(db,self.at)
    def message(self,name,text):
        self.uid+=1
        uid=self.uid
        update=telegram_update(uid,CHATS[name],text)
        ingest(self.db,update,self.model,self.at)
        drain(self.db,self.transport,self.at)
        return update
    def scan(self):
        n=process_reminders(self.db,self.model,self.at)
        drain(self.db,self.transport,self.at)
        return n
    def steps(self):
        self.message("Mira","/link "+self.accounts["Mira"]["invite"])
        self.message("Asha","/link "+self.accounts["Asha"]["invite"])
        yield "Link teacher and first student"
        self.message("Mira",CREATE)
        yield "Review the draft and resolved deadline"
        self.message("Mira","/publish 1")
        yield "Publish the class assignment"
        self.message("Mira",EDIT)
        yield "Change the deadline"
        self.message("Ravi","/link "+self.accounts["Ravi"]["invite"])
        yield "Link Ravi after the deadline changed"
        self.message("Asha",PROGRESS)
        self.message("Asha",BLOCK)
        yield "Record progress, then a blocker"
        self.at+=7200
        self.scan()
        self.message("Mira","/summary")
        yield "Run contextual reminders and the teacher briefing"
        update=self.message("Asha","/submit 1 Question 3: 3/4 + 1/8 = 7/8. My answers are attached here as text.")
        ingest(self.db,update,self.model,self.at)
        yield "Submit text and replay the same incoming update"
        self.message("Mira","/feedback 1 complete Good work using a common denominator. Your answer to question 3 is correct.")
        yield "Give feedback and complete the work"
        self.message("Ravi","/class 999")
        yield "Reject a wrong-class request"

def print_replay(db):
    replay=Replay(db)
    pos=0
    print("OFFLINE REPLAY · scripted model and captured Telegram transport")
    for label in replay.steps():
        print("\n"+label)
        for chat,body in replay.transport.messages[pos:]:
            print(" →",next(n for n,i in CHATS.items() if i==chat),body)
        pos=len(replay.transport.messages)
    return replay.accounts

