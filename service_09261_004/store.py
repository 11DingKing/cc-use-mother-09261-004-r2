"""SQLite 追加式事件仓储。

事件与幂等记录在同一个事务里写入，要么都落盘要么都回滚；
UNIQUE(feedback_id, version) 兜底保证同一条反馈不会有两个当前版本，
即使并发写入或服务中断后恢复也一样。
"""
import json
import sqlite3

SCHEMA = """
CREATE TABLE IF NOT EXISTS events(
 seq INTEGER PRIMARY KEY AUTOINCREMENT,
 feedback_id TEXT NOT NULL,
 version INTEGER NOT NULL,
 kind TEXT NOT NULL,
 actor TEXT NOT NULL,
 payload TEXT NOT NULL,
 idempotency_key TEXT NOT NULL,
 at TEXT NOT NULL,
 UNIQUE(feedback_id,version)
);
CREATE TABLE IF NOT EXISTS idempotency(
 key TEXT PRIMARY KEY,
 response TEXT NOT NULL
);
"""


class StorageConflict(Exception):
    """唯一约束冲突：重复版本号或重复幂等键。"""


class SQLiteStore:
    def __init__(self, path=":memory:"):
        self.db = sqlite3.connect(path, check_same_thread=False)
        self.db.executescript(SCHEMA)

    def load_events(self):
        rows = self.db.execute(
            "SELECT feedback_id,version,kind,actor,payload,idempotency_key,at"
            " FROM events ORDER BY seq").fetchall()
        return [{"feedback_id": r[0], "version": r[1], "kind": r[2], "actor": r[3],
                 "payload": json.loads(r[4]), "idempotency_key": r[5], "at": r[6]}
                for r in rows]

    def find(self, key):
        row = self.db.execute("SELECT response FROM idempotency WHERE key=?",
                              (key,)).fetchone()
        return json.loads(row[0]) if row else None

    def append(self, event, key, response):
        """单事务写入事件与幂等记录；违反唯一约束时抛 StorageConflict。"""
        try:
            with self.db:
                self.db.execute(
                    "INSERT INTO events(feedback_id,version,kind,actor,payload,"
                    "idempotency_key,at) VALUES(?,?,?,?,?,?,?)",
                    (event["feedback_id"], event["version"], event["kind"],
                     event["actor"], json.dumps(event["payload"], ensure_ascii=False),
                     event["idempotency_key"], event["at"]))
                self.db.execute(
                    "INSERT INTO idempotency(key,response) VALUES(?,?)",
                    (key, json.dumps(response, ensure_ascii=False)))
        except sqlite3.IntegrityError as exc:
            raise StorageConflict(str(exc))
