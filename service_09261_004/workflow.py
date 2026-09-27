"""教材反馈工作流：追加式事件日志 + 单一当前版本。"""
import sqlite3,threading
from dataclasses import dataclass,replace,asdict
from datetime import date,datetime,timezone
TERMINAL={"withdrawn","archived"}
ALLOWED={"draft":{"reviewing"},"reviewing":{"approved","rejected"},"rejected":{"draft"},"approved":{"archived"}}
class Conflict(Exception):pass
class Unknown(Exception):pass
def _now():return datetime.now(timezone.utc).isoformat()
def _check_due(due):
 try:date.fromisoformat(due)
 except Exception:raise ValueError("bad due date")
@dataclass(frozen=True)
class Event:
 case:str;seq:int;type:str;actor:str;body:dict;ts:str;key:str=None
@dataclass(frozen=True)
class Case:
 id:str;reporter:str;owner:str;due:str;state:str;version:int;detail:str=""
def fold(case,ev):
 """把一条事件折叠进当前状态；事件只能按 seq 顺序追加。"""
 if ev.type=="submitted":
  if ev.seq!=1 or case is not None:raise Conflict("version gap")
  return Case(ev.case,ev.actor,ev.body["owner"],ev.body["due"],"draft",1,ev.body.get("detail",""))
 if case is None or ev.seq!=case.version+1:raise Conflict("version gap")
 if ev.type=="moved":
  to=ev.body["state"]
  if to not in ALLOWED.get(case.state,set()):raise ValueError("invalid transition")
  return replace(case,state=to,version=ev.seq)
 if ev.type=="withdrawn":
  if case.state in TERMINAL:raise ValueError("invalid transition")
  return replace(case,state="withdrawn",version=ev.seq)
 if ev.type=="revised":
  if case.state in TERMINAL:raise ValueError("invalid transition")
  return replace(case,detail=ev.body["detail"],version=ev.seq)
 if ev.type=="reassigned":
  if case.state in TERMINAL:raise ValueError("invalid transition")
  return replace(case,owner=ev.body["owner"],due=ev.body["due"],version=ev.seq)
 raise ValueError("unknown event")
class Workflow:
 """命令入口：先过版本与流转检查，再把变更作为一条事件追加落库。"""
 def __init__(self,store):
  self.store=store;self.lock=threading.RLock();self.cases={};self.keys={}
  for d in store.events():
   ev=Event(d["case"],d["seq"],d["type"],d["actor"],d["body"],d["ts"],d["key"])
   self.cases[ev.case]=fold(self.cases.get(ev.case),ev)
   if ev.key:self.keys[ev.key]=ev.case
 def _append(self,ev,case):
  try:self.store.append(ev)
  except sqlite3.IntegrityError:raise Conflict("duplicate event")
  self.cases[ev.case]=case
  if ev.key:self.keys[ev.key]=ev.case
  return case
 def submit(self,id,actor,owner,due,detail="",key=None):
  """登记反馈并指定负责人与有效期限；幂等键重复时返回原反馈。"""
  with self.lock:
   if key and key in self.keys:return self.cases[self.keys[key]]
   if id in self.cases:raise ValueError("duplicate")
   if not owner:raise ValueError("owner required")
   _check_due(due)
   ev=Event(id,1,"submitted",actor,{"owner":owner,"due":due,"detail":detail},_now(),key)
   return self._append(ev,fold(None,ev))
 def _cmd(self,id,actor,type,body,expected,key):
  with self.lock:
   if key and key in self.keys:return self.cases[self.keys[key]]
   case=self.cases.get(id)
   if case is None:raise Unknown(id)
   if expected!=case.version:raise Conflict(f"expected v{expected}, current v{case.version}")
   ev=Event(id,case.version+1,type,actor,body,_now(),key)
   return self._append(ev,fold(case,ev))
 def revise(self,id,actor,detail,expected,key=None):return self._cmd(id,actor,"revised",{"detail":detail},expected,key)
 def reassign(self,id,actor,owner,due,expected,key=None):
  if not owner:raise ValueError("owner required")
  _check_due(due)
  return self._cmd(id,actor,"reassigned",{"owner":owner,"due":due},expected,key)
 def move(self,id,state,actor,expected,key=None):return self._cmd(id,actor,"moved",{"state":state},expected,key)
 def withdraw(self,id,actor,reason,expected,key=None):return self._cmd(id,actor,"withdrawn",{"reason":reason},expected,key)
 def snapshot(self):return [asdict(self.cases[k]) for k in sorted(self.cases)]
 def history(self,id):
  if id not in self.cases:raise Unknown(id)
  return self.store.events(id)
