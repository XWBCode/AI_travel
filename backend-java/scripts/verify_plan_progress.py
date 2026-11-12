"""执行进度接口的端到端验证（真实 HTTP + 真实落库）。

验的是「用户点一下会发生什么」，不是「接口返回 200」：
1. 草稿阶段打勾被拒（30103），且不留进度行
2. 确认 + 开始后能打勾，进度与 percentage 正确
3. 重复打勾幂等（完成数不翻倍）
4. 取消勾选回退，再次勾选仍能成功（唯一键与物理删除的配合）
5. 拿别人计划的行程项打勾被拒（30105）
6. 全部完成后标记完成，状态变 COMPLETED

用法：python backend-java/scripts/verify_plan_progress.py
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
        with urllib.request.urlopen(req, timeout=60) as resp:
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
         {"username": username, "password": "prog12345", "nickname": "进度验证"})
    _, body = call("POST", "/auth/login", {"username": username, "password": "prog12345"})
    return body["data"]["accessToken"]


def make_plan(token: str) -> str:
    status, body = call("POST", "/plans/generate",
                        {"messages": [{"role": "user", "content": "帮我规划成都三天，2个人，预算3000"}]},
                        token)
    if status != 200:
        print(f"生成计划失败：{body}")
        sys.exit(2)
    # 真实模型要 30-40s，这里只断言拿到了 planId
    return body["data"]["planId"]


def main() -> int:
    token = new_user("prog")
    plan_id = make_plan(token)
    print(f"计划 {plan_id} 已生成")

    # 1) 草稿阶段：进度可读但全 0，打勾被拒
    _, prog = call("GET", f"/plans/{plan_id}/progress", token=token)
    data = prog["data"]
    total = data["total"]
    check("草稿可读进度", prog["code"] == 0 and total > 0, prog)
    check("草稿初始完成数为 0", data["completed"] == 0, data)
    first_item = data["days"][0]["items"][0]["itemId"]

    status, rejected = call("POST", f"/plans/{plan_id}/progress",
                            {"itemId": first_item, "completed": True}, token)
    check("草稿打勾被拒 30103", rejected.get("code") == 30103, rejected)

    _, prog = call("GET", f"/plans/{plan_id}/progress", token=token)
    check("被拒后不留进度", prog["data"]["completed"] == 0, prog["data"])

    # 2) 确认 → 开始
    for action in ("confirm", "start"):
        status, body = call("POST", f"/plans/{plan_id}/{action}", token=token)
        check(f"{action} 成功", body.get("code") == 0, body)

    _, prog = call("GET", f"/plans/{plan_id}/progress", token=token)
    check("进行中状态", prog["data"]["status"] == "IN_PROGRESS", prog["data"]["status"])
    check("第一项被标为 next", prog["data"]["days"][0]["items"][0]["next"] is True)

    # 3) 打勾 + 幂等
    _, marked = call("POST", f"/plans/{plan_id}/progress",
                     {"itemId": first_item, "completed": True}, token)
    check("打勾成功", marked["data"]["completed"] == 1, marked["data"])

    _, again = call("POST", f"/plans/{plan_id}/progress",
                    {"itemId": first_item, "completed": True}, token)
    check("重复打勾幂等", again["data"]["completed"] == 1, again["data"])

    expected_percent = round(100 / total)
    check("百分比正确", again["data"]["percent"] == expected_percent,
          f"{again['data']['percent']} != {expected_percent}")

    # 4) 取消勾选 → 再勾
    _, undone = call("POST", f"/plans/{plan_id}/progress",
                     {"itemId": first_item, "completed": False}, token)
    check("取消勾选回退", undone["data"]["completed"] == 0, undone["data"])

    _, redo = call("POST", f"/plans/{plan_id}/progress",
                   {"itemId": first_item, "completed": True}, token)
    check("取消后再勾选成功", redo["data"]["completed"] == 1, redo["data"])

    # 5) 越权：别人的行程项
    other_token = new_user("progx")
    other_plan = make_plan(other_token)
    _, other_prog = call("GET", f"/plans/{other_plan}/progress", token=other_token)
    other_item = other_prog["data"]["days"][0]["items"][0]["itemId"]
    status, forbidden = call("POST", f"/plans/{plan_id}/progress",
                             {"itemId": other_item, "completed": True}, token)
    check("跨计划行程项被拒 30105", forbidden.get("code") == 30105, forbidden)

    # 6) 全部勾完 → 标记完成
    all_items = [i["itemId"] for d in redo["data"]["days"] for i in d["items"]]
    for item_id in all_items:
        call("POST", f"/plans/{plan_id}/progress", {"itemId": item_id, "completed": True}, token)
    _, full = call("GET", f"/plans/{plan_id}/progress", token=token)
    check("全部完成后 percent=100", full["data"]["percent"] == 100, full["data"])

    status, body = call("POST", f"/plans/{plan_id}/complete", token=token)
    check("标记完成成功", body.get("code") == 0, body)

    _, done = call("GET", f"/plans/{plan_id}/progress", token=token)
    check("状态变为 COMPLETED", done["data"]["status"] == "COMPLETED", done["data"]["status"])
    check("完成后进度仍可读", done["data"]["percent"] == 100, done["data"])

    status, rejected = call("POST", f"/plans/{plan_id}/progress",
                            {"itemId": first_item, "completed": False}, token)
    check("完成后不能再改进度 30103", rejected.get("code") == 30103, rejected)

    print()
    if failures:
        print(f"{len(failures)} 项失败：" + "、".join(failures))
        return 1
    print("全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
