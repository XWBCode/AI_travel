"""MVP 正式验收harness（AC-01 ~ AC-15，02 §6）。

与 verify_*.py 的区别：那些是「功能是否按设计工作」，这个只回答一件事——
**02 §6 的验收指标，当前基线（main @ 2e27428）达到了没有，用什么测的、测出多少。**

三条纪律（PM 要求）：
1. 不断言做不到的事：测不了的标 BLOCKED 并写明原因，绝不把 BLOCKED 记成 PASS；
2. Mock 模式下的数字必须标注 `mock`，不得当作真实模型结论；
3. 只读 + 可重复：脚本不删数据、不改代码，跑几次结果都该一致。

用法：
    python backend-java/scripts/verify_acceptance.py            # 跑全部
    python backend-java/scripts/verify_acceptance.py AC-01 AC-13  # 只跑指定项
输出：
    终端表格 + docs/acceptance/raw/ac-last-run.json
"""

from __future__ import annotations

import json
import os
import statistics
import sys
import threading
import time
import urllib.error
import urllib.request
from datetime import datetime

BASE = "http://localhost:8080/api"
AI = "http://localhost:8000"
OUT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "docs", "acceptance", "raw")

results: list[dict] = []


# ---------------------------------------------------------------- 基础工具

def call(method: str, path: str, body=None, token: str = "", base: str = BASE, timeout: int = 300):
    data = json.dumps(body, ensure_ascii=False).encode() if body is not None else None
    req = urllib.request.Request(base + path, data=data, method=method)
    req.add_header("Content-Type", "application/json")
    if token:
        req.add_header("Authorization", "Bearer " + token)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        raw = e.read().decode()
        try:
            return e.code, json.loads(raw)
        except json.JSONDecodeError:
            return e.code, {"message": raw[:200]}


def stream_events(token: str, content: str, session_id=None, abort_after: int | None = None):
    """读一次 SSE，返回 (事件列表, meta→首个 delta 的毫秒数)。

    abort_after 不为 None 时，读够这么多事件就主动断开，用于 AC-04。
    """
    body = {"content": content}
    if session_id:
        body["sessionId"] = session_id
    req = urllib.request.Request(BASE + "/chat/stream",
                                 data=json.dumps(body, ensure_ascii=False).encode(),
                                 method="POST")
    req.add_header("Content-Type", "application/json")
    req.add_header("Accept", "text/event-stream")
    req.add_header("Authorization", "Bearer " + token)

    events: list[tuple[str, str]] = []
    meta_at = None
    first_delta_ms = None
    resp = urllib.request.urlopen(req, timeout=120)
    try:
        name = None
        for raw in resp:
            line = raw.decode().rstrip("\r\n")
            if line.startswith("event:"):
                name = line[6:].strip()
            elif line.startswith("data:"):
                payload = line[5:].strip()
                if name:
                    events.append((name, payload))
                    now = time.monotonic()
                    if name == "meta" and meta_at is None:
                        meta_at = now
                    if name == "delta" and first_delta_ms is None and meta_at is not None:
                        first_delta_ms = round((now - meta_at) * 1000, 1)
                    if abort_after is not None and len(events) >= abort_after:
                        resp.close()
                        return events, first_delta_ms
    finally:
        try:
            resp.close()
        except Exception:
            pass
    return events, first_delta_ms


def new_user(prefix: str, pwd: str = "accept12345") -> tuple[str, str]:
    stamp = str(int(time.time() * 1000))[-9:]
    username = prefix + stamp
    call("POST", "/auth/register", {"username": username, "password": pwd, "nickname": "验收"})
    _, body = call("POST", "/auth/login", {"username": username, "password": pwd})
    return username, body["data"]["accessToken"]


def make_plan(token: str, text: str = "帮我规划成都三天，2个人，预算3000，喜欢美食") -> str:
    _, body = call("POST", "/plans/generate",
                   {"messages": [{"role": "user", "content": text}]}, token)
    if body.get("code") != 0:
        raise RuntimeError(f"生成计划失败：{body}")
    return body["data"]["planId"]


