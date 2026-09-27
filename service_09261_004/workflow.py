"""教材反馈领域核心：追加式事件日志驱动的版本化反馈。

每条反馈都有明确负责人与有效期限；每次修订生成一个新版本并完整保留；
撤回只追加一条撤回记录，原始反馈与历史版本永不删除。
所有命令必须携带幂等键：重复提交、乱序处理或崩溃后重试，
都不会让同一条反馈出现两个当前版本。
"""
import threading
from dataclasses import asdict, dataclass
from datetime import date, datetime, timezone

from .store import StorageConflict

CREATED, REVISED, RETRACTED = "created", "revised", "retracted"


class FeedbackError(Exception):
    """领域错误基类。"""


class NotFoundError(FeedbackError):
    """反馈不存在。"""


class ConflictError(FeedbackError):
    """版本冲突或状态不允许（重复建单、基版本过期、已撤回）。"""


class ValidationError(FeedbackError):
    """请求参数不合法。"""


def _now():
    return datetime.now(timezone.utc).isoformat()


def _require(value, message):
    if not value or not str(value).strip():
        raise ValidationError(message)
    return value


def _check_version_fields(title, assignee, deadline):
    _require(title, "title required")
    _require(assignee, "assignee required")
    try:
        date.fromisoformat(str(deadline or ""))
    except ValueError:
        raise ValidationError("deadline must be an ISO date (YYYY-MM-DD)")


@dataclass(frozen=True)
class Event:
    """不可变变更事件；version 是变更后的版本号，从 1 起逐条递增。"""
    feedback_id: str
    version: int
    kind: str
    actor: str
    payload: dict
    idempotency_key: str
    at: str


def _fold(events):
    """把一条反馈的全部事件折叠成当前视图；负责人与有效期限随版本走。"""
    view = None
    for ev in events:
        if ev.kind == CREATED:
            view = {"id": ev.feedback_id, "reporter": ev.actor,
                    "retracted": False, "retract_reason": None}
        if ev.kind in (CREATED, REVISED):
            view.update(title=ev.payload["title"], detail=ev.payload["detail"],
                        assignee=ev.payload["assignee"], deadline=ev.payload["deadline"])
        else:
            view.update(retracted=True, retract_reason=ev.payload["reason"])
        view["version"] = ev.version
    return view


class FeedbackService:
    """命令入口：校验 → 构造事件 → 原子持久化 → 更新内存视图。

    持久化日志是唯一事实来源；内存视图随时可由日志重建（崩溃恢复）。
    """

    def __init__(self, store):
        self.store = store
        self._lock = threading.Lock()
        self._log = {}
        self._reload()

    def _reload(self):
        self._log = {}
        for row in self.store.load_events():
            self._log.setdefault(row["feedback_id"], []).append(Event(**row))

    # ---- 查询 ----
    def _events_of(self, feedback_id):
        events = self._log.get(feedback_id)
        if not events:
            raise NotFoundError(feedback_id)
        return events

    def current(self, feedback_id):
        with self._lock:
            return _fold(self._events_of(feedback_id))

    def history(self, feedback_id):
        with self._lock:
            return [{"version": ev.version, "kind": ev.kind, "actor": ev.actor,
                     "at": ev.at, **ev.payload} for ev in self._events_of(feedback_id)]

    def snapshot(self):
        with self._lock:
            return [_fold(self._log[fid]) for fid in sorted(self._log)]

    # ---- 命令 ----
    def create(self, feedback_id, reporter, title, detail, assignee, deadline,
               idempotency_key):
        def build():
            _require(feedback_id, "id required")
            _require(reporter, "reporter required")
            _check_version_fields(title, assignee, deadline)
            if feedback_id in self._log:
                raise ConflictError("feedback already exists: %s" % feedback_id)
            return Event(feedback_id, 1, CREATED, reporter,
                         {"title": title, "detail": detail or "",
                          "assignee": assignee, "deadline": deadline},
                         idempotency_key, _now())
        return self._run("create", idempotency_key, build)

    def revise(self, feedback_id, base_version, actor, title, detail, assignee,
               deadline, idempotency_key):
        def build():
            _require(actor, "actor required")
            _check_version_fields(title, assignee, deadline)
            view = self._check_open(feedback_id, base_version)
            return Event(feedback_id, view["version"] + 1, REVISED, actor,
                         {"title": title, "detail": detail or "",
                          "assignee": assignee, "deadline": deadline},
                         idempotency_key, _now())
        return self._run("revise", idempotency_key, build)

    def retract(self, feedback_id, base_version, actor, reason, idempotency_key):
        def build():
            _require(actor, "actor required")
            _require(reason, "retract reason required")
            view = self._check_open(feedback_id, base_version)
            return Event(feedback_id, view["version"] + 1, RETRACTED, actor,
                         {"reason": reason}, idempotency_key, _now())
        return self._run("retract", idempotency_key, build)

    # ---- 内部 ----
    def _check_open(self, feedback_id, base_version):
        if not isinstance(base_version, int):
            raise ValidationError("base_version must be an integer")
        view = _fold(self._events_of(feedback_id))
        if view["retracted"]:
            raise ConflictError("feedback already retracted: %s" % feedback_id)
        if base_version != view["version"]:
            raise ConflictError("stale base_version: current is %d, got %r"
                                % (view["version"], base_version))
        return view

    def _run(self, op, idempotency_key, build):
        """幂等执行：命中幂等键直接返回首次结果；否则校验、写日志、更新视图。

        校验或冲突失败的命令不占用幂等键，客户端修正后可用原键重试。
        """
        _require(idempotency_key, "idempotency_key required")
        key = "%s:%s" % (op, idempotency_key)  # 幂等键按命令类型隔离
        with self._lock:
            hit = self.store.find(key)
            if hit is not None:
                return hit  # 重复提交或崩溃后重试：返回首次结果，不再变更
            event = build()
            view = _fold(self._log.get(event.feedback_id, []) + [event])
            try:
                self.store.append(asdict(event), key, view)
            except StorageConflict:
                # 唯一约束兜底：并发或恢复后状态已变，以持久化日志为准重建
                self._reload()
                hit = self.store.find(key)
                if hit is not None:
                    return hit
                raise ConflictError("concurrent update on feedback %s"
                                    % event.feedback_id)
            self._log.setdefault(event.feedback_id, []).append(event)
            return view
