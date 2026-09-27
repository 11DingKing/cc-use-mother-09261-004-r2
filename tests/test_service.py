import os
import tempfile
import unittest

from service_09261_004.api import dispatch
from service_09261_004.store import SQLiteStore
from service_09261_004.workflow import (ConflictError, FeedbackService,
                                        NotFoundError, ValidationError)


def make_service(path=":memory:"):
    return FeedbackService(SQLiteStore(path))


def draft(**overrides):
    base = dict(feedback_id="f1", reporter="teacher-li",
                title="三年级语文上册 P12 错别字", detail="“陶冶”误印为“陶治”",
                assignee="editor-wang", deadline="2026-10-15",
                idempotency_key="k-create")
    base.update(overrides)
    return base


class TestCreate(unittest.TestCase):
    def test_create_assigns_owner_and_deadline(self):
        svc = make_service()
        view = svc.create(**draft())
        self.assertEqual(view["version"], 1)
        self.assertEqual(view["reporter"], "teacher-li")
        self.assertEqual(view["assignee"], "editor-wang")
        self.assertEqual(view["deadline"], "2026-10-15")
        self.assertFalse(view["retracted"])

    def test_create_requires_owner_deadline_and_key(self):
        svc = make_service()
        with self.assertRaises(ValidationError):
            svc.create(**draft(assignee=""))
        with self.assertRaises(ValidationError):
            svc.create(**draft(deadline="下个月"))
        with self.assertRaises(ValidationError):
            svc.create(**draft(idempotency_key=""))
        self.assertEqual(svc.snapshot(), [])  # 校验失败不落任何数据


class TestRevisions(unittest.TestCase):
    def test_every_revision_is_kept(self):
        svc = make_service()
        svc.create(**draft())
        svc.revise("f1", 1, "editor-wang", "三年级语文上册 P12 错别字", "已核对原文",
                   "editor-chen", "2026-11-01", "k-rev-2")
        svc.revise("f1", 2, "editor-chen", "三年级语文上册 P12/P30 错别字",
                   "新增 P30 一处", "editor-chen", "2026-11-20", "k-rev-3")
        history = svc.history("f1")
        self.assertEqual([h["version"] for h in history], [1, 2, 3])
        self.assertEqual([h["kind"] for h in history],
                         ["created", "revised", "revised"])
        self.assertEqual(history[0]["assignee"], "editor-wang")  # 旧版本原样保留
        self.assertEqual(history[0]["deadline"], "2026-10-15")
        current = svc.current("f1")
        self.assertEqual(current["version"], 3)
        self.assertEqual(current["assignee"], "editor-chen")
        self.assertEqual(current["deadline"], "2026-11-20")

    def test_out_of_order_revision_rejected(self):
        svc = make_service()
        svc.create(**draft())
        svc.revise("f1", 1, "editor-wang", "t", "d", "editor-chen",
                   "2026-11-01", "k-rev-2")
        with self.assertRaises(ConflictError):  # 基于旧版本 v1 的迟到修订
            svc.revise("f1", 1, "editor-wang", "t2", "d2", "editor-zhao",
                       "2026-12-01", "k-rev-late")
        self.assertEqual(svc.current("f1")["version"], 2)
        self.assertEqual(len(svc.history("f1")), 2)

    def test_unknown_feedback(self):
        svc = make_service()
        with self.assertRaises(NotFoundError):
            svc.current("nope")
        with self.assertRaises(NotFoundError):
            svc.revise("nope", 1, "a", "t", "d", "b", "2026-10-01", "k-x")


class TestRetraction(unittest.TestCase):
    def test_retract_appends_record_and_keeps_original(self):
        svc = make_service()
        svc.create(**draft())
        svc.revise("f1", 1, "editor-wang", "t", "d", "editor-chen",
                   "2026-11-01", "k-rev-2")
        view = svc.retract("f1", 2, "teaching-researcher",
                           "与最新课标不符，撤回重报", "k-retract")
        self.assertTrue(view["retracted"])
        self.assertEqual(view["version"], 3)
        history = svc.history("f1")
        self.assertEqual([h["kind"] for h in history],
                         ["created", "revised", "retracted"])
        self.assertEqual(history[-1]["reason"], "与最新课标不符，撤回重报")
        self.assertEqual(history[0]["title"], "三年级语文上册 P12 错别字")
        self.assertEqual(len(svc.snapshot()), 1)  # 撤回是追加记录，不是抹掉条目

    def test_retracted_feedback_is_readonly(self):
        svc = make_service()
        svc.create(**draft())
        svc.retract("f1", 1, "teaching-researcher", "重复反馈", "k-retract")
        with self.assertRaises(ConflictError):
            svc.revise("f1", 2, "a", "t", "d", "b", "2026-12-01", "k-rev-after")
        with self.assertRaises(ConflictError):
            svc.retract("f1", 2, "a", "再次撤回", "k-retract-again")

    def test_retract_requires_reason(self):
        svc = make_service()
        svc.create(**draft())
        with self.assertRaises(ValidationError):
            svc.retract("f1", 1, "a", "", "k-retract")