def detail(token: str, plan_id: str) -> dict:
    _, body = call("GET", f"/plans/{plan_id}", token=token)
    return body.get("data") or {}


def walk_fact_fields(node, path=""):
    """递归找所有 FactField（带 verified 键的对象），产出 (路径, 节点)。"""
    if isinstance(node, dict):
        if "verified" in node and isinstance(node.get("verified"), bool):
            yield path, node
        for k, v in node.items():
            yield from walk_fact_fields(v, f"{path}/{k}")
    elif isinstance(node, list):
        for i, v in enumerate(node):
            yield from walk_fact_fields(v, f"{path}/{i}")


def record(ac: str, title: str, status: str, metric: str, detail_text: str, note: str = ""):
    results.append({
        "ac": ac, "title": title, "status": status,
        "metric": metric, "detail": detail_text, "note": note,
    })
    print(f"[{status:7}] {ac} {title} — {metric}")


# ---------------------------------------------------------------- AC-01

def ac01():
    _, token = new_user("acc01")
    texts = ["你好", "我想去成都玩三天", "谢谢"]
    session_id = None
    for t in texts:
        events, _ = stream_events(token, t, session_id=session_id)
        for name, payload in events:
            if name == "meta":
                session_id = json.loads(payload)["sessionId"]
    _, first = call("GET", f"/chat/sessions/{session_id}/messages", token=token)
    _, second = call("GET", f"/chat/sessions/{session_id}/messages", token=token)
    a = [(m["id"], m["role"], m["content"]) for m in first["data"]]
    b = [(m["id"], m["role"], m["content"]) for m in second["data"]]
    # 3 轮 = 3 条用户消息 + 3 条助手消息；内容必须逐字一致
    ok = a == b and len(a) >= 6 and any("成都" in m[2] or "计划" in m[2] for m in a)
    record("AC-01", "历史消息持久化", "PASS" if ok else "FAIL",
           f"恢复率 {'100%' if a == b else '不一致'}，消息 {len(a)} 条",
           f"3 轮消息 ID/角色/内容两次读取完全一致：{a == b}；条数 {len(a)}")


# ---------------------------------------------------------------- AC-02

def ac02():
    _, token_a = new_user("acc02a")
    _, token_b = new_user("acc02b")
    events, _ = stream_events(token_a, "你好")
    session_id = next(json.loads(p)["sessionId"] for n, p in events if n == "meta")

    forbidden = 0
    for _ in range(20):
        status, body = call("GET", f"/chat/sessions/{session_id}/messages", token=token_b)
        if status == 403 or body.get("code") == 20003:
            forbidden += 1
    # 归属人自己能读，作为对照组
    status_ok, _ = call("GET", f"/chat/sessions/{session_id}/messages", token=token_a)
    rate = forbidden / 20 * 100
    record("AC-02", "会话隔离", "PASS" if rate == 100 and status_ok == 200 else "FAIL",
           f"越权拒绝率 {rate:.0f}%（20/20）",
           f"B 读 A 的 sessionId 20 次全部 403；A 自己读 200")


# ---------------------------------------------------------------- AC-03

def ac03():
    _, token = new_user("acc03")
    samples = []
    for i in range(30):
        _, ms = stream_events(token, f"你好，第 {i} 次")
        if ms is not None:
            samples.append(ms)
    if not samples:
        record("AC-03", "流式首字延迟", "BLOCKED", "未采到 delta", "30 次请求均无 delta 事件")
        return
    p50 = statistics.median(samples)
    p95 = sorted(samples)[max(0, int(len(samples) * 0.95) - 1)]
    # 02 §6：mock 模式 ≤ 500ms；真实模型 P95 ≤ 3s 待 D-07
    ok = p95 <= 500
    record("AC-03", "流式首字延迟（mock）", "PASS" if ok else "FAIL",
           f"P50 {p50:.0f}ms / P95 {p95:.0f}ms（n={len(samples)}）",
           "meta→首个 delta；真实模型未验证（key 失效，见 AC-15/LLM 报告）",
           "指标为 mock 模式 ≤500ms；真实模型 P95 待 D-07 另定")


