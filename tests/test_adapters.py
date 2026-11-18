"""Provider-boundary checks use a local HTTP responder; no external secrets are required."""
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler,HTTPServer
import json
import os
import threading
import unittest
from unittest.mock import patch
from companion.jobs import Telegram
from companion.model import LanguageModel,ModelError,ask,deadline

@contextmanager
def responder(reply,status=200):
    received=[]
    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            received.append((self.path,json.loads(self.rfile.read(int(self.headers['Content-Length'])))))
            self.send_response(status)
            self.send_header("Content-Type","application/json")
            self.end_headers()
            self.wfile.write(json.dumps(reply).encode())
        def log_message(self,*args):
            pass
    with HTTPServer(("127.0.0.1",0),Handler) as server:
        thread=threading.Thread(target=server.serve_forever,kwargs={"poll_interval":.02},daemon=True)
        thread.start()
        try:
            yield f"http://127.0.0.1:{server.server_port}",received
        finally:
            server.shutdown()
            thread.join()

class AdapterTests(unittest.TestCase):
    def test_model_request_and_structured_reply(self):
        reply={"status":"blocked","note":"Needs help with question three."}
        response={"choices":[{"message":{"content":json.dumps(reply)}}]}
        with responder(response) as (base,seen), patch.dict(os.environ,MODEL_API_KEY="fixture",MODEL_BASE_URL=base):
            self.assertEqual(ask(LanguageModel(),"progress",{"message":"I'm stuck."}),reply)
            path,body=seen[0]
            self.assertEqual(path,"/chat/completions")
            self.assertEqual(body["response_format"],{"type":"json_object"})
            self.assertEqual(json.loads(body["messages"][1]["content"])["message"],"I'm stuck.")
    def test_bad_model_json_is_recoverable(self):
        with responder({"choices":[{"message":{"content":"bad JSON"}}]}) as (base,_), patch.dict(os.environ,MODEL_API_KEY="fixture",MODEL_BASE_URL=base):
            with self.assertRaises(ModelError):
                LanguageModel().ask("progress",{})
    def test_telegram_delivery_serialization(self):
        from urllib.request import urlopen as real_open, Request
        with responder({"ok":True,"result":{"message_id":1}}) as (base,seen), patch.dict(os.environ,TELEGRAM_BOT_TOKEN="fixture"):
            def redirect(req,timeout):
                self.assertTrue(req.full_url.endswith("/botfixture/sendMessage"))
                return real_open(Request(base+"/sendMessage",data=req.data,headers=dict(req.header_items())),timeout=timeout)
            with patch("companion.jobs.request.urlopen",side_effect=redirect):
                Telegram().send(123,"Feedback")
            self.assertEqual(seen,[("/sendMessage",{"chat_id":123,"text":"Feedback"})])
    def test_rejects_clock_change_ambiguity(self):
        for date in ("2026-03-08T02:15","2026-11-01T01:15"):
            with self.subTest(date=date),self.assertRaises(ValueError):
                deadline({"due_local":date},"America/New_York",0)

