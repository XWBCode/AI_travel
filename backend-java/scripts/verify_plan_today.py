"""当日执行视图的端到端验证（真实 HTTP + 真实落库 + 真实模型生成）。

这一块验的不是「接口通了」，而是「今天到底对应第几天」这件事在四种情况下都说得清：
1. 没有出发日 → UNANCHORED，不猜第 1 天
2. 开始旅行时锚定日期 → startDate/endDate/每天日期一起回填
3. 未出发 / 行程中 / 已超期 → BEFORE / TODAY / AFTER，并给出差值
4. 当天打勾后，当日进度与整趟进度同时变化

用法：python backend-java/scripts/verify_plan_today.py
前提：AI 服务(8000)、Java 后端(8080) 已启动；真实模型生成一份计划约需半分钟。
"""

from __future__ import annotations

import datetime as dt
import json
import sys
import time
import urllib.error
import urllib.request

BASE = "http://localhost:8080/api"
failures: list[str] = []


def call(method: str, path: str, body=None, token: str = ""):
    data = json.dumps(body, ensure_ascii=False).encode() if body is not None else None
    req = urllib.request.Request(BASE + path, data=data, method=method)
    req.add_header("Content-Type", "application/json")
    if token:
        req.add_header("Authorization", "Bearer " + token)
    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            return resp.status, json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        raw = e.read().decode()
        try:
            return e.code, json.loads(raw)
        except json.JSONDecodeError:
            return e.code, {"message": raw[:200]}


def check(name: str, ok: bool, detail=""):
    if ok:
        print(f"PASS {name}")
    else:
        failures.append(name)
        print(f"FAIL {name} {detail}")


def new_user(prefix: str) -> str:
    stamp = str(int(time.time() * 1000))[-9:]
    username = prefix + stamp
    call("POST", "/auth/register",
         {"username": username, "password": "today1234", "nickname": "当日验证"})
    _, body = call("POST", "/auth/login", {"username": username, "password": "today1234"})
    return body["data"]["accessToken"]


def make_plan(token: str) -> str:
    status, body = call("POST", "/plans/generate",
                        {"messages": [{"role": "user", "content": "帮我规划成都三天，2个人，预算3000"}]},
                        token)
    if status != 200:
        print(f"生成计划失败：{body}")
        sys.exit(2)
    return body["data"]["planId"]


def plus(iso: str, days: int) -> str:
    return (dt.date.fromisoformat(iso) + dt.timedelta(days=days)).isoformat()