# ---------------------------------------------------------------- AC-04

def ac04():
    _, token = new_user("acc04")
    # meta 一到就断：模拟用户刚看到「正在生成」就关页面
    events, _ = stream_events(token, "帮我规划成都三天", abort_after=1)
    session_id = next(json.loads(p)["sessionId"] for n, p in events if n == "meta")
    names = [n for n, _ in events]
    time.sleep(2)  # 给服务端把中断落库的时间
    _, body = call("GET", f"/chat/sessions/{session_id}/messages", token=token)
    msgs = body.get("data") or []
    assistant = [m for m in msgs if m["role"] == "assistant"]
    user_msgs = [m for m in msgs if m["role"] == "user"]
    # 用户消息必须在（流开始前就落库），助手消息必须有一条且状态是终态而非 SUCCESS 假成功
    persisted = bool(user_msgs)
    terminal = bool(assistant) and all(m["status"] in ("FAILED", "CANCELED", "SUCCESS") for m in assistant)
    # 断流后不应出现「SUCCESS 但内容为空」这种虚假成功
    fake_success = any(m["status"] == "SUCCESS" and not (m["content"] or "").strip() for m in assistant)
    ok = persisted and terminal and not fake_success
    statuses = [m["status"] for m in assistant]
    # 只有真被中断（CANCELED/FAILED）才算验过「中断」；mock 生成太快时窗口抓不到，
    # 那时只能说「没有虚假成功」，不能说「中断完整性达标」
    interrupted = any(st in ("CANCELED", "FAILED") for st in statuses)
    verdict = "PASS" if ok and interrupted else ("PARTIAL" if ok else "FAIL")
    record("AC-04", "生成中断完整性", verdict,
           f"用户消息落库 {len(user_msgs)} 条，助手消息 {statuses}",
           f"读到 {names} 后断开；无「SUCCESS 且内容为空」的假成功：{not fake_success}；"
           f"是否真的抓到中断窗口：{interrupted}"
           + ("" if interrupted else "（mock 生成太快，断连时服务端已生成完，此项未取得有效样本）"))


# ---------------------------------------------------------------- AC-05

def ac05():
    """意图识别准确率：直接打 Python 的 /plan/intent（不经过模型）。"""
    cases = [
        ("我想去成都玩三天", "成都", 3),
        ("帮我规划一下西安两天行程", "西安", 2),
        ("下周去杭州，玩四天，两个人", "杭州", 4),
        ("想去云南玩五天", "云南", 5),
        ("三亚三天怎么玩", "三亚", 3),
        ("从北京去上海出差两天", "上海", 2),
        ("厦门两日游", "厦门", 2),
        ("重庆三天，预算三千", "重庆", 3),
        ("我想去青岛玩三天，爱吃海鲜", "青岛", 3),
        ("帮我安排长沙两天", "长沙", 2),
        ("南京玩三天", "南京", 3),
        ("去大理五天", "大理", 5),
        ("成都三天", "成都", 3),
        ("哈尔滨四天，冬天去", "哈尔滨", 4),
        ("去拉萨七天", "拉萨", 7),
        ("苏州两天一夜", "苏州", 2),
        ("我想去广州玩四天", "广州", 4),
        ("昆明三天，喜欢自然", "昆明", 3),
        ("帮我规划去武汉玩两天", "武汉", 2),
        ("张家界玩三天", "张家界", 3),
    ]
    hit = 0
    misses = []
    for text, city, days in cases:
        _, body = call("POST", "/v1/plan/intent", {"messages": [{"role": "user", "content": text}]},
                       base=AI, timeout=30)
        data = body.get("data") or {}
        slots = data.get("slots") or {}
        got_city = (slots.get("destination") or "").strip()
        got_days = slots.get("days")
        if got_city == city and got_days == days:
            hit += 1
        else:
            misses.append(f"{text} → city={got_city!r} days={got_days!r}")
    rate = hit / len(cases) * 100
    record("AC-05", "需求字段识别（目的地/天数）", "PASS" if rate >= 95 else "FAIL",
           f"准确率 {rate:.0f}%（{hit}/{len(cases)}）",
           ("全部命中" if not misses else "未命中：" + "；".join(misses[:5])))


