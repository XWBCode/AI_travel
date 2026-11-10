"""Mock 计划生成器。

职责：在没有任何 LLM key 的情况下，产出**结构完全合法**的 TravelPlan 草稿，
让 M1 的前端渲染、Java 校验落库、状态机可以全链路验收。

三条自我约束：
1. 不编造具体事实。所有活动标题都是"泛化类型"（如「老城历史街区漫步」），
   不点名真实景点、不给票价与耗时——否则 Mock 数据会被当成真实推荐。
2. 显式标 `[MOCK]`。aiNote 首句就写明来源，避免被误认为真实 AI 输出（总纲 9.3）。
3. 所有实时字段走 L0：`value=null`、`display=待核实`、`verified=false`、`source=NONE`。
   这不是"填不上"，而是契约要求——L0 阶段不允许出现任何具体数值。
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any

from app.plan.contract import count_fact_fields
from app.plan.intent import TravelIntent

# 每天的活动时段：上午 / 下午 / 傍晚
_TIME_SLOTS: tuple[tuple[str, str], ...] = (("09:00", "11:30"), ("14:00", "16:30"), ("18:00", "19:30"))

# 按兴趣给出泛化活动，不点名具体景点
_ACTIVITIES_BY_INTEREST: dict[str, tuple[str, ...]] = {
    "美食": ("本地特色餐饮街区", "老字号小吃探访", "夜市与街头美食体验"),
    "历史人文": ("老城历史街区漫步", "本地博物馆参观", "古迹与寺庙走访"),
    "自然风光": ("城郊自然景观步道", "城市公园与湖区漫步", "山野观景点徒步"),
    "购物": ("城市商业街区逛街", "本地市集与特产采购", "免税或百货商场"),
    "亲子": ("适合亲子的城市公园", "科技馆或自然博物馆", "亲子友好型室内场馆"),
    "摄影": ("城市天际线观景点", "老城街巷摄影漫步", "日落时分的滨水区域"),
    "夜生活": ("夜间商业街区", "滨水夜景步道", "本地夜市体验"),
    "休闲度假": ("温泉或休闲会所", "城市慢行街区", "咖啡馆与书店巡游"),
}

_DEFAULT_ACTIVITIES: tuple[str, ...] = ("城市地标与老城漫步", "本地博物馆或文化场馆", "特色餐饮街区")

_DAILY_NOTES: tuple[str, ...] = (
    "建议出发前确认当日开放情况",
    "该时段安排较宽松，可根据体力调整",
    "如遇人流高峰可改至相邻时段",
)


def _fact(note: str) -> dict[str, Any]:
    """标准 FactField（L0）：七键齐全，value 恒 null，display 恒「待核实」。"""
    return {
        "value": None,
        "display": "待核实",
        "source": "NONE",
        "verified": False,
        "fetchedAt": None,
        "expiresAt": None,
        "note": note,
    }


def _activities(interests: list[str]) -> tuple[str, ...]:
    for tag in interests:
        if tag in _ACTIVITIES_BY_INTEREST:
            return _ACTIVITIES_BY_INTEREST[tag]
    return _DEFAULT_ACTIVITIES


def _city_for_day(day_index: int, days: int, cities: list[str]) -> str:
    """多城市时按天数均分；单城市恒为目的地。"""
    if len(cities) == 1:
        return cities[0]
    per_city = days / len(cities)
    position = min(int((day_index - 1) / per_city), len(cities) - 1)
    return cities[position]


def _build_item(order: int, title: str, start: str, end: str, note: str) -> dict[str, Any]:
    return {
        "itemOrder": order,
        "attractionId": None,
        "title": title,
        "startTime": start,
        "endTime": end,
        "duration": _fact("未接入实时数据源"),
        "openHours": _fact("请以景区公告为准"),
        "ticketPrice": _fact("请以景区公告为准"),
        "note": note,
    }


def _intra_day_segment(from_order: int, to_order: int) -> dict[str, Any]:
    return {
        "scope": "INTRA_DAY",
        "fromItemOrder": from_order,
        "toItemOrder": to_order,
        "fromDayIndex": None,
        "toDayIndex": None,
        "fromCity": None,
        "toCity": None,
        "mode": "公共交通",
        "duration": _fact("未接入地图服务"),
        "distance": _fact("未接入地图服务"),
        "advice": "两处距离较近，可用公共交通或步行前往，具体线路建议现场查询",
    }


def _inter_city_segment(from_day: int, to_day: int, from_city: str, to_city: str) -> dict[str, Any]:
    return {
        "scope": "INTER_CITY",
        "fromItemOrder": None,
        "toItemOrder": None,
        "fromDayIndex": from_day,
        "toDayIndex": to_day,
        "fromCity": from_city,
        "toCity": to_city,
        "mode": "城际铁路",
        "duration": _fact("未接入班次数据"),
        "distance": _fact("未接入地图服务"),
        "advice": "建议提前查询班次并购票，预留充足换乘时间",
    }


def _build_day(day_index: int, the_date: str | None, city: str, is_last_day: bool,
               interests: list[str], previous_city: str | None) -> dict[str, Any]:
    activities = _activities(interests)
    slot_count = 2 if len(activities) < 3 else 3

    items = []
    for offset in range(slot_count):
        start, end = _TIME_SLOTS[offset]
        # 按天错位取模板，避免每天都排出完全一样的三件事
        title = activities[(day_index - 1 + offset) % len(activities)]
        items.append(_build_item(offset + 1, title, start, end, _DAILY_NOTES[offset % len(_DAILY_NOTES)]))

    segments: list[dict[str, Any]] = []
    # 跨城段挂在新一天上，fromDayIndex 指向前一天（沿用 contracts 样例的表达）
    if previous_city and previous_city != city:
        segments.append(_inter_city_segment(day_index - 1, day_index, previous_city, city))
    for offset in range(len(items) - 1):
        segments.append(_intra_day_segment(offset + 1, offset + 2))

    accommodation = None
    if not is_last_day:
        accommodation = {
            "areaName": f"{city}市中心或交通枢纽附近",
            "suggestion": "建议选择交通便利、便于衔接次日行程的区域",
            "priceRange": _fact("未接入房价数据"),
        }

    return {
        "dayIndex": day_index,
        "date": the_date,
        "city": city,
        "theme": "行程安排" if not interests else interests[0],
        "items": items,
        "transportSegments": segments,
        "accommodation": accommodation,
    }


def _build_budget(has_budget_hint: bool) -> dict[str, Any]:
    categories = [
        ("TRANSPORT", "往返与城际交通"),
        ("ACCOMMODATION", "住宿"),
        ("FOOD", "餐饮"),
        ("TICKET", "门票与活动"),
    ]
    note = "按中等消费水平估算，需自行核实"
    return {
        "currency": "CNY",
        "total": _fact("未接入实时价格"),
        "items": [
            {"category": category, "label": label, "amount": _fact("未接入实时价格"), "note": note}
            for category, label in categories
        ],
        "note": (
            "已记录您提到的预算预期，具体金额请以实际报价为准"
            if has_budget_hint
            else "当前未接入实时价格数据，仅提供估算口径，请以实际报价为准"
        ),
    }


def build_plan(intent: TravelIntent, today: date | None = None) -> dict[str, Any]:
    """按意图槽位构造一份合法草稿。调用方负责再用契约校验一次。"""
    today = today or date.today()
    slots = intent.slots

    destination = slots.get("destination")
    if not destination:
        raise ValueError("缺少目的地，不能生成计划（应先追问）")

    cities = slots.get("destinations") or [destination]
    days = slots.get("days") or 1
    start_date = slots.get("startDate")

    day_plans = []
    previous_city: str | None = None
    for index in range(1, days + 1):
        city = _city_for_day(index, days, cities)
        the_date = None
        if start_date:
            the_date = (date.fromisoformat(start_date) + timedelta(days=index - 1)).isoformat()
        day_plans.append(
            _build_day(index, the_date, city, index == days, slots.get("interests") or [], previous_city)
        )
        previous_city = city

    plan: dict[str, Any] = {
        "schemaVersion": "1.0",
        "planId": None,
        "title": f"{'-'.join(cities)}{days}日行程",
        "status": "DRAFT",
        "basic": {
            "originCity": slots.get("originCity"),
            "destination": destination,
            "destinations": cities,
            "startDate": start_date,
            "endDate": slots.get("endDate"),
            "days": days,
            "travelers": slots.get("travelers"),
            "missingFields": intent.missing_fields,
        },
        "preferences": {
            "interests": slots.get("interests") or [],
            "transport": slots.get("transport"),
            "accommodation": slots.get("accommodation"),
            "specialRequirements": [],
            "interestsSource": "USER" if slots.get("interests") else "USER_DEFAULT",
            "transportSource": "USER" if slots.get("transport") else "USER_DEFAULT",
            "accommodationSource": "USER" if slots.get("accommodation") else "USER_DEFAULT",
            "specialRequirementsSource": "USER_DEFAULT",
        },
        "days": day_plans,
        "budget": _build_budget(slots.get("budget") is not None),
        "risks": [
            "景区开放时间可能临时调整，出行前请查询官方公告",
            "旺季人流较大，建议错峰安排行程",
            "跨城移动建议预留充足换乘时间",
        ],
        "aiNote": (
            "[MOCK] 本计划由本地 Mock 生成器产出，仅用于打通链路与验证结构，"
            "未调用真实模型，也未接入任何实时数据源。所有票价、开放时间与耗时均为待核实状态，"
            "请以官方渠道为准。"
        ),
        "unverifiedFieldCount": 0,
    }
    # 由结构自动统计，而不是写死常量——避免样例里那种"声明 25、实际 24"的偏差
    plan["unverifiedFieldCount"] = count_fact_fields(plan)
    return plan
