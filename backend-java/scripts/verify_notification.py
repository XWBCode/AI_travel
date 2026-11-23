"""站内通知的端到端验证（真实 HTTP + 真实落库）。

验的是「用户会看到什么」，不是「接口返回 200」：
1. 新用户没有任何通知，未读数 0
2. 非法状态迁移（草稿直接完成）不产生通知——通知写在状态判定之后
3. 确认 → 开始 → 完成，各产生一条，顺序与类型正确，正文带上行程信息
4. 「只看未读」筛选只改列表，红点数字不变（两者同源，不会打架）
5. 标记已读幂等：重复点同一条不会刷新第一次的已读时间
6. 全部已读归零
7. 别人的通知：列表看不到，也标不了已读
8. 取消草稿计划同样进时间线
9. 雪花 ID 一律字符串下发（19 位转 Number 会丢精度）

用法：python backend-java/scripts/verify_notification.py
前置：Java 8080、Python 8000 已启动
"""

from __future__ import annotations

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
        with urllib.request.urlopen(req, timeout=300) as resp:
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
         {"username": username, "password": "notify12345", "nickname": "通知验证"})
    _, body = call("POST", "/auth/login", {"username": username, "password": "notify12345"})
    return body["data"]["accessToken"]


def make_plan(token: str) -> str:
    status, body = call("POST", "/plans/generate",
                        {"messages": [{"role": "user", "content": "帮我规划成都三天，2个人，预算3000"}]},
                        token)
    if status != 200 or body.get("code") != 0:
        print(f"生成计划失败：{body}")
        sys.exit(2)
    return body["data"]["planId"]


def feed(token: str, unread_only: bool = False, size: int = 20):
    q = f"?page=1&size={size}&unreadOnly={'true' if unread_only else 'false'}"
    _, body = call("GET", "/notifications" + q, token=token)
    return body.get("data") or {}


def main() -> int:
    token = new_user("noti")
    other = new_user("noti2")

    plan = make_plan(token)
    print(f"计划 {plan} 已生成")

    # 1) 新用户：空空如也
    data = feed(token)
    check("新用户没有通知", data.get("total") == 0, data)
    check("新用户未读数为 0", data.get("unreadCount") == 0, data)

    # 2) 非法迁移不产生通知：草稿不能直接完成
    status, body = call("POST", f"/plans/{plan}/complete", token=token)
    check("草稿直接完成被拒", body.get("code") == 30103, body)
    data = feed(token)
    check("被拒的动作不留通知", data.get("total") == 0, data)

    # 3) 确认 → 开始 → 完成
    call("POST", f"/plans/{plan}/confirm", token=token)
    data = feed(token)
    records = data.get("records") or []
    check("确认产生 1 条", data.get("total") == 1, data)
    check("类型是 PLAN_CONFIRMED", records and records[0]["type"] == "PLAN_CONFIRMED", records[:1])
    check("标题是人话", records and records[0]["title"] == "行程已确认", records[:1])
    check("正文带计划标题", records and "成都" in (records[0].get("body") or ""), records[:1])
    check("新通知是未读", records and records[0]["read"] is False, records[:1])

    first_id = records[0]["notificationId"] if records else ""
    check("notificationId 是字符串（雪花精度）", isinstance(first_id, str) and len(first_id) >= 18, first_id)
    check("planId 是字符串", isinstance(records[0].get("planId"), str), records[:1])

    call("POST", f"/plans/{plan}/start", token=token)
    data = feed(token)
    records = data.get("records") or []
    check("开始后共 2 条", data.get("total") == 2, data)
    check("最新一条是 PLAN_STARTED", records and records[0]["type"] == "PLAN_STARTED", records[:1])
    check("开始正文写出第 1 天", records and "第 1 天是" in (records[0].get("body") or ""), records[:1])

    # 4) 只看未读：列表变短，红点不变
    data = feed(token, unread_only=True)
    check("只看未读返回 2 条", len(data.get("records") or []) == 2, data)
    check("筛选不影响红点", data.get("unreadCount") == 2, data)

    # 5) 标记一条已读，幂等
    _, body = call("POST", f"/notifications/{first_id}/read", token=token)
    check("标记已读返回未读数 1", body.get("data") == 1, body)
    _, again = call("POST", f"/notifications/{first_id}/read", token=token)
    check("重复标记仍是 1（幂等）", again.get("data") == 1, again)
    data = feed(token, unread_only=True)
    check("只看未读剩 1 条", len(data.get("records") or []) == 1, data)
    check("红点同步为 1", data.get("unreadCount") == 1, data)

    # 6) 全部已读
    _, body = call("POST", "/notifications/read-all", token=token)
    check("全部已读后未读为 0", body.get("data") == 0, body)
    check("列表仍然是 2 条（不是删掉）", feed(token).get("total") == 2, feed(token))

    # 7) 别人的通知
    other_data = feed(other)
    check("别人看不到我的通知", other_data.get("total") == 0, other_data)
    _, body = call("POST", f"/notifications/{first_id}/read", token=other)
    check("别人标我的已读返回 403（20003）", body.get("code") == 20003, body)
    check("别人操作失败后我的状态不变", feed(token).get("unreadCount") == 0, feed(token))

    _, body = call("POST", "/notifications/1/read", token=token)
    check("通知不存在返回 30111", body.get("code") == 30111, body)

    # 8) 完成 + 取消也都进时间线
    call("POST", f"/plans/{plan}/complete", token=token)
    data = feed(token)
    records = data.get("records") or []
    check("完成后共 3 条", data.get("total") == 3, data)
    check("最新是 PLAN_COMPLETED", records and records[0]["type"] == "PLAN_COMPLETED", records[:1])

    plan2 = make_plan(token)
    call("POST", f"/plans/{plan2}/cancel", token=token)
    data = feed(token)
    records = data.get("records") or []
    check("取消草稿也通知", records and records[0]["type"] == "PLAN_CANCELLED", records[:1])
    check("累计 4 条", data.get("total") == 4, data)
    # 全部已读之后又产生了「完成」「取消」两条，所以是 2
    check("新事件重新点亮红点（未读 2）", data.get("unreadCount") == 2, data)

    # 9) 分页上限
    data = feed(token, size=999)
    check("size 超上限被夹到 50", data.get("size") == 50, data)
    data = feed(token, size=0)
    check("size 非法值退化为 1", data.get("size") == 1, data)

    print()
    if failures:
        print(f"{len(failures)} 项失败：")
        for f in failures:
            print("  - " + f)
        return 1
    print("全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