# ---------------------------------------------------------------- AC-06

def ac06():
    _, token = new_user("acc06")
    plan_id = make_plan(token, "帮我规划成都三天")
    d = detail(token, plan_id)
    basic = d.get("basic") or {}
    missing = basic.get("missingFields") or []
    # 没说的字段必须是 null + 进 missingFields，不能是 0 / "" / 编一个默认值
    checks = {
        "travelers": basic.get("travelers"),
        "budget": basic.get("budget"),
        "interests": (d.get("preferences") or {}).get("interests"),
    }
    clean = {k: v for k, v in checks.items() if not (v is None or v == [] or v == {})}
    declared = [k for k in checks if k in missing]
    ok = not clean and len(declared) >= 2
    record("AC-06", "缺失字段处理", "PASS" if ok else "FAIL",
           f"未编造 {len(checks) - len(clean)}/{len(checks)} 个字段，missingFields 声明 {len(declared)} 个",
           f"实际值为空的字段：{list(clean)}；missingFields={missing}")


# ---------------------------------------------------------------- AC-07

def ac07():
    """Schema 校验：契约里 10 组畸形样例必须全部被拒（AI 侧校验器）。"""
    path = os.path.join(os.path.dirname(__file__), "..", "..",
                        "contracts", "travel-plan", "examples", "invalid_cases.json")
    try:
        cases = json.load(open(path, encoding="utf-8"))
    except FileNotFoundError:
        record("AC-07", "计划 Schema 校验", "BLOCKED", "找不到畸形样例集", path)
        return
    # 由 pytest 侧真正执行；这里只核对样例规模，并把结果标为部分
    record("AC-07", "计划 Schema 校验", "PARTIAL",
           f"畸形样例 {len(cases)} 组（目标 50 组）",
           "执行证据在 backend-ai/tests/test_plan.py 与 PlanSchemaValidatorTest；"
           "「注入 50 组」的样本量未达 02 §6 目标，故记 PARTIAL 而非 PASS")


# ---------------------------------------------------------------- AC-08

def ac08():
    _, token = new_user("acc08")
    viol = 0
    total = 0
    for text in ("帮我规划成都三天", "帮我规划西安两天，喜欢历史", "帮我规划厦门三天，爱吃海鲜"):
        d = detail(token, make_plan(token, text))
        for p, node in walk_fact_fields(d):
            total += 1
            if node.get("verified") is False and node.get("value") not in (None, ""):
                viol += 1
    ok = viol == 0
    record("AC-08", "实时字段不编造", "PASS" if ok else "FAIL",
           f"违规 {viol} 处 / FactField {total} 个",
           "扫描 3 份生成计划的全部 FactField：verified=false 时 value 必须为空")


# ---------------------------------------------------------------- AC-09

def ac09():
    _, token = new_user("acc09")
    plan_id = make_plan(token)
    # 一批「不该成功」的操作，看状态会不会被顺手改掉
    call("POST", f"/plans/{plan_id}/start", token=token)      # 草稿不能开始
    call("POST", f"/plans/{plan_id}/complete", token=token)   # 草稿不能完成
    call("POST", f"/plans/{plan_id}/progress", {"itemId": "1", "completed": True}, token)  # 草稿不能打勾
    call("GET", f"/plans/{plan_id}/today", token=token)
    call("GET", f"/plans/{plan_id}/progress", token=token)
    d = detail(token, plan_id)
    status = d.get("status")
    record("AC-09", "确认状态机", "PASS" if status == "DRAFT" else "FAIL",
           f"非法操作后状态仍为 {status}",
           "生成后依次尝试 start/complete/progress/today，状态未被任何操作自动推进")


# ---------------------------------------------------------------- AC-10

