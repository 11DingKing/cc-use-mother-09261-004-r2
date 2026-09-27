"""JSON API 适配器：把 method+path+body 翻译成领域命令并统一错误码。"""
from .workflow import ConflictError, NotFoundError, ValidationError


def dispatch(service, method, path, body=None):
    body = body or {}
    parts = [p for p in path.split("/") if p]
    try:
        if method == "POST" and parts == ["feedbacks"]:
            return 201, service.create(body.get("id", ""), body.get("reporter", ""),
                                       body.get("title", ""), body.get("detail", ""),
                                       body.get("assignee", ""), body.get("deadline", ""),
                                       body.get("idempotency_key", ""))
        if method == "GET" and parts == ["feedbacks"]:
            return 200, service.snapshot()
        if len(parts) >= 2 and parts[0] == "feedbacks":
            fid = parts[1]
            if method == "GET" and len(parts) == 2:
                return 200, service.current(fid)
            if method == "GET" and len(parts) == 3 and parts[2] == "history":
                return 200, service.history(fid)
            if method == "POST" and len(parts) == 3 and parts[2] == "revisions":
                return 200, service.revise(fid, body.get("base_version"),
                                           body.get("actor", ""), body.get("title", ""),
                                           body.get("detail", ""), body.get("assignee", ""),
                                           body.get("deadline", ""),
                                           body.get("idempotency_key", ""))
            if method == "POST" and len(parts) == 3 and parts[2] == "retraction":
                return 200, service.retract(fid, body.get("base_version"),
                                            body.get("actor", ""), body.get("reason", ""),
                                            body.get("idempotency_key", ""))
        return 404, {"error": "not_found"}
    except ValidationError as exc:
        return 422, {"error": "invalid", "message": str(exc)}
    except NotFoundError:
        return 404, {"error": "not_found"}
    except ConflictError as exc:
        return 409, {"error": "conflict", "message": str(exc)}
