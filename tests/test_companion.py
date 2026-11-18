from contextlib import closing
import io
from pathlib import Path
import tempfile
import unittest
from urllib.parse import urlencode

from companion.demo import BLOCK,CHATS,CREATE,EDIT,Replay,telegram_update
from companion.jobs import DeliveryError,drain,process_reminders
from companion.model import ModelError
from companion.store import add_class,connect,invite,person,transaction
from companion.web import Web
from companion.workflow import ingest

class BrokenModel:
    def ask(self,task,facts):
        raise ModelError()

class PilotTests(unittest.TestCase):
    def setUp(self):
        tmp=tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.path=Path(tmp.name)/"db.sqlite"
        self.db=connect(self.path)
        self.addCleanup(self.db.close)
        self.r=Replay(self.db,1794976200) # 10:00 IST
        self.steps=self.r.steps()
        next(self.steps)
    def value(self,sql):
        return self.db.execute(sql).fetchone()[0]
    def through(self,name):
        for label in self.steps:
            if label.startswith(name):
                return
        self.fail(name)
    def request(self,path="/",method="GET",form=None,cookie="",query=""):
        payload=urlencode(form or {}).encode()
        env=dict(PATH_INFO=path,REQUEST_METHOD=method,QUERY_STRING=query,CONTENT_LENGTH=str(len(payload)),HTTP_COOKIE=cookie)
        env["wsgi.input"]=io.BytesIO(payload)
        out={}
        def start(status,headers):
            out["status"]=status.split()[0]
            out["headers"]=dict(headers)
        app=Web(self.path,self.r.model,lambda:self.r.at)
        out["body"]=b"".join(app(env,start)).decode()
        return out
    def login(self,name):
        res=self.request("/login","POST",{"key":self.r.accounts[name]["login"]})
        self.assertEqual(res["status"],"303")
        return res["headers"]["Set-Cookie"].split(";")[0]
    def test_complete_demo(self):
        list(self.steps)
        self.assertEqual(self.value("SELECT count(*) FROM submissions"),1)
        self.assertEqual(self.value("SELECT status FROM targets WHERE student=2"),"completed")
        self.assertEqual(self.value("SELECT status FROM targets WHERE student=3"),"assigned")
        self.assertEqual(self.value("SELECT count(*) FROM feedback"),1)
        self.assertEqual(self.value("SELECT count(*) FROM outbox WHERE kind='reminder' AND state='sent'"),2)
    def test_duplicate_draft_and_submit(self):
        self.through("Publish")
        update=telegram_update(3,CHATS["Mira"],CREATE)
        self.assertFalse(ingest(self.db,update,self.r.model,self.r.at))
        self.r.message("Asha","/submit 1 Answer")
        self.r.message("Asha","/submit 1 Answer")
        self.assertEqual(self.value("SELECT count(*) FROM assignments"),1)
        self.assertEqual(self.value("SELECT count(*) FROM submissions"),1)
    def test_invite_binding_and_reissue(self):
        key=self.r.accounts["Ravi"]["invite"]
        with transaction(self.db):
            fresh=invite(self.db,3,1,self.r.at)
        self.r.message("Ravi","/link "+key)
        self.assertIsNone(self.value("SELECT tg FROM people WHERE id=3"))
        self.r.message("Ravi","/link "+fresh)
        res=telegram_update(700,99999,"/link "+fresh)
        ingest(self.db,res,self.r.model,self.r.at)
        self.assertEqual(self.value("SELECT tg FROM people WHERE id=3"),CHATS["Ravi"])
        self.assertIsNone(self.db.execute("SELECT 1 FROM people WHERE tg=99999").fetchone())
    def test_expired_invitation(self):
        self.r.at+=86401
        self.r.message("Ravi","/link "+self.r.accounts["Ravi"]["invite"])
        self.assertIsNone(self.value("SELECT tg FROM people WHERE id=3"))
    def test_restart_ignores_old_deadline_then_link_gets_latest(self):
        self.through("Publish")
        self.r.at+=7200
        process_reminders(self.db,self.r.model,self.r.at)
        with closing(connect(self.path)) as reopened:
            ingest(reopened,telegram_update(820,CHATS["Mira"],EDIT),self.r.model,self.r.at)
            drain(reopened,self.r.transport,self.r.at)
        self.assertEqual(self.value("SELECT state FROM outbox WHERE kind='reminder'"),"superseded")
        self.r.message("Ravi","/link "+self.r.accounts["Ravi"]["invite"])
        self.assertEqual(self.value("SELECT version FROM outbox WHERE chat=11003 AND kind='assignment'"),2)
    def test_silence_blocked_quiet_hours_and_repeat_scan(self):
        self.through("Record progress")
        self.r.at+=7200
        self.assertEqual(process_reminders(self.db,self.r.model,self.r.at+12*3600),0)
        self.assertEqual(self.r.scan(),2)
        self.assertEqual(self.r.scan(),0)
        self.assertEqual({r[0] for r in self.db.execute("SELECT chat FROM outbox WHERE kind='reminder'")},{11001,11003})
    def test_submission_supersedes_pending_reminder(self):
        self.through("Record progress")
        self.r.at+=7200
        process_reminders(self.db,self.r.model,self.r.at)
        self.r.message("Asha","/submit 1 my answer")
        self.assertEqual(self.value("SELECT state FROM outbox WHERE kind='reminder' AND student=2"),"superseded")
    def test_revision_then_resubmit_then_complete(self):
        self.through("Submit text")
        self.r.message("Mira","/feedback 1 revise Please show the steps.")
        self.r.message("Asha","/submit 1 Here are my steps.")
        self.r.message("Mira","/feedback 1 complete Stale")
        self.assertEqual(self.value("SELECT count(*) FROM feedback"),1)
        self.r.message("Mira","/feedback 2 complete Good work.")
        self.assertEqual(self.value("SELECT status FROM targets WHERE student=2"),"completed")
    def test_target_subset_and_class_ownership(self):
        self.r.message("Mira","/to 3 "+CREATE)
        self.r.message("Mira","/publish 1")
        self.assertEqual(self.value("SELECT count(*) FROM targets"),1)
        self.r.message("Mira","/newclass Literature")
        self.r.message("Mira","/edit 1 Move to tomorrow.")
        self.assertEqual(self.value("SELECT version FROM assignments"),1)
    def test_model_error_rolls_back_and_ambiguous_does_not_mutate(self):
        ingest(self.db,telegram_update(700,CHATS["Mira"],CREATE),BrokenModel(),self.r.at)
        self.r.message("Mira","maybe sometime")
        self.assertEqual(self.value("SELECT count(*) FROM assignments"),0)
        self.assertEqual(self.value("SELECT count(*) FROM events WHERE kind='model failure'"),1)
    def test_draft_is_private_and_cancel_stops_delivery(self):
        self.through("Review")
        cookie=self.login("Asha")
        self.assertNotIn("Fractions",self.request(cookie=cookie)["body"])
        self.through("Publish")
        self.r.message("Mira","/cancel 1")
        self.r.at+=7200
        self.assertEqual(self.r.scan(),0)
        self.r.message("Asha","/submit 1 Too late")
        self.assertEqual(self.value("SELECT count(*) FROM submissions"),0)
    def test_scoped_views_sessions_and_csrf(self):
        list(self.steps)
        ravi=self.login("Ravi")
        self.assertEqual(self.request("/work/1",cookie=ravi,query="class=1&student=2")["status"],"404")
        self.assertNotIn("Asha",self.request(cookie=ravi)["body"])
        self.assertIn("Good work",self.request("/work/1",cookie=self.login("Asha"),query="class=1&student=2")["body"])
        teacher=self.login("Mira")
        self.assertEqual(self.request("/remind","POST",cookie=teacher)["status"],"403")
        self.assertEqual(self.request("/","GET")["status"],"303")
        with transaction(self.db):
            school=self.db.execute("INSERT INTO schools(name) VALUES('Other')").lastrowid
            outsider,_=person(self.db,school,"Other teacher",True)
            cid=add_class(self.db,outsider,"Other class")
        self.assertEqual(self.request(cookie=teacher,query=f"class={cid}")["status"],"404")
    def test_delivery_retry(self):
        class Unavailable:
            def send(self,chat,text):
                raise DeliveryError(429,45)
        ingest(self.db,telegram_update(900,CHATS["Mira"],"/list"),self.r.model,self.r.at)
        drain(self.db,Unavailable(),self.r.at)
        self.assertEqual(self.value("SELECT count(*) FROM outbox WHERE state='pending'"),1)
        self.assertEqual(drain(self.db,self.r.transport,self.r.at+44),0)
        self.assertEqual(drain(self.db,self.r.transport,self.r.at+45),1)
    def test_coordinator_can_share_class_only_inside_school(self):
        self.r.message("Mira","/teacher Neel")
        self.assertEqual(self.value("SELECT teaching FROM members WHERE person=4 AND class_id=1"),1)
        self.r.message("Mira","/newclass Geometry")
        self.r.message("Mira","/share 4")
        self.assertEqual(self.value("SELECT count(*) FROM members WHERE person=4"),2)
        self.r.message("Asha","/share 4")
        self.assertEqual(self.value("SELECT count(*) FROM members WHERE person=4"),2)

