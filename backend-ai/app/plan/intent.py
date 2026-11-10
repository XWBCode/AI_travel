"""意图判定与槽位抽取（M1 规则版）。

为什么不用 LLM 判意图：
1. 判意图要跑在每次对话上，用模型成本高、延迟大；
2. 规则可测、可解释，出了问题能定位到具体正则；
3. M1 的槽位类型固定（城市/天数/日期/人数/预算/偏好），规则覆盖率够。

代价是表达力有限：说法太绕会抽不到，抽不到就进 missingFields 追问，不会乱猜。
真实模型的职责是"生成计划内容"，不是"猜用户说了什么"。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any

# 中文数字
_CN_DIGITS = {"零": 0, "一": 1, "二": 2, "两": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9}

# missingFields 的追问优先级：越靠前越该先问（05 文档要求一次最多追问 2 个）
_FIELD_ORDER = [
    "destination", "days", "startDate", "endDate", "originCity",
    "travelers", "budget", "interests", "transport", "accommodation",
]

# 明确表示"在旅行语境"的词；单字"去"太宽泛，不单独作为信号
_TRAVEL_SIGNALS = ("旅游", "旅行", "行程", "攻略", "度假", "日游", "出游", "自由行", "跟团")

_INTEREST_KEYWORDS: dict[str, tuple[str, ...]] = {
    "美食": ("美食", "小吃", "火锅", "餐厅", "吃货", "探店", "好吃的"),
    "历史人文": ("历史", "人文", "古迹", "博物馆", "文化", "古城", "古镇", "寺庙", "遗址"),
    "自然风光": ("自然", "风景", "山水", "徒步", "爬山", "海边", "海岛", "雪山", "草原", "公园", "湖"),
    "购物": ("购物", "逛街", "商场", "免税", "买买买"),
    "亲子": ("亲子", "孩子", "小孩", "带娃", "儿童", "小朋友"),
    "摄影": ("摄影", "拍照", "出片", "打卡"),
    "夜生活": ("夜生活", "酒吧", "夜市", "夜景"),
    "休闲度假": ("休闲", "放松", "温泉", "度假", "躺平", "慢节奏"),
}

_TRANSPORT_KEYWORDS = {
    "高铁": ("高铁", "动车", "火车"),
    "飞机": ("飞机", "航班", "飞过去"),
    "自驾": ("自驾", "开车"),
    "大巴": ("大巴", "汽车", "客车"),
}

_ACCOMMODATION_KEYWORDS = {
    "民宿": ("民宿",),
    "酒店": ("酒店", "宾馆"),
    "青旅": ("青旅", "青年旅舍"),
    "客栈": ("客栈",),
    "经济型": ("经济型", "便宜住", "省钱"),
    "高档": ("五星", "高档", "奢华"),
}

# 抽到的"城市"若是这些词，说明用户还没定目的地（或把"我想"这类主语当成了地名）
_CITY_STOPWORDS = {
    "哪里", "哪儿", "什么地方", "周边", "附近", "国内", "国外", "外地", "那边", "这里",
    "我想", "我要", "我想要", "想要", "打算", "准备", "计划", "帮我", "麻烦", "可以", "能不能",
}

_CITY = r"[\u4e00-\u9fa5]{2,8}"


def _cn_to_int(text: str) -> int | None:
    """把 一~九十九 的中文数字转成整数；无法解析返回 None。"""
    text = (text or "").strip()
    if not text:
        return None
    if text.isdigit():
        return int(text)
    text = text.replace("零", "")
    if "十" in text:
        left, _, right = text.partition("十")
        tens = _CN_DIGITS.get(left, 1) if left else 1
        ones = _CN_DIGITS.get(right, 0) if right else 0
        if left and left not in _CN_DIGITS:
            return None
        if right and right not in _CN_DIGITS:
            return None
        return tens * 10 + ones
    if len(text) == 1 and text in _CN_DIGITS:
        return _CN_DIGITS[text]
    return None


def _clean_city(value: str) -> str | None:
    """清洗抽到的城市名：去掉语气词与动词尾巴，过滤停用词。"""
    if not value:
        return None
    value = re.sub(r"(玩|旅游|旅行|出差|度假|逛|待着|呆着|的|了|吧|呢|呀|啊|一下|几天)+$", "", value.strip())
    value = value.strip(" ，,。.、！!？?")
    if not value or value in _CITY_STOPWORDS or len(value) < 2:
        return None
    if not re.fullmatch(r"[\u4e00-\u9fa5]{2,8}", value):
        return None
    return value


def _split_cities(text: str) -> list[str]:
    """把「成都、乐山」「成都和乐山」拆成多个城市（Q-01 多城市行程）。"""
    parts = re.split(r"[、,，/和及与+]", text)
    cities: list[str] = []
    for part in parts:
        city = _clean_city(part)
        if city and city not in cities:
            cities.append(city)
    return cities[:10]


def _extract_destinations(text: str) -> list[str]:
    """抽目的地。

    关键是后缀边界：`去成都玩三天` 只能抽到「成都」，不能抽到「成都玩三天」。
    用 lookahead 卡住常见动词与标点，non-greedy 保证取最短命中。
    不在这里处理「从X去Y」——出发地由 _extract_origin 单独抽，否则「我想去成都」
    里的「我想」会被当成城市。
    """
    suffix = r"(?=玩|旅游|旅行|出差|度假|待|呆|逛|度|，|,|。|！|!|？|\?|\s|$)"
    patterns = [
        rf"(?:去|到|至|飞往|前往)\s*({_CITY}?){suffix}",
        rf"({_CITY}?)\s*(?:[0-9]{{1,2}}|[一二三四五六七八九十两]{{1,3}})\s*[日天]游",
    ]
    for pattern in patterns:
        match = re.search(pattern, text)
        if not match:
            continue
        cities = _split_cities(match.group(1))
        if cities:
            return cities
    return []


def _extract_origin(text: str) -> str | None:
    """抽出发地。只认「从X…」和「X到/至Y」两种明确说法。"""
    match = re.search(rf"从\s*({_CITY}?)\s*(?:出发|去|到|飞|坐|自驾|乘|前往)", text)
    if match:
        return _clean_city(match.group(1))
    match = re.search(rf"^\s*({_CITY}?)\s*(?:到|至)\s*{_CITY}", text)
    if match:
        return _clean_city(match.group(1))
    return None


_DATE_CN = re.compile(r"(?:(\d{4})\s*年\s*)?(\d{1,2})\s*月\s*(\d{1,2})\s*[日号]")
_DATE_ISO = re.compile(r"(\d{4})-(\d{1,2})-(\d{1,2})")


def _resolve_date(year: int | None, month: int, day: int, today: date) -> str | None:
    """把年月日解析成 ISO 字符串。没写年份时，若已过期则顺延到明年。"""
    candidates = []
    if year:
        candidates.append(year)
    else:
        candidates.extend([today.year, today.year + 1])
    for y in candidates:
        try:
            return date(y, month, day).isoformat()
        except ValueError:
            continue
    return None


def _extract_dates(text: str, today: date) -> tuple[str | None, str | None, str]:
    """抽取日期区间，返回 (start, end, 去掉日期片段后的剩余文本)。

    剩余文本用于后续抽天数，避免把「11月20日」里的 20 误认成 20 天。
    """
    cleaned = text

    iso = _DATE_ISO.search(text)
    if iso:
        start = _resolve_date(int(iso.group(1)), int(iso.group(2)), int(iso.group(3)), today)
        cleaned = _DATE_ISO.sub(" ", text, count=1)
        return start, None, cleaned

    matches = list(_DATE_CN.finditer(text))
    if not matches:
        return None, None, cleaned

    first = matches[0]
    start = _resolve_date(int(first.group(1)) if first.group(1) else None, int(first.group(2)), int(first.group(3)), today)
    end = None
    if len(matches) >= 2:
        second = matches[1]
        # 第二个日期若没写月份，沿用第一个的月份
        month = int(second.group(2)) if second.group(2) else int(first.group(2))
        if second.group(2) and not second.group(1):
            month = int(second.group(2))
        end = _resolve_date(int(second.group(1)) if second.group(1) else None, month, int(second.group(3)), today)
    for match in matches:
        cleaned = cleaned.replace(match.group(0), " ")
    return start, end, cleaned


def _extract_days(text: str) -> int | None:
    """抽天数。负向后顾排除「11月20日」这类日期里的数字。"""
    match = re.search(r"(?<![0-9月])([0-9]{1,2}|[一二三四五六七八九十两]{1,3})\s*[天日](?!游)", text)
    if not match:
        return None
    return _cn_to_int(match.group(1))


def _extract_travelers(text: str) -> int | None:
    match = re.search(r"([0-9]{1,2}|[一二三四五六七八九十两]{1,3})\s*(?:个)?\s*(?:人|位|成人|大人)", text)
    if match:
        return _cn_to_int(match.group(1))
    if any(word in text for word in ("我和朋友", "我们两个", "夫妻", "情侣", "两个人")):
        return 2
    if any(word in text for word in ("一家人", "全家", "带孩子")):
        return None  # 人数不明确，不猜
    return None


def _extract_budget(text: str) -> int | None:
    """抽预算（人民币元）。只认明确说法，不做"看着办"式的猜测。"""
    # 预算三千 / 预算 3000 / 预算约 5000 元
    match = re.search(r"预算\s*(?:大约|大概|约)?\s*([0-9]+(?:\.[0-9]+)?|[一二三四五六七八九两])\s*(万|千)?", text)
    if match:
        raw = match.group(1)
        unit = match.group(2)
        value = float(raw) if "." in raw else (_cn_to_int(raw) or 0)
        if unit == "万":
            value *= 10000
        elif unit == "千":
            value *= 1000
        return int(value) if value > 0 else None
    # 三千块 / 3000 元
    match = re.search(r"([0-9]{3,6}|[一二三四五六七八九两]千)\s*(?:元|块钱|块)", text)
    if match:
        raw = match.group(1)
        if raw.endswith("千"):
            base = _cn_to_int(raw[:-1]) or 0
            return base * 1000
        return int(raw)
    return None


def _extract_interests(text: str) -> list[str]:
    found: list[str] = []
    for tag, words in _INTEREST_KEYWORDS.items():
        if any(word in text for word in words):
            found.append(tag)
    return found


def _extract_by_dict(text: str, mapping: dict[str, tuple[str, ...]]) -> str | None:
    for label, words in mapping.items():
        if any(word in text for word in words):
            return label
    return None


@dataclass
class TravelIntent:
    """意图判定结果。slots 是抽取到的原始槽位，missing_fields 与 Schema 的 basic 一致。"""

    scene: str = "general"          # general | plan
    need_plan: bool = False
    slots: dict[str, Any] = field(default_factory=dict)
    missing_fields: list[str] = field(default_factory=list)

    def to_payload(self) -> dict[str, Any]:
        """SSE `intent` 事件的 data（05 文档 §4）。"""
        return {"scene": self.scene, "needPlan": self.need_plan, "missingFields": self.missing_fields}

    @property
    def follow_ups(self) -> list[str]:
        """真正需要追问的字段，最多 2 个（05 system prompt 的硬性规则 3）。

        注意 days 与 startDate 是"二选一"关系：只要有一个缺失就问天数，
        因为用户更容易回答"去几天"，日期可由天数推出，不必连问两个。
        """
        questions: list[str] = []
        if "destination" in self.missing_fields:
            questions.append("destination")
        if "days" in self.missing_fields and "startDate" in self.missing_fields:
            questions.append("days")
        return questions[:2]


def analyze(text: str, today: date | None = None) -> TravelIntent:
    """从最后一条用户消息中判定意图并抽取槽位。

    只看最后一条用户消息：多轮对话里的历史约束由 Java 侧裁剪后另行合并，
    这里不猜"上一轮说过什么"，避免把过期条件当成当前条件。
    """
    today = today or date.today()
    raw = (text or "").strip()
    if not raw:
        return TravelIntent()

    start_date, end_date, without_dates = _extract_dates(raw, today)
    days = _extract_days(without_dates)
    if days and (days < 1 or days > 30):
        days = None

    destinations = _extract_destinations(without_dates)
    origin = _extract_origin(raw)

    slots: dict[str, Any] = {
        "originCity": origin,
        "destination": destinations[0] if destinations else None,
        "destinations": destinations,
        "startDate": start_date,
        "endDate": end_date,
        "days": days,
        "travelers": _extract_travelers(raw),
        "budget": _extract_budget(raw),
        "interests": _extract_interests(raw),
        "transport": _extract_by_dict(raw, _TRANSPORT_KEYWORDS),
        "accommodation": _extract_by_dict(raw, _ACCOMMODATION_KEYWORDS),
    }

    # 只给日期区间没给天数时，用日期差补天数
    if slots["days"] is None and start_date and end_date and start_date < end_date:
        delta = (date.fromisoformat(end_date) - date.fromisoformat(start_date)).days + 1
        slots["days"] = delta if 1 <= delta <= 30 else None
    # 只有开始日期 + 天数时，推出结束日期
    if slots["startDate"] and slots["days"] and not slots["endDate"]:
        delta = timedelta(days=slots["days"] - 1)
        slots["endDate"] = (date.fromisoformat(slots["startDate"]) + delta).isoformat()

    has_signal = bool(
        slots["destination"] or slots["days"] or start_date
        or any(word in raw for word in _TRAVEL_SIGNALS)
    )
    if not has_signal:
        return TravelIntent(scene="general", need_plan=False, slots=slots, missing_fields=[])

    # 生成计划的最低条件：知道去哪、知道去几天（或哪天去）。其余缺失靠追问与"待核实"承载
    need_plan = bool(slots["destination"]) and bool(slots["days"] or slots["startDate"])

    missing = set()
    for key in ("originCity", "destination", "startDate", "endDate", "days", "travelers"):
        if slots.get(key) is None:
            missing.add(key)
    if slots["budget"] is None:
        missing.add("budget")
    if not slots["interests"]:
        missing.add("interests")
    if slots["transport"] is None:
        missing.add("transport")
    if slots["accommodation"] is None:
        missing.add("accommodation")

    ordered = [f for f in _FIELD_ORDER if f in missing]
    return TravelIntent(scene="plan", need_plan=need_plan, slots=slots, missing_fields=ordered)


def analyze_messages(messages: list[dict[str, str]]) -> TravelIntent:
    """从消息列表里取最后一条 user 消息判定。"""
    last_user = next((m.get("content", "") for m in reversed(messages) if m.get("role") == "user"), "")
    return analyze(last_user)