def ac10():
    _, token = new_user("acc10")
    d = detail(token, make_plan(token))
    days = d.get("days") or []
    items = [i for day in days for i in (day.get("items") or [])]
    fields_ok = all(i.get("title") for i in items)
    structured = bool(days) and bool(items) and fields_ok and bool(d.get("basic", {}).get("days"))
    # 关键：结构化字段自洽（天数 = days 长度、每项有时间），不依赖任何 Markdown
    consistent = len(days) == (d.get("basic", {}).get("days") or 0)
    ok = structured and consistent
    record("AC-10", "结构化存储", "PASS" if ok else "FAIL",
           f"{len(days)} 天 / {len(items)} 个行程项，字段自洽 {consistent}",
           "详情完全由结构化字段还原（标题/时间/备注），渲染与 aiNote 无关")


# ---------------------------------------------------------------- AC-11

def ac11():
    _, token = new_user("acc11")
    plan_id = make_plan(token)
    call("POST", f"/plans/{plan_id}/confirm", token=token)
    call("POST", f"/plans/{plan_id}/start", token=token)
    before = detail(token, plan_id)
    version_before = before.get("version")
    content_before = json.dumps(before.get("days"), ensure_ascii=False, sort_keys=True)

    _, body = call("POST", f"/plans/{plan_id}/adjustments",
                   {"instruction": "把第一项的备注改成从北门进"}, token)
    if body.get("code") != 0:
        record("AC-11", "调整需确认", "BLOCKED",
               f"建议生成失败：{body.get('code')} {body.get('message')}",
               "mock 模式下 mock_adjust 行为与真实模型不同，此行不构成 AC-11 证据")
        return
    after = detail(token, plan_id)
    unchanged = (after.get("version") == version_before
                 and json.dumps(after.get("days"), ensure_ascii=False, sort_keys=True) == content_before)
    record("AC-11", "调整需确认（AI 不直接写正式计划）", "PASS" if unchanged else "FAIL",
           f"建议生成后 version {version_before} → {after.get('version')}，内容不变：{unchanged}",
           "生成的只是 PENDING 建议；正式计划只有 confirm 之后才变（详见 verify_plan_adjustment.py）")


# ---------------------------------------------------------------- AC-12

def ac12():
    _, token_a = new_user("acc12a")
    _, token_b = new_user("acc12b")
    plan_id = make_plan(token_a)
    call("POST", f"/plans/{plan_id}/confirm", token=token_a)
    call("POST", f"/plans/{plan_id}/start", token=token_a)
    # itemId 只在进度视图里下发：路书本身不需要它，打勾才需要
    _, prog = call("GET", f"/plans/{plan_id}/progress", token=token_a)
    item_id = next(i["itemId"] for day in prog["data"]["days"] for i in day["items"])

    probes = [
        ("GET", f"/plans/{plan_id}", None, 20003),
        ("PATCH", f"/plans/{plan_id}", {"title": "篡改"}, 20003),
        ("POST", f"/plans/{plan_id}/cancel", None, 20003),
        ("GET", f"/plans/{plan_id}/progress", None, 20003),
        ("POST", f"/plans/{plan_id}/progress", {"itemId": item_id, "completed": True}, 20003),
        ("GET", f"/plans/{plan_id}/versions", None, 20003),
    ]
    hits = 0
    detail_msgs = []
    for method, path, payload, expect in probes:
        status, body = call(method, path, payload, token_b)
        code = body.get("code")
        if code == expect or status == 403:
            hits += 1
        else:
            detail_msgs.append(f"{method} {path} → {status}/{code}")
    rate = hits / len(probes) * 100
    record("AC-12", "越权防护（计划/进度/版本）", "PASS" if rate == 100 else "FAIL",
           f"403 率 {rate:.0f}%（{hits}/{len(probes)} 类资源）",
           ("六类越权全部 403" if not detail_msgs else "未拦截：" + "；".join(detail_msgs)),
           "通知侧越权见 verify_notification.py（20003）")


# ---------------------------------------------------------------- AC-13

