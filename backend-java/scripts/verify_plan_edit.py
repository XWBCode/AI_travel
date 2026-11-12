"""PATCH /api/plans/{id} 的端到端验证（真实 HTTP，不 mock 后端）。

跑法：python scripts/verify_plan_edit.py [base_url]
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
        with urllib.request.urlopen(req, timeout=60) as resp:
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


stamp = str(int(time.time()))
user = "edit" + stamp[-8:]
call("POST", "/auth/register", {"username": user, "password": "edit12345", "nickname": "编辑验证"})
login = call("POST", "/auth/login", {"username": user, "password": "edit12345"})
token = (login.get("data") or {}).get("accessToken")
check("注册并登录拿到 token", bool(token), login)
if not token:
    sys.exit(1)

gen = call("POST", "/plans/generate",
           {"messages": [{"role": "user", "content": "帮我规划成都三天，2个人"}]}, token)
plan = gen.get("data") or {}
plan_id = plan.get("planId")
check("生成计划", gen.get("code") == 0 and bool(plan_id), gen)
if not plan_id:
    sys.exit(1)

print("  初始：title=%s days=%s start=%s end=%s version=%s" % (
    plan.get("title"), plan.get("basic", {}).get("days"),
    plan.get("basic", {}).get("startDate"), plan.get("basic", {}).get("endDate"),
    plan.get("version")))

# 1) 正常编辑：改标题 + 天数 3 → 5
before_days = plan.get("basic", {}).get("days")
before_start = plan.get("basic", {}).get("startDate")
resp = call("PATCH", "/plans/%s" % plan_id,
            {"title": "成都慢游五日", "days": 5, "baseVersion": plan.get("version")}, token)
edited = resp.get("data") or {}
check("PATCH 成功", resp.get("code") == 0, resp)
check("标题已改", edited.get("title") == "成都慢游五日", edited.get("title"))
check("天数已改", edited.get("basic", {}).get("days") == 5, edited.get("basic", {}).get("days"))
check("version 自增到 1", edited.get("version") == 1, edited.get("version"))
if before_start:
    from datetime import date, timedelta
    y, m, d = [int(x) for x in before_start.split("-")]
    expected_end = (date(y, m, d) + timedelta(days=4)).isoformat()
    check("结束日期跟着天数走", edited.get("basic", {}).get("endDate") == expected_end,
          "%s != %s" % (edited.get("basic", {}).get("endDate"), expected_end))
print("  编辑后：title=%s days=%s start=%s end=%s version=%s" % (
    edited.get("title"), edited.get("basic", {}).get("days"),
    edited.get("basic", {}).get("startDate"), edited.get("basic", {}).get("endDate"),
    edited.get("version")))

# 2) 旧版本再提交：必须冲突
stale = call("PATCH", "/plans/%s" % plan_id, {"title": "过期版本", "baseVersion": 0}, token)
check("旧 baseVersion 被拒（30104）", stale.get("code") == 30104, stale)
detail = call("GET", "/plans/%s" % plan_id, None, token)
check("冲突请求没改动数据", (detail.get("data") or {}).get("title") == "成都慢游五日",
      (detail.get("data") or {}).get("title"))

# 2b) 日期联动：给出发日期 + 天数，结束日期必须自动推出来
gen2 = call("POST", "/plans/generate",
            {"messages": [{"role": "user", "content": "帮我规划西安三天，2个人"}]}, token)
plan2_id = (gen2.get("data") or {}).get("planId")
r2 = call("PATCH", "/plans/%s" % plan2_id,
          {"startDate": "2026-12-01", "days": 3, "baseVersion": 0}, token)
e2 = r2.get("data") or {}
check("给日期+天数：结束日期自动推导", e2.get("basic", {}).get("endDate") == "2026-12-03",
      e2.get("basic", {}).get("endDate"))

# 2c) 反过来：只给日期区间，天数由区间反推
r3 = call("PATCH", "/plans/%s" % plan2_id,
          {"startDate": "2026-12-01", "endDate": "2026-12-06", "baseVersion": e2.get("version")}, token)
e3 = r3.get("data") or {}
check("给日期区间：天数反推为 6", e3.get("basic", {}).get("days") == 6,
      e3.get("basic", {}).get("days"))

# 3) 无变更保存：不产生新版本
noop = call("PATCH", "/plans/%s" % plan_id, {"title": "成都慢游五日", "baseVersion": 1}, token)
check("无变更仍返回成功", noop.get("code") == 0, noop)
check("无变更不推版本", (noop.get("data") or {}).get("version") == 1, (noop.get("data") or {}).get("version"))

# 4) 版本快照
versions = call("GET", "/plans/%s/versions" % plan_id, None, token).get("data") or []
check("写入了改前与改后两份快照", len(versions) == 2, [v.get("versionNo") for v in versions])
print("  版本：", [(v.get("versionNo"), v.get("changeSummary")) for v in versions])

# 5) 确认后不可编辑
call("POST", "/plans/%s/confirm" % plan_id, None, token)
locked = call("PATCH", "/plans/%s" % plan_id, {"title": "确认后再改"}, token)
check("已确认计划不可编辑（30103）", locked.get("code") == 30103, locked)

# 6) 字段校验
bad = call("PATCH", "/plans/%s" % plan_id, {"unknownField": 1}, token)
check("未知字段被拒", bad.get("code") == 10001, bad)

print("\n" + ("全部通过" if ok_all else "有失败项"))
sys.exit(0 if ok_all else 1)