def main() -> int:
    token = new_user("today")
    plan_id = make_plan(token)
    _, detail = call("GET", f"/plans/{plan_id}", token=token)
    basic = detail["data"]["basic"]
    days = basic.get("days") or len(detail["data"].get("days") or [])
    print(f"计划 {plan_id} 已生成：{days} 天，出发日 {basic.get('startDate')}")

    # 0) 缺 date 参数：服务端要兜底，不能 500
    status, fallback = call("GET", f"/plans/{plan_id}/today", token=token)
    check("缺 date 参数不报错", status == 200 and fallback.get("code") == 0, fallback)
    check("兜底也回传了计算日期", bool(fallback["data"].get("date")), fallback["data"])

    # 1) 草稿 + 无出发日 → UNANCHORED（AI 若给了日期，则走 ELSE 分支验相位）
    start_date = basic.get("startDate")
    if not start_date:
        _, today = call("GET", f"/plans/{plan_id}/today?date=2026-12-01", token=token)
        data = today["data"]
        check("无出发日返回 UNANCHORED", data["phase"] == "UNANCHORED", data)
        check("无出发日不给天数", data.get("dayIndex") is None, data)
        check("无出发日不给卡片", data.get("day") is None, data)
        check("无出发日仍能显示整趟项数", data["total"] > 0, data)
        anchor = "2026-12-01"
    else:
        anchor = start_date

    call("POST", f"/plans/{plan_id}/confirm", token=token)

    # 2) 开始旅行：显式给出发日 → 回填并整体对齐
    status, started = call("POST", f"/plans/{plan_id}/start", {"startDate": anchor}, token)
    check("开始行程成功", status == 200 and started.get("code") == 0, started)

    _, detail = call("GET", f"/plans/{plan_id}", token=token)
    basic = detail["data"]["basic"]
    check("startDate 已回填", basic.get("startDate") == anchor, basic)
    check("endDate 按天数算出", basic.get("endDate") == plus(anchor, days - 1), basic)
    day_dates = [d.get("date") for d in detail["data"]["days"]]
    check("每天日期按出发日重算",
          day_dates == [plus(anchor, i) for i in range(days)], day_dates)

    # 3) 三种相位
    _, mid = call("GET", f"/plans/{plan_id}/today?date={plus(anchor, 1)}", token=token)
    mid = mid["data"]
    check("行程中：第 2 天", mid["phase"] == "TODAY" and mid["dayIndex"] == 2, mid)
    check("行程中：卡片日期是当天", mid["day"]["date"] == plus(anchor, 1), mid["day"])
    check("行程中：回传计算日期", mid["date"] == plus(anchor, 1), mid)

    _, before = call("GET", f"/plans/{plan_id}/today?date={plus(anchor, -2)}", token=token)
    before = before["data"]
    check("未出发：BEFORE + 距出发 2 天",
          before["phase"] == "BEFORE" and before["daysUntilStart"] == 2, before)
    check("未出发：预览第 1 天", before["dayIndex"] == 1, before)

    _, after = call("GET", f"/plans/{plan_id}/today?date={plus(anchor, days + 1)}", token=token)
    after = after["data"]
    check("超期：AFTER + 超期 2 天",
          after["phase"] == "AFTER" and after["daysSinceEnd"] == 2, after)
    check("超期：停在最后一天", after["dayIndex"] == days, after)

    # 4) 当天打勾：当日与整趟同时变化
    _, day1 = call("GET", f"/plans/{plan_id}/today?date={anchor}", token=token)
    day1 = day1["data"]
    check("出发当天是第 1 天", day1["phase"] == "TODAY" and day1["dayIndex"] == 1, day1)
    items = day1["day"]["items"]
    if items:
        item_id = items[0]["itemId"]
        check("当日第一项被标为「下一项」", items[0]["next"] is True, items[0])
        _, marked = call("POST", f"/plans/{plan_id}/progress",
                         {"itemId": item_id, "completed": True}, token)
        check("打勾成功", marked.get("code") == 0, marked)
        _, day1_after = call("GET", f"/plans/{plan_id}/today?date={anchor}", token=token)
        got = day1_after["data"]
        check("当日完成数 +1", got["day"]["completed"] == day1["day"]["completed"] + 1, got["day"])
        check("整趟完成数 +1", got["completed"] == day1["completed"] + 1, got)
        check("百分比同步", got["percent"] > day1["percent"], [day1["percent"], got["percent"]])
    else:
        check("第 1 天有行程项", False, day1)

    # 5) 越权：另一个人看不到别人的「今天」
    other = new_user("todayb")
    _, forbidden = call("GET", f"/plans/{plan_id}/today?date={anchor}", token=other)
    # 20003 = 无权访问该计划（归属校验统一返回「不存在或无权」，不暴露计划是否存在）
    check("越权被拒", forbidden.get("code") in (20003, 30101, 403), forbidden)

    # 6) 终态：没有「今天」
    call("POST", f"/plans/{plan_id}/complete", token=token)
    status, done = call("GET", f"/plans/{plan_id}/today?date={anchor}", token=token)
    check("已完成拒绝当日视图 30103", done.get("code") == 30103, done)

    print()
    if failures:
        print(f"{len(failures)} 项失败：{', '.join(failures)}")
        return 1
    print("全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
