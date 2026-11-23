"""天气展示的端到端验证（M3-1，真实 HTTP + 真实上游，不 mock）。

跑法：python scripts/verify_weather.py [base_url]

前提：
- Java(:8080) 与 Python(:8000) 都已启动
- Java 侧 ``WEATHER_PROVIDER`` 与 Python 侧 ``WEATHER_PROVIDER`` 都是 ``open-meteo``

验的是「HTTP 上能观察到的东西」，不重复单测已经覆盖的内部分支：
    W-1 有日期 + 已知城市 → 每天四组 FactField，source=TOOL、verified=true、带 fetchedAt
    W-3 无出发日期 → available=false + NO_START_DATE，且不返回任何数值
    W-4 缓存 → 二次请求 fetchedAt 不变、耗时显著下降（进程内缓存无法从外部数上游调用次数，
        所以只报耗时与 fetchedAt 一致性，**不声称"0 次上游调用"**）
    W-6 越权 → 别人的计划 403，且不泄露天气
    W-8 署名 → 响应含 attribution
    越窗口 → DATE_OUT_OF_RANGE 与 NO_START_DATE 是两条不同的提示

注意：计划生成的模型调用是真模型，单次可能 60–200s，脚本整体要几分钟。
"""

import json
import sys
import time
import urllib.error
import urllib.request

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://localhost:8080/api"
ok_all = True
stamp = str(int(time.time()))[-8:]