class TestIdempotency(unittest.TestCase):
    def test_duplicate_create_returns_same_version(self):
        svc = make_service()
        first = svc.create(**draft())
        second = svc.create(**draft(title="完全不同的标题"))  # 同键重复提交
        self.assertEqual(first, second)
        self.assertEqual(len(svc.snapshot()), 1)
        self.assertEqual(len(svc.history("f1")), 1)

    def test_duplicate_revision_adds_no_version(self):
        svc = make_service()
        svc.create(**draft())
        first = svc.revise("f1", 1, "a", "t", "d", "editor-chen",
                           "2026-11-01", "k-rev")
        second = svc.revise("f1", 1, "a", "t", "d", "editor-chen",
                            "2026-11-01", "k-rev")
        self.assertEqual(first, second)
        self.assertEqual(svc.current("f1")["version"], 2)
        self.assertEqual(len(svc.history("f1")), 2)

    def test_same_feedback_id_different_key_conflicts(self):
        svc = make_service()
        svc.create(**draft())
        with self.assertRaises(ConflictError):
            svc.create(**draft(idempotency_key="k-create-2"))


class TestRecovery(unittest.TestCase):
    def test_restart_recovers_state_and_dedupes_retry(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "events.db")
            svc1 = make_service(path)
            svc1.create(**draft())
            svc1.revise("f1", 1, "a", "t", "d", "editor-chen",
                        "2026-11-01", "k-rev")
            svc2 = make_service(path)  # 模拟服务中断后从同一文件恢复
            self.assertEqual(svc2.current("f1")["version"], 2)
            self.assertEqual(len(svc2.history("f1")), 2)
            # 崩溃前已提交但响应丢失 → 客户端用同一幂等键重试
            retried = svc2.create(**draft())
            self.assertEqual(retried["version"], 1)
            self.assertEqual(len(svc2.snapshot()), 1)
            self.assertEqual(len(svc2.history("f1")), 2)  # 没有第二个当前版本

    def test_concurrent_writers_cannot_fork_current_version(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "events.db")
            svc1 = make_service(path)
            svc1.create(**draft())
            svc2 = make_service(path)  # 另一实例，内存视图独立
            svc1.revise("f1", 1, "a", "t1", "d", "editor-chen",
                        "2026-11-01", "k-rev-1")
            with self.assertRaises(ConflictError):  # svc2 内存已过期，落库被唯一约束拦下
                svc2.revise("f1", 1, "b", "t2", "d", "editor-zhao",
                            "2026-12-01", "k-rev-2")
            # 冲突后以持久化日志为准重建：仍然只有一个当前版本 v2
            self.assertEqual([h["version"] for h in svc2.history("f1")], [1, 2])
            self.assertEqual(svc2.current("f1")["title"], "t1")


class TestAPI(unittest.TestCase):
    def setUp(self):
        self.svc = make_service()

    def post_feedback(self, **overrides):
        body = {"id": "f1", "reporter": "teacher-li", "title": "t", "detail": "d",
                "assignee": "editor-wang", "deadline": "2026-10-15",
                "idempotency_key": "k1"}
        body.update(overrides)
        return dispatch(self.svc, "POST", "/feedbacks", body)

    def test_create_and_query(self):
        status, view = self.post_feedback()
        self.assertEqual(status, 201)
        self.assertEqual(view["assignee"], "editor-wang")
        status, rows = dispatch(self.svc, "GET", "/feedbacks")
        self.assertEqual((status, len(rows)), (200, 1))
        status, view = dispatch(self.svc, "GET", "/feedbacks/f1")
        self.assertEqual((status, view["version"]), (200, 1))
        self.assertEqual(dispatch(self.svc, "GET", "/feedbacks/nope")[0], 404)

    def test_revise_and_retract_via_api(self):
        self.post_feedback()
        status, view = dispatch(self.svc, "POST", "/feedbacks/f1/revisions",
                                {"base_version": 1, "actor": "a", "title": "t2",
                                 "detail": "d2", "assignee": "editor-chen",
                                 "deadline": "2026-11-01", "idempotency_key": "k2"})
        self.assertEqual((status, view["version"]), (200, 2))
        status = dispatch(self.svc, "POST", "/feedbacks/f1/revisions",
                          {"base_version": 1, "actor": "a", "title": "t3",
                           "detail": "d3", "assignee": "editor-zhao",
                           "deadline": "2026-12-01", "idempotency_key": "k3"})[0]
        self.assertEqual(status, 409)  # 越过正常处理顺序
        status, view = dispatch(self.svc, "POST", "/feedbacks/f1/retraction",
                                {"base_version": 2, "actor": "b",
                                 "reason": "信息有误", "idempotency_key": "k4"})
        self.assertEqual((status, view["retracted"]), (200, True))
        history = dispatch(self.svc, "GET", "/feedbacks/f1/history")[1]
        self.assertEqual([h["kind"] for h in history],
                         ["created", "revised", "retracted"])

    def test_validation_and_unknown_route(self):
        self.assertEqual(self.post_feedback(assignee="")[0], 422)
        self.assertEqual(self.post_feedback(idempotency_key="")[0], 422)
        self.assertEqual(dispatch(self.svc, "DELETE", "/feedbacks/f1")[0], 404)


if __name__ == "__main__":
    unittest.main()