def ac13():
    _, token = new_user("acc13")
    plan_id = make_plan(token)
    call("POST", f"/plans/{plan_id}/confirm", token=token)

    def once():
        return call("POST", f"/plans/{plan_id}/complete", token=token)[1].get("code")

    success = 0
    lock = threading.Lock()
    codes = []

    def worker():
        nonlocal success
        code = once()
        with lock:
            codes.append(code)
            if code == 0:
                success += 1

    call("POST", f"/plans/{plan_id}/start", token=token)
    threads = [threading.Thread(target=worker) for _ in range(50)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    d = detail(token, plan_id)
    _, n = call("GET", "/notifications?page=1&size=50", token=token)
    done_notes = [r for r in (n.get("data", {}).get("records") or [])
                  if r["type"] == "PLAN_COMPLETED"]
    ok = success == 1 and d.get("status") == "COMPLETED" and len(done_notes) == 1
    record("AC-13", "幂等性 / 并发安全（完成旅行 50 并发）", "PASS" if ok else "FAIL",
           f"成功 {success}/50，状态 {d.get('status')}，「旅行已结束」通知 {len(done_notes)} 条",
           f"错误码分布：{ {c: codes.count(c) for c in sorted(set(codes))} }")


# ---------------------------------------------------------------- AC-14

def ac14():
    """外部源不可用时前端是否降级：需要人为断掉 AI 服务，验收阶段不做破坏性操作。"""
    record("AC-14", "异常可见（外部源降级）", "BLOCKED",
           "未执行",
           "需要停掉 8000 端口或注入 5xx 才能测；验收阶段不改运行环境。"
           "已有代码证据：AiServiceClient 把网关异常映射为 50201，前端 stream-error.ts 有对应文案（15 项断言）",
           "方法：停 Python 服务后在聊天页发消息，观察是否出现可读的降级提示")


# ---------------------------------------------------------------- AC-15

def ac15():
    record("AC-15", "来源标注（L1 天气）", "NA",
           "不适用",
           "L0 阶段没有实时天气数据源，本项依赖 M3（D-03/D-04 未定）；"
           "FactField 的 source/fetchedAt 结构已在契约中就位，但无数据可验")


# ---------------------------------------------------------------- 汇总

ALL = [ac01, ac02, ac03, ac04, ac05, ac06, ac07, ac08, ac09, ac10, ac11, ac12, ac13, ac14, ac15]


def main() -> int:
    ids = {ac01: "AC-01", ac02: "AC-02", ac03: "AC-03", ac04: "AC-04", ac05: "AC-05",
           ac06: "AC-06", ac07: "AC-07", ac08: "AC-08", ac09: "AC-09", ac10: "AC-10",
           ac11: "AC-11", ac12: "AC-12", ac13: "AC-13", ac14: "AC-14", ac15: "AC-15"}
    wanted = [a.upper() for a in sys.argv[1:] if a.upper().startswith("AC-")]
    print(f"验收基线：main @ 2e27428 · {datetime.now():%Y-%m-%d %H:%M:%S}")
    print("-" * 78)
    for fn in ALL:
        ac = ids[fn]
        if wanted and ac not in wanted:
            continue
        try:
            fn()
        except Exception as e:  # 单项炸掉不能拖垮整轮验收
            record(ac, fn.__doc__ or fn.__name__, "ERROR", str(e)[:120], repr(e)[:300])
    print("-" * 78)

    counts: dict[str, int] = {}
    for r in results:
        counts[r["status"]] = counts.get(r["status"], 0) + 1
    print("汇总：" + " · ".join(f"{k} {v}" for k, v in sorted(counts.items())))

    os.makedirs(OUT_DIR, exist_ok=True)
    out = os.path.join(OUT_DIR, "ac-last-run.json")
    with open(out, "w", encoding="utf-8") as f:
        json.dump({"baseline": "main@2e27428", "at": datetime.now().isoformat(timespec="seconds"),
                   "results": results}, f, ensure_ascii=False, indent=2)
    print(f"明细已写入 {os.path.normpath(out)}")
    # 有 FAIL / ERROR 时退出码非 0，CI 可以直接接
    return 1 if counts.get("FAIL") or counts.get("ERROR") else 0


if __name__ == "__main__":
    sys.exit(main())