def call(method, path, body=None, token=None, timeout=300):
    data = None if body is None else json.dumps(body, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(BASE + path, data=data, method=method)
    req.add_header("Content-Type", "application/json")
    if token:
        req.add_header("Authorization", "Bearer " + token)
    started = time.time()
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
            return payload, resp.status, time.time() - started
    except urllib.error.HTTPError as e:
        raw = e.read().decode("utf-8")
        try:
            return json.loads(raw), e.code, time.time() - started
        except Exception:
            return {"code": -1, "message": raw, "data": None}, e.code, time.time() - started


def check(name, cond, detail=""):
    global ok_all
    if not cond:
        ok_all = False
    print(("PASS " if cond else "FAIL ") + name + ("" if cond else " -> " + str(detail)[:400]))


def fact_ok(field, name):
    """一个天气 FactField 的合规性：真值必须带来源与时间，且 display 非空"""
    if not isinstance(field, dict):
        check("%s 存在" % name, False, field)
        return False
    passed = (
        field.get("verified") is True
        and field.get("source") == "TOOL"
        and bool(field.get("fetchedAt"))
        and bool(field.get("display"))
    )
    check("%s 是已核实的真值（source=TOOL / 有 fetchedAt / display 非空）" % name, passed, field)
    return passed


def register_and_login(prefix, pwd):
    user = prefix + stamp
    call("POST", "/auth/register", {"username": user, "password": pwd, "nickname": "天气验证"})
    body, _, _ = call("POST", "/auth/login", {"username": user, "password": pwd})
    return user, (body.get("data") or {}).get("accessToken")


print("== 0. 准备用户与计划 ==")
user, token = register_and_login("wx", "weather12345")
check("注册并登录", bool(token))
if not token:
    sys.exit(1)

gen, _, gen_cost = call("POST", "/plans/generate",
                        {"messages": [{"role": "user", "content": "帮我规划成都三天，2个人"}]},
                        token=token)
plan = gen.get("data") or {}
plan_id = plan.get("planId")
check("生成计划（耗时 %.1fs）" % gen_cost, gen.get("code") == 0 and bool(plan_id), gen)
if not plan_id:
    sys.exit(1)
basic = plan.get("basic") or {}
print("  计划 %s：destination=%s days=%s startDate=%s"
      % (plan_id, basic.get("destination"), basic.get("days"), basic.get("startDate")))

print("\n== 1. W-3：计划可能没有出发日期 ==")
w, status, _ = call("GET", "/plans/%s/weather" % plan_id, token=token)
check("HTTP 200（降级不是错误）", status == 200, status)
if basic.get("startDate"):
    print("  SKIP 本次生成的计划自带出发日期，W-3 由单测覆盖（PlanWeatherServiceTest）")
else:
    check("available=false", w.get("data", {}).get("available") is False, w)
    check("reason=NO_START_DATE", w.get("data", {}).get("reason") == "NO_START_DATE", w)
    check("不返回任何天数（不拿今天顶替）", not (w.get("data", {}).get("days") or []), w)
    check("提示语可读", "日期" in (w.get("data", {}).get("message") or ""), w)

print("\n== 2. 补上出发日期后应能查到真实天气（W-1 / W-8）=")
patch, _, _ = call("PATCH", "/plans/%s" % plan_id,
                   {"startDate": "2026-11-24", "endDate": "2026-11-26",
                    "baseVersion": plan.get("version")}, token)
check("设置出发日期", patch.get("code") == 0, patch)
version = (patch.get("data") or {}).get("version", plan.get("version"))

w, status, first_cost = call("GET", "/plans/%s/weather" % plan_id, token=token)
data = w.get("data") or {}
check("available=true", data.get("available") is True, data)
check("W-8 响应含署名", "Open-Meteo" in (data.get("attribution") or ""), data.get("attribution"))
check("source=OPEN_METEO", data.get("source") == "OPEN_METEO", data.get("source"))
days = data.get("days") or []
check("按行程天数返回（3 天）", len(days) == 3, [d.get("date") for d in days])
if days:
    fact_ok(days[0].get("temperature"), "温度")
    fact_ok(days[0].get("precipitation"), "降水")
    fact_ok(days[0].get("wind"), "风")
    check("天气现象有中文描述", bool((days[0].get("weather") or {}).get("display")),
          days[0].get("weather"))
    check("dayIndex 从 1 开始且连续", [d.get("dayIndex") for d in days] == [1, 2, 3],
          [d.get("dayIndex") for d in days])
    check("日期与出发日对齐", (days[0].get("date") or "") == "2026-11-24", days[0].get("date"))
    print("  样例：%s %s %s %s %s" % (
        days[0].get("date"), (days[0].get("city") or ""),
        (days[0].get("weather") or {}).get("display"),
        (days[0].get("temperature") or {}).get("display"),
        (days[0].get("precipitation") or {}).get("display")))

print("\n== 3. W-4：缓存（二次请求 fetchedAt 不变、耗时下降）=")
w2, _, second_cost = call("GET", "/plans/%s/weather" % plan_id, token=token)
data2 = w2.get("data") or {}
check("二次请求 fetchedAt 未变（命中缓存）",
      data2.get("fetchedAt") == data.get("fetchedAt"),
      [data.get("fetchedAt"), data2.get("fetchedAt")])
check("二次请求更快：%.0fms → %.0fms" % (first_cost * 1000, second_cost * 1000),
      second_cost < first_cost, (first_cost, second_cost))
print("  说明：进程内缓存无法从外部数上游调用次数，只据此判断命中，不声称「0 次上游调用」")

print("\n== 4. 超出预报窗口：与「没日期」是两条不同提示 ==")
call("PATCH", "/plans/%s" % plan_id,
     {"startDate": "2027-06-01", "endDate": "2027-06-03", "baseVersion": version}, token)
w3, _, _ = call("GET", "/plans/%s/weather" % plan_id, token=token)
data3 = w3.get("data") or {}
check("available=false", data3.get("available") is False, data3)
check("reason=DATE_OUT_OF_RANGE", data3.get("reason") == "DATE_OUT_OF_RANGE", data3)
check("提示与 NO_START_DATE 不同", (data3.get("message") or "") != (data.get("message") or ""),
      data3.get("message"))

print("\n== 5. W-6：越权 ==")
_, other = register_and_login("wv", "weather12345")
resp, status, _ = call("GET", "/plans/%s/weather" % plan_id, token=other)
check("别人的计划返回 403", status == 403, (status, resp))
check("不泄露任何天气数据", not (resp.get("data") or {}).get("days"), resp)

print()
if ok_all:
    print("全部通过")
else:
    print("存在失败项")
    sys.exit(1)
