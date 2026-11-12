"""POST /api/plans/{id}/versions/{versionNo}/rollback 的端到端验证（真实 HTTP + 真实落库）。

跑法：python scripts/verify_plan_rollback.py [base_url]

为什么值得单独跑一遍而不只靠 JUnit：回滚会重建 5 张子表并清进度，
单测里看到的是 Service 返回值，这里看到的是「接口 + 序列化 + 详情回读」整条链路，
雪花 ID 精度、快照 JSON 被反序列化成 DTO 之类的坑只有这条链路才暴露。
"""
import json
import sys
import time
import urllib.error
import urllib.request

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://localhost:8080/api"
ok_all = True


def call(method, path, body=None, token=None):
    data = None if body is None else json.dumps(body, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(BASE + path, data=data, method=method)
    req.add_header("Content-Type", "application/json")
    if token:
        req.add_header("Authorization", "Bearer " + token)
    try:
        with urllib.request.urlopen(req, timeout=240) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        raw = e.read().decode("utf-8")
        try:
            return json.loads(raw)
        except Exception:
            return {"code": -1, "message": raw, "data": None}


def check(name, cond, detail=""):
    global ok_all
    if not cond:
        ok_all = False
    print(("PASS " if cond else "FAIL ") + name + ("" if cond else " -> " + str(detail)))


def shape(plan):
    """内容指纹：标题 + 天数 + 每天条目数。回滚后必须一模一样。"""
    days = plan.get("days") or []
    return {
        "title": plan.get("title"),
        "days": (plan.get("basic") or {}).get("days"),
        "items": [len(d.get("items") or []) for d in days],
        "dayCount": len(days),
    }


stamp = str(int(time.time()))
user = "rb" + stamp[-8:]
call("POST", "/auth/register", {"username": user, "password": "rb123456", "nickname": "回滚验证"})
login = call("POST", "/auth/login", {"username": user, "password": "rb123456"})
token = (login.get("data") or {}).get("accessToken")
check("注册并登录拿到 token", bool(token), login)
if not token:
    sys.exit(1)

# 1) 生成：真实模型，约 30-40s
gen = call("POST", "/plans/generate",
           {"messages": [{"role": "user", "content": "帮我规划成都三天，2个人，预算3000"}]}, token)
plan = gen.get("data") or {}
plan_id = plan.get("planId")
check("生成计划", gen.get("code") == 0 and bool(plan_id), gen)
if not plan_id:
    sys.exit(1)

v0 = shape(plan)
print("  v0：%s" % json.dumps(v0, ensure_ascii=False))

versions = (call("GET", "/plans/%s/versions" % plan_id, token=token).get("data") or [])
check("生成时就写了 v0 快照（AI 原稿可回滚）",
      any(v["versionNo"] == 0 for v in versions), versions)

# 2) 编辑成 5 天，制造一个可回滚的差异
edit = call("PATCH", "/plans/%s" % plan_id,
            {"title": "改过的标题", "days": 5, "baseVersion": 0}, token)
edited = edit.get("data") or {}
check("编辑成功且 version 推到 1", edited.get("version") == 1, edit)
check("编辑后天数变 5", (edited.get("basic") or {}).get("days") == 5, edited)
v1 = shape(edited)

# 3) 回滚到 v0
rb = call("POST", "/plans/%s/versions/0/rollback" % plan_id, token=token)
result = rb.get("data") or {}
check("回滚接口成功", rb.get("code") == 0, rb)
check("回滚后 version 继续递增到 2", result.get("version") == 2, rb)
check("返回 restoredFrom=0", result.get("restoredFrom") == 0, rb)
check("草稿回滚不涉及进度", result.get("clearedProgress") == 0, rb)

back = shape(result.get("plan") or {})
check("回滚后内容与 v0 完全一致", back == v0, {"now": back, "v0": v0})

# 4) 详情回读：确认真的写进库了，而不只是接口返回好看
detail = call("GET", "/plans/%s" % plan_id, token=token).get("data") or {}
check("详情回读内容与 v0 一致", shape(detail) == v0, {"detail": shape(detail), "v0": v0})
check("详情里 version=2", detail.get("version") == 2, detail)
check("状态仍是 DRAFT（回滚不改状态）", detail.get("status") == "DRAFT", detail)

# 5) 版本列表：回滚也要留痕
versions = (call("GET", "/plans/%s/versions" % plan_id, token=token).get("data") or [])
summary_v2 = next((v.get("changeSummary") for v in versions if v["versionNo"] == 2), None)
check("v2 的变更说明是「回滚到 v0」", summary_v2 == "回滚到 v0", versions)

# 6) 再回滚到 v1：验证「回滚前的内容」确实被保住了
rb2 = call("POST", "/plans/%s/versions/1/rollback" % plan_id, token=token)
again = shape((rb2.get("data") or {}).get("plan") or {})
check("回滚到 v1 恢复编辑后的内容", again == v1, {"now": again, "v1": v1})

# 7) 连续回滚同一版本：子表物理删除才做得到（逻辑删除会撞 uk_plan_day）
rb3 = call("POST", "/plans/%s/versions/0/rollback" % plan_id, token=token)
rb4 = call("POST", "/plans/%s/versions/0/rollback" % plan_id, token=token)
check("连续两次回滚同一版本都成功", rb3.get("code") == 0 and rb4.get("code") == 0, [rb3, rb4])
check("连续回滚后内容仍是 v0", shape((rb4.get("data") or {}).get("plan") or {}) == v0, rb4)

# 8) 非法目标
missing = call("POST", "/plans/%s/versions/99/rollback" % plan_id, token=token)
check("不存在的版本 → 30106", missing.get("code") == 30106, missing)

current_version = ((call("GET", "/plans/%s" % plan_id, token=token).get("data") or {}).get("version"))
same = call("POST", "/plans/%s/versions/%s/rollback" % (plan_id, current_version), token=token)
check("回滚到当前版本 → 10001", same.get("code") == 10001, same)

# 9) 越权
other = "rbo" + stamp[-7:]
call("POST", "/auth/register", {"username": other, "password": "rb123456", "nickname": "他人"})
other_token = (call("POST", "/auth/login", {"username": other, "password": "rb123456"})
               .get("data") or {}).get("accessToken")
forbidden = call("POST", "/plans/%s/versions/0/rollback" % plan_id, token=other_token)
check("他人回滚 → 20003", forbidden.get("code") == 20003, forbidden)

# 10) 终态不可回滚
call("POST", "/plans/%s/confirm" % plan_id, token=token)
call("POST", "/plans/%s/start" % plan_id, token=token)
call("POST", "/plans/%s/complete" % plan_id, token=token)
done = call("POST", "/plans/%s/versions/0/rollback" % plan_id, token=token)
check("已完成的计划回滚 → 30103", done.get("code") == 30103, done)

print("\n" + ("全部通过" if ok_all else "存在失败项"))
print("planId=%s user=%s" % (plan_id, user))
sys.exit(0 if ok_all else 1)
