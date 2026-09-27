"""JSON API 适配器。"""
from dataclasses import asdict
from .workflow import Conflict,Unknown
def dispatch(flow,method,path,body=None):
 body=body or {}
 try:
  p=path.strip("/").split("/")
  if method=="POST" and p==["feedbacks"]:
   return 201,asdict(flow.submit(body["id"],body["actor"],body["owner"],body["due"],body.get("detail",""),body.get("idempotency_key")))
  if method=="GET" and p==["feedbacks"]:return 200,flow.snapshot()
  if len(p)==3 and p[0]=="feedbacks":
   id,act=p[1],p[2];k=body.get("idempotency_key")
   if method=="GET" and act=="history":return 200,flow.history(id)
   if method=="POST" and act=="revise":return 200,asdict(flow.revise(id,body["actor"],body["detail"],body["expected_version"],k))
   if method=="POST" and act=="reassign":return 200,asdict(flow.reassign(id,body["actor"],body["owner"],body["due"],body["expected_version"],k))
   if method=="POST" and act=="move":return 200,asdict(flow.move(id,body["state"],body["actor"],body["expected_version"],k))
   if method=="POST" and act=="withdraw":return 200,asdict(flow.withdraw(id,body["actor"],body.get("reason",""),body["expected_version"],k))
  return 404,{"error":"not_found"}
 except Unknown:return 404,{"error":"unknown_feedback"}
 except Conflict as e:return 409,{"error":"conflict","detail":str(e)}
 except KeyError as e:return 400,{"error":"missing_field","field":e.args[0]}
 except ValueError as e:return 422,{"error":str(e)}
