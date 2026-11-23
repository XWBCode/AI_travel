"""AI 调整建议的端到端验证（真实模型 + 真实落库）。

验的是 R-23 / R-24 / AC-11 那条链：AI 只出建议 → 用户确认前正式计划一字不动 →
确认后增量生效且不影响没被点名的项与已打的勾 → 计划变了建议作废。

用法：python backend-java/scripts/verify_plan_adjustment.py
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
        with urllib.request.urlopen(req, timeout=200) as resp:
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
         {"username": username, "password": "adj12345", "nickname": "调整验证"})
    _, body = call("POST", "/auth/login", {"username": username, "password": "adj12345"})
    return body["data"]["accessToken"]


def make_plan(token: str) -> str:
    status, body = call("POST", "/plans/generate",
                        {"messages": [{"role": "user", "content": "帮我规划成都三天，2个人，预算3000，喜欢美食"}]},
                        token)
    if status != 200 or body.get("code") != 0:
        print(f"生成计划失败：{body}")
        sys.exit(2)
    return body["data"]["planId"]


def detail(token: str, plan_id: str) -> dict:
    _, body = call("GET", f"/plans/{plan_id}", token=token)
    return body["data"]


def progress(token: str, plan_id: str) -> dict:
    _, body = call("GET", f"/plans/{plan_id}/progress", token=token)
    return body["data"]


def find_progress_item(prog: dict, item_id: str):
    for day in prog["days"]:
        for item in day["items"]:
            if item["itemId"] == str(item_id):
                return day, item
    return None, None


def main() -> int:
    token = new_user("adj")
    plan_id = make_plan(token)
    print(f"计划 {plan_id} 已生成")

    for action in ("confirm", "start"):
        _, body = call("POST", f"/plans/{plan_id}/{action}", token=token)
        if body.get("code") != 0:
            print(f"{action} 失败：{body}")
            sys.exit(2)

    prog = progress(token, plan_id)
    first_item = prog["days"][0]["items"][0]["itemId"]
    total_before = prog["total"]

    # 打一个勾：后面要验「调整不会清掉无关项的进度」
    _, before_item = find_progress_item(prog, first_item)
    start_before = before_item.get("startTime")
    _, marked = call("POST", f"/plans/{plan_id}/progress",
                     {"itemId": first_item, "completed": True}, token)
    check("前置：第一项已勾选", marked["data"]["completed"] == 1, marked["data"])

    # ---------- 1. 生成建议：只产出建议，不动计划 ----------
    version_before = detail(token, plan_id)["version"]
    print("生成调整建议（真实模型，约 30s）…")
    status, body = call("POST", f"/plans/{plan_id}/adjustments",
                        {"instruction": "第一项安排得太早了，改成上午十点开始"}, token)
    check("建议生成成功", body.get("code") == 0, body)
    proposal = body.get("data") or {}
    ops = proposal.get("ops") or []
    check("建议里有增量操作", len(ops) > 0, proposal)
    check("建议状态为待确认", proposal.get("status") == "PENDING", proposal.get("status"))
    check("建议记录了基线版本",
          proposal.get("baseVersion") == version_before, (proposal.get("baseVersion"), version_before))
    check("下发形态的 itemId 是字符串",
          all(isinstance(op.get("itemId"), str) for op in ops if op.get("itemId")), ops)

    after_propose = detail(token, plan_id)
    check("确认前计划版本不变", after_propose["version"] == version_before, after_propose["version"])
    _, still = find_progress_item(progress(token, plan_id), first_item)
    check("确认前第一项的开始时间未被改动",
          still is not None and still.get("startTime") == start_before, still)

    # ---------- 2. 确认：增量生效 ----------
    adjustment_id = proposal["adjustmentId"]
    _, applied = call("POST", f"/plans/{plan_id}/adjustments/{adjustment_id}/confirm", token=token)
    check("确认成功", applied.get("code") == 0, applied)
    result = applied.get("data") or {}
    check("版本前进一", result.get("version") == version_before + 1, result)

    prog_after = progress(token, plan_id)
    for op in ops:
        kind = op["op"]
        if kind == "REMOVE_ITEM":
            _, gone = find_progress_item(prog_after, op["itemId"])
            check(f"删除生效 {op.get('title')}", gone is None, op)
        elif kind == "UPDATE_ITEM":
            _, current = find_progress_item(prog_after, op["itemId"])
            for change in op.get("changes") or []:
                check(f"修改生效 {op.get('title')} · {change['field']}",
                      current is not None and str(current.get(change["field"]) or "") == str(change["after"] or ""),
                      (current, change))
        elif kind == "ADD_ITEM":
            check("新增生效：总数增加", prog_after["total"] == total_before + 1,
                  (prog_after["total"], total_before))
        elif kind == "MOVE_ITEM":
            day, _ = find_progress_item(prog_after, op["itemId"])
            check(f"移动生效 {op.get('title')}",
                  day is not None and day["dayIndex"] == op["toDayIndex"], (day, op))

    # 未被点名的项、已打的勾都必须还在
    _, restored = find_progress_item(prog_after, first_item)
    check("已勾选的项仍在且仍为完成",
          restored is not None and restored["completed"] is True, restored)

    # ---------- 3. 计划变了 → 建议作废 ----------
    print("生成第二条建议（验证过期）…")
    _, second = call("POST", f"/plans/{plan_id}/adjustments",
                     {"instruction": "第二天那项想改个备注"}, token)
    check("第二条建议生成成功", second.get("code") == 0, second)
    second_data = second.get("data") or {}
    if second.get("code") != 0:
        print(f"\n{len(failures)} 项失败：{failures}")
        return 1

    # 先用打勾确认「进度变化不算内容变更」：打勾不动 version，建议因此不会失效。
    # 这是刻意的——若每次勾选都让待确认的建议作废，用户永远等不到能点的那一次。
    another = next(i["itemId"] for d in prog_after["days"] for i in d["items"]
                   if i["itemId"] != str(first_item))
    call("POST", f"/plans/{plan_id}/progress", {"itemId": another, "completed": True}, token)
    _, after_mark = call("POST", f"/plans/{plan_id}/adjustments/{second_data['adjustmentId']}/confirm",
                         token=token)
    check("打勾不影响建议有效性（进度不改变版本）", after_mark.get("code") == 0, after_mark)

    # 真正会让建议失效的是**内容变更**：再做一次调整并确认，version 前进
    _, third = call("POST", f"/plans/{plan_id}/adjustments",
                    {"instruction": "把备注再改一下"}, token)
    check("内容变更用建议生成成功", third.get("code") == 0, third)
    third_data = third.get("data") or {}
    if third.get("code") != 0:
        print(f"\n{len(failures)} 项失败：{failures}")
        return 1
    call("POST", f"/plans/{plan_id}/adjustments/{third_data['adjustmentId']}/confirm", token=token)

    # 再生成一条，然后用上一步的变更把它的基线顶掉
    _, fourth = call("POST", f"/plans/{plan_id}/adjustments",
                     {"instruction": "再改一处"}, token)
    fourth_data = fourth.get("data") or {}
    if fourth.get("code") != 0:
        print(f"\n{len(failures)} 项失败：{failures}")
        return 1
    _, fifth = call("POST", f"/plans/{plan_id}/adjustments",
                    {"instruction": "又改一处"}, token)
    fifth_data = fifth.get("data") or {}
    if fifth.get("code") == 0:
        call("POST", f"/plans/{plan_id}/adjustments/{fifth_data['adjustmentId']}/confirm", token=token)

    _, stale = call("POST", f"/plans/{plan_id}/adjustments/{fourth_data['adjustmentId']}/confirm",
                    token=token)
    check("计划内容变更后确认被拒 30108", stale.get("code") == 30108, stale)

    _, listed = call("GET", f"/plans/{plan_id}/adjustments", token=token)
    statuses = {a["adjustmentId"]: a["status"] for a in listed["data"]}
    check("过期的建议标记为 EXPIRED",
          statuses.get(fourth_data["adjustmentId"]) == "EXPIRED", statuses)

    # ---------- 4. 拒绝：不改计划、不产生版本 ----------
    print("生成第三条建议（验证拒绝）…")
    version_now = detail(token, plan_id)["version"]
    _, third = call("POST", f"/plans/{plan_id}/adjustments",
                    {"instruction": "把第三天的第一项标题改一下"}, token)
    third_data = third.get("data") or {}
    if third.get("code") == 0:
        _, rejected = call("POST",
                           f"/plans/{plan_id}/adjustments/{third_data['adjustmentId']}/reject",
                           {"reason": "不需要"}, token)
        check("拒绝成功", rejected.get("code") == 0, rejected)
        check("拒绝后版本不变", detail(token, plan_id)["version"] == version_now)
    else:
        check("第三条建议生成成功（模型未产出可执行改动）", False, third)

    # ---------- 5. 越权 ----------
    other = new_user("adjb")
    _, forbidden = call("POST",
                        f"/plans/{plan_id}/adjustments/{adjustment_id}/confirm", token=other)
    check("他人无法确认建议", forbidden.get("code") in (403, 20003, 30107), forbidden)

    # ---------- 6. 草稿不能走调整 ----------
    draft_id = make_plan(token)
    _, draft_adjust = call("POST", f"/plans/{draft_id}/adjustments",
                           {"instruction": "改一下"}, token)
    check("草稿阶段拒绝调整 30103", draft_adjust.get("code") == 30103, draft_adjust)

    print()
    if failures:
        print(f"{len(failures)} 项失败：{failures}")
        return 1
    print("全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
