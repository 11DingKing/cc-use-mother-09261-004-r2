import os,tempfile,unittest
from service_09261_004.store import SQLiteStore
from service_09261_004.workflow import Workflow,Conflict
from service_09261_004.api import dispatch
class TestFlow(unittest.TestCase):
 def setUp(self):self.f=Workflow(SQLiteStore())
 def test_submit_assigns_owner_and_due(self):
  c=self.f.submit("f1","teacher","owner-a","2026-10-01","第3页例题数据有误")
  self.assertEqual((c.reporter,c.owner,c.due,c.state,c.version),("teacher","owner-a","2026-10-01","draft",1))
  with self.assertRaises(ValueError):self.f.submit("f2","teacher","","2026-10-01")
  with self.assertRaises(ValueError):self.f.submit("f3","teacher","owner-a","not-a-date")
 def test_revisions_are_kept(self):
  self.f.submit("f1","t","o1","2026-10-01","v1")
  self.f.revise("f1","o1","v2",1)
  c=self.f.revise("f1","o1","v3",2)
  self.assertEqual((c.detail,c.version),("v3",3))
  self.assertEqual([e["body"].get("detail") for e in self.f.history("f1")],["v1","v2","v3"])
 def test_reassign_is_recorded(self):
  self.f.submit("f1","t","o1","2026-10-01","d")
  c=self.f.reassign("f1","admin","o2","2026-11-01",1)
  self.assertEqual((c.owner,c.due,c.version),("o2","2026-11-01",2))
  self.assertEqual([e["type"] for e in self.f.history("f1")],["submitted","reassigned"])
 def test_withdraw_appends_and_keeps_original(self):
  self.f.submit("f1","t","o1","2026-10-01","原始反馈")
  with self.assertRaises(ValueError):self.f.move("f1","withdrawn","o1",1)
  c=self.f.withdraw("f1","o1","重复反馈",1)
  self.assertEqual(c.state,"withdrawn")
  h=self.f.history("f1")
  self.assertEqual([e["type"] for e in h],["submitted","withdrawn"])
  self.assertEqual(h[0]["body"]["detail"],"原始反馈")
  with self.assertRaises(ValueError):self.f.revise("f1","o1","x",2)
 def test_duplicate_submit_is_idempotent(self):
  self.f.submit("f1","t","o1","2026-10-01","d",key="k1")
  b=self.f.submit("f1","t","o1","2026-10-01","d",key="k1")
  self.assertEqual(b.version,1);self.assertEqual(len(self.f.snapshot()),1)
  with self.assertRaises(ValueError):self.f.submit("f1","t","o1","2026-10-01","d")
 def test_out_of_order_rejected(self):
  self.f.submit("f1","t","o1","2026-10-01","d")
  with self.assertRaises(Conflict):self.f.revise("f1","o1","x",7)
  with self.assertRaises(ValueError):self.f.move("f1","approved","o1",1)
  c=self.f.move("f1","reviewing","o1",1)
  self.assertEqual((c.state,c.version),("reviewing",2))
  with self.assertRaises(Conflict):self.f.move("f1","approved","o1",1)
 def test_retry_after_timeout_does_not_double_apply(self):
  self.f.submit("f1","t","o1","2026-10-01","d")
  self.f.revise("f1","o1","v2",1,key="r1")
  c=self.f.revise("f1","o1","v2",1,key="r1")
  self.assertEqual(c.version,2);self.assertEqual(len(self.f.history("f1")),2)
 def test_recovery_keeps_single_current_version(self):
  with tempfile.TemporaryDirectory() as d:
   path=os.path.join(d,"events.db")
   f1=Workflow(SQLiteStore(path));f1.submit("f1","t","o1","2026-10-01","d");f1.revise("f1","o1","v2",1)
   f2=Workflow(SQLiteStore(path))
   snap=f2.snapshot()
   self.assertEqual(len(snap),1)
   self.assertEqual((snap[0]["detail"],snap[0]["version"]),("v2",2))
   self.assertEqual(f2.revise("f1","o1","v3",2).version,3)
 def test_api(self):
  code,body=dispatch(self.f,"POST","/feedbacks",{"id":"f1","actor":"t","owner":"o1","due":"2026-10-01","detail":"d","idempotency_key":"k"})
  self.assertEqual((code,body["owner"],body["version"]),(201,"o1",1))
  code,body=dispatch(self.f,"POST","/feedbacks",{"id":"f1","actor":"t","owner":"o1","due":"2026-10-01","idempotency_key":"k"})
  self.assertEqual((code,body["version"]),(201,1))
  code,_=dispatch(self.f,"POST","/feedbacks/f1/move",{"state":"approved","actor":"o1","expected_version":1})
  self.assertEqual(code,422)
  code,_=dispatch(self.f,"POST","/feedbacks/f1/move",{"state":"reviewing","actor":"o1","expected_version":1})
  self.assertEqual(code,200)
  code,_=dispatch(self.f,"POST","/feedbacks/f1/move",{"state":"approved","actor":"o1","expected_version":1})
  self.assertEqual(code,409)
  code,h=dispatch(self.f,"GET","/feedbacks/f1/history")
  self.assertEqual((code,len(h)),(200,2))
  code,_=dispatch(self.f,"POST","/feedbacks/f2/move",{"state":"reviewing","actor":"a","expected_version":1})
  self.assertEqual(code,404)
  code,_=dispatch(self.f,"POST","/feedbacks/f1/revise",{"actor":"a","expected_version":2})
  self.assertEqual(code,400)
if __name__=="__main__":unittest.main()
