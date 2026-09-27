"""SQLite 追加式事件仓储：只插不改，崩溃后按日志重放。"""
import sqlite3,json
COLS="case_id,seq,type,actor,body,ts,ikey"
class SQLiteStore:
 def __init__(self,path=":memory:"):
  self.db=sqlite3.connect(path,check_same_thread=False)
  self.db.execute("CREATE TABLE IF NOT EXISTS events(case_id TEXT NOT NULL,seq INTEGER NOT NULL,type TEXT NOT NULL,actor TEXT NOT NULL,body TEXT NOT NULL,ts TEXT NOT NULL,ikey TEXT,PRIMARY KEY(case_id,seq))")
  self.db.execute("CREATE UNIQUE INDEX IF NOT EXISTS events_ikey ON events(ikey) WHERE ikey IS NOT NULL")
  self.db.commit()
 def append(self,ev):
  """单条插入即一个事务；重复 (case_id,seq) 或幂等键会抛 IntegrityError。"""
  with self.db:self.db.execute(f"INSERT INTO events({COLS}) VALUES(?,?,?,?,?,?,?)",(ev.case,ev.seq,ev.type,ev.actor,json.dumps(ev.body,ensure_ascii=False),ev.ts,ev.key))
 def events(self,case=None):
  q=f"SELECT {COLS} FROM events"+(" WHERE case_id=?" if case else "")+" ORDER BY case_id,seq"
  rows=self.db.execute(q,(case,) if case else ()).fetchall()
  return [{"case":c,"seq":s,"type":t,"actor":a,"body":json.loads(b),"ts":ts,"key":k} for c,s,t,a,b,ts,k in rows]
