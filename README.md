# 课堂反馈闭环

教材反馈跟踪服务，纯 Python 服务端基础项目。每条反馈指定明确负责人与有效期限；
追加式事件日志完整保留每次修订；撤回只追加一条撤回记录，原始反馈永不删除。

一致性保障（同一条反馈不会出现两个当前版本）：

- **重复提交**：所有写命令必须携带幂等键，命中即返回首次结果，不再变更；
- **越过处理顺序**：修订/撤回必须携带 `base_version`，与当前版本不符即拒绝（409）；
- **服务中断后恢复**：事件与幂等记录在同一 SQLite 事务落盘，重启后从日志重建状态，
  重试命中幂等记录；`UNIQUE(feedback_id, version)` 兜底拦截并发分叉。

## 接口

- `POST /feedbacks` — 新建反馈（`id`、`reporter`、`title`、`detail`、`assignee`、`deadline`、`idempotency_key`）
- `POST /feedbacks/{id}/revisions` — 提交修订（`base_version` 必须等于当前版本；负责人与有效期限随版本更新）
- `POST /feedbacks/{id}/retraction` — 追加撤回记录（`reason` 必填；撤回后该反馈只读）
- `GET /feedbacks` — 全部反馈的当前视图
- `GET /feedbacks/{id}` — 单条反馈当前视图
- `GET /feedbacks/{id}/history` — 完整修订与撤回历史

错误码：404 不存在 / 409 版本冲突或已撤回 / 422 参数不合法。

测试命令：python3 -m unittest discover -s tests -v

编译命令：python3 -m compileall -q service_09261_004 tests
