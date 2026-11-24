"""逐笔订单归因明细账本。

归因分析原本只输出「渠道 -> 权重」的汇总，无法回答「这笔订单被哪些渠道
触点影响、各占多少」。本模块把归因下沉到订单粒度：

* 订单：按 ``order_id`` 幂等登记，重复提交只算一笔有效转化；
* 触点：按 ``touchpoint_id``（缺省时按 用户+渠道+时间）幂等登记；
* 归因明细：每笔有效订单落在自己的归因窗口内，窗口内触点权重合计为 1；
* 已确认版本：确认后的明细是不可变快照（含窗口与触点），渠道复盘必须
  绑定且只绑定一份已确认明细；
* 渠道复盘：从所选渠道的转化订单圈定复盘人群与转化事件范围，再按漏斗
  步骤统计人数；同批次内同一订单只能绑定同一个归因版本领取，避免在
  触点更正过程中被两个渠道按不同版本重复领取。

所有写操作都在同一把可重入锁内完成，重算 + 确认是原子事务：渠道复盘
要么领取更正前的已确认版本，要么领取更正后的已确认版本，不会读到半成品。
"""
from __future__ import annotations

import copy
import hashlib
import json
import threading
from datetime import datetime, timedelta, timezone
from typing import Any, Iterable

# 每笔有效订单的归因窗口默认回看 120 分钟。窗口只回看、不前瞻，因此
# 11:45 的迟到触点不可能进入 09:00 订单的窗口，只会进入 12:00 订单。
DEFAULT_WINDOW_MINUTES = 120

# 没有命中任何触点时，把整笔转化记到「直接访问」，保证每笔有效转化的
# 贡献合计恒为 1。
DIRECT_CHANNEL = "direct"

ALGORITHM_ALIASES = {
    "first_touch": "first_touch",
    "first": "first_touch",
    "首次触点": "first_touch",
    "首次触点归因": "first_touch",
    "last_touch": "last_touch",
    "last": "last_touch",
    "末次触点": "last_touch",
    "末次触点归因": "last_touch",
    "linear": "linear",
    "线性": "linear",
    "线性归因": "linear",
    "time_decay": "time_decay",
    "时间衰减": "time_decay",
    "时间衰减归因": "time_decay",
    "position_based": "position_based",
    "position": "position_based",
    "位置归因": "position_based",
}

INVALID_ORDER_STATUSES = {"无效", "已取消", "取消", "退款", "已退款", "cancelled", "canceled", "invalid", "refunded"}

DEFAULT_FUNNEL_STEPS = ["访问", "加购", "下单", "支付"]

# 未显式指定批次时，所有渠道复盘共用一个默认批次：同一笔订单在默认批次
# 内只绑定一个归因版本。每次确认新的归因版本会释放默认批次，允许更正后
# 按新口径重新复盘；显式批次则需要调用方自行释放。
DEFAULT_REVIEW_BATCH = "DEFAULT"

# 已确认版本是只读快照；后来被更正版本替代的历史版本仍是「已确认」，
# 只是不再是当前生效版本，仍可用于事后按当时口径复盘。
CONFIRMED_STATUSES = ("confirmed", "superseded")

STATUS_LABELS = {
    "draft": "草稿",
    "confirmed": "已确认",
    "superseded": "已确认（已被更正版本替代）",
}


# ============================================================
# 时间与规范化工具
# ============================================================

def parse_timestamp(value: Any) -> datetime:
    """把 ISO 字符串 / datetime 统一成「无时区的 UTC 时间」。"""
    if isinstance(value, datetime):
        dt = value
    else:
        text = str(value or "").strip()
        if not text:
            raise ValueError("时间不能为空")
        candidate = text.replace("Z", "+00:00")
        try:
            dt = datetime.fromisoformat(candidate)
        except ValueError:
            parsed = None
            for fmt in ("%Y-%m-%d %H:%M:%S", "%Y/%m/%d %H:%M:%S", "%Y-%m-%d %H:%M"):
                try:
                    parsed = datetime.strptime(candidate, fmt)
                    break
                except ValueError:
                    continue
            if parsed is None:
                raise ValueError(f"无法解析时间：{value}")
            dt = parsed
    if dt.tzinfo is not None:
        dt = dt.astimezone(timezone.utc).replace(tzinfo=None)
    return dt


def format_timestamp(dt: datetime) -> str:
    return dt.isoformat(timespec="seconds")


def _copy(value: Any) -> Any:
    return copy.deepcopy(value)


def _stable_checksum(payload: Any) -> str:
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _normalize_algorithm(value: Any) -> str:
    text = str(value or "linear").strip()
    return ALGORITHM_ALIASES.get(text, ALGORITHM_ALIASES.get(text.lower(), "linear"))


def _is_valid_order(order: dict[str, Any]) -> bool:
    status = str(order.get("status") or "").strip()
    if status.casefold() in INVALID_ORDER_STATUSES:
        return False
    return bool(order.get("valid", True))


def _round_weights(items: list[dict[str, Any]], key: str = "weight") -> None:
    """四舍五入到 6 位后把残差补到最后一个正权重上，确保每笔合计精确为 1。"""
    if not items:
        return
    total = 0.0
    positive: list[dict[str, Any]] = []
    for item in items:
        value = round(float(item.get(key, 0.0)), 6)
        item[key] = value
        total += value
        if value > 0:
            positive.append(item)
    residual = round(1.0 - total, 6)
    if positive and abs(residual) > 0:
        positive[-1][key] = round(positive[-1][key] + residual, 6)


# ============================================================
# 归因权重算法（订单粒度）
# ============================================================

def compute_order_weights(
    touchpoints: list[dict[str, Any]],
    order_time: datetime,
    algorithm: str = "linear",
) -> list[dict[str, Any]]:
    """给一笔订单窗口内的触点分配权重，返回带 ``weight`` 的触点副本。"""
    ordered = sorted(touchpoints, key=lambda item: (item["_dt"], item["touchpoint_id"]))
    if not ordered:
        return []
    normalized = _normalize_algorithm(algorithm)
    n = len(ordered)

    if normalized == "first_touch":
        weights = [1.0] + [0.0] * (n - 1)
    elif normalized == "last_touch":
        weights = [0.0] * (n - 1) + [1.0]
    elif normalized == "position_based":
        if n == 1:
            weights = [1.0]
        elif n == 2:
            weights = [0.5, 0.5]
        else:
            middle = 0.2 / (n - 2)
            weights = [0.4] + [middle] * (n - 2) + [0.4]
    elif normalized == "time_decay":
        # 半衰期 24 小时：离订单越近，权重越高。
        raw = []
        for tp in ordered:
            delta_hours = max((order_time - tp["_dt"]).total_seconds() / 3600.0, 0.0)
            raw.append(2.0 ** (-delta_hours / 24.0))
        total = sum(raw) or 1.0
        weights = [value / total for value in raw]
    else:  # linear
        weights = [1.0 / n] * n

    result: list[dict[str, Any]] = []
    for tp, weight in zip(ordered, weights):
        item = {key: value for key, value in tp.items() if not key.startswith("_")}
        item["weight"] = weight
        result.append(item)
    _round_weights(result)
    return result


# ============================================================
# 账本
# ============================================================

class AttributionLedger:
    """订单、触点、归因版本与渠道复盘的唯一写入口。"""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._orders: dict[str, dict[str, Any]] = {}
        self._touchpoints: dict[str, dict[str, Any]] = {}
        self._events: dict[str, dict[str, Any]] = {}
        self._versions: dict[int, dict[str, Any]] = {}
        self._reviews: dict[int, dict[str, Any]] = {}
        self._claims: dict[str, dict[str, dict[str, Any]]] = {}
        self._seq = {"order": 0, "touchpoint": 0, "event": 0, "version": 0, "review": 0}
        self._correction_in_progress = False

    # ---------- 订单 ----------

    def submit_order(self, payload: dict[str, Any], *, actor: str = "分析师") -> dict[str, Any]:
        """幂等登记订单。同一 order_id 只保留第一次提交的有效数据。"""
        order_id = str(payload.get("order_id") or payload.get("订单编号") or "").strip()
        if not order_id:
            raise ValueError("缺少订单编号 order_id")
        user_id = str(payload.get("user_id") or payload.get("用户编号") or "").strip()
        if not user_id:
            raise ValueError("缺少用户编号 user_id")
        order_time_raw = payload.get("order_time") or payload.get("订单时间") or payload.get("timestamp")
        order_time = parse_timestamp(order_time_raw)

        with self._lock:
            existing = self._orders.get(order_id)
            if existing is not None:
                return {
                    "ok": True,
                    "created": False,
                    "duplicate": True,
                    "message": f"订单 {order_id} 已登记，重复提交不重复计入转化",
                    "order": _copy(existing),
                    "total_conversions": self._conversion_count_locked(),
                }

            self._seq["order"] += 1
            status = str(payload.get("status") or payload.get("订单状态") or "有效").strip() or "有效"
            order = {
                "order_id": order_id,
                "user_id": user_id,
                "order_time": format_timestamp(order_time),
                "amount": payload.get("amount", payload.get("金额", 0)),
                "status": status,
                "valid": status.casefold() not in INVALID_ORDER_STATUSES,
                "channel_hint": payload.get("channel") or payload.get("渠道") or "",
                "created_by": actor,
                "submitted_at": format_timestamp(datetime.utcnow()),
            }
            order["_order_dt"] = order_time
            self._orders[order_id] = order
            return {
                "ok": True,
                "created": True,
                "duplicate": False,
                "message": f"订单 {order_id} 已登记",
                "order": self._public_order(order),
                "total_conversions": self._conversion_count_locked(),
            }

    def submit_orders(self, payloads: Iterable[dict[str, Any]], *, actor: str = "分析师") -> list[dict[str, Any]]:
        return [self.submit_order(payload, actor=actor) for payload in payloads]

    def list_orders(self) -> list[dict[str, Any]]:
        with self._lock:
            return [self._public_order(order) for order in self._sorted_orders()]

    # ---------- 触点 ----------

    def add_touchpoint(self, payload: dict[str, Any]) -> dict[str, Any]:
        user_id = str(payload.get("user_id") or payload.get("用户编号") or "").strip()
        if not user_id:
            raise ValueError("缺少用户编号 user_id")
        channel = str(payload.get("channel") or payload.get("渠道") or "").strip()
        if not channel:
            raise ValueError("缺少渠道 channel")
        timestamp_raw = payload.get("timestamp") or payload.get("触点时间") or payload.get("touch_time")
        timestamp = parse_timestamp(timestamp_raw)

        with self._lock:
            touchpoint_id = str(payload.get("touchpoint_id") or payload.get("触点编号") or "").strip()
            if not touchpoint_id:
                # 未显式给编号时用内容指纹去重，迟到触点重复上报不会重复计权。
                digest = hashlib.sha1(
                    f"{user_id}|{channel}|{format_timestamp(timestamp)}".encode("utf-8")
                ).hexdigest()[:8]
                touchpoint_id = f"TP-{digest}"
            existing = self._touchpoints.get(touchpoint_id)
            if existing is not None:
                return {
                    "ok": True,
                    "created": False,
                    "duplicate": True,
                    "message": f"触点 {touchpoint_id} 已存在，重复上报不重复计权",
                    "touchpoint": self._public_touchpoint(existing),
                }
            self._seq["touchpoint"] += 1
            touchpoint = {
                "touchpoint_id": touchpoint_id,
                "user_id": user_id,
                "channel": channel,
                "timestamp": format_timestamp(timestamp),
                "source": payload.get("source") or "external",
                "properties": _copy(payload.get("properties") or {}),
                "_dt": timestamp,
            }
            self._touchpoints[touchpoint_id] = touchpoint
            return {
                "ok": True,
                "created": True,
                "duplicate": False,
                "message": f"触点 {touchpoint_id} 已登记",
                "touchpoint": self._public_touchpoint(touchpoint),
            }

    def add_touchpoints(self, payloads: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
        return [self.add_touchpoint(payload) for payload in payloads]

    def list_touchpoints(self, *, user_id: str | None = None) -> list[dict[str, Any]]:
        with self._lock:
            rows = sorted(self._touchpoints.values(), key=lambda item: (item["_dt"], item["touchpoint_id"]))
            return [
                self._public_touchpoint(tp) for tp in rows
                if user_id is None or tp["user_id"] == user_id
            ]

    # ---------- 事件（漏斗步骤数据） ----------

    def add_event(self, payload: dict[str, Any]) -> dict[str, Any]:
        user_id = str(payload.get("user_id") or payload.get("用户编号") or "").strip()
        event_name = str(payload.get("event_name") or payload.get("事件名称") or "").strip()
        if not user_id or not event_name:
            raise ValueError("事件需要 user_id 与 event_name")
        timestamp = parse_timestamp(payload.get("timestamp") or payload.get("事件时间"))
        with self._lock:
            event_id = str(payload.get("event_id") or "").strip()
            if not event_id:
                digest = hashlib.sha1(
                    f"{user_id}|{event_name}|{format_timestamp(timestamp)}".encode("utf-8")
                ).hexdigest()[:10]
                event_id = f"EV-{digest}"
            if event_id in self._events:
                return {"ok": True, "created": False, "duplicate": True,
                        "message": "事件已存在", "event": _copy(self._events[event_id])}
            self._seq["event"] += 1
            event = {
                "event_id": event_id,
                "user_id": user_id,
                "event_name": event_name,
                "timestamp": format_timestamp(timestamp),
                "properties": _copy(payload.get("properties") or {}),
                "_dt": timestamp,
            }
            self._events[event_id] = event
            return {"ok": True, "created": True, "duplicate": False,
                    "message": "事件已登记", "event": self._public_event(event)}

    def add_events(self, payloads: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
        return [self.add_event(payload) for payload in payloads]

    def list_events(self) -> list[dict[str, Any]]:
        with self._lock:
            rows = sorted(self._events.values(), key=lambda item: (item["_dt"], item["event_id"]))
            return [self._public_event(event) for event in rows]

    # ---------- 归因重算 / 版本 ----------

    def recompute(
        self,
        *,
        window_minutes: int | float | None = None,
        window_hours: int | float | None = None,
        window_days: int | float | None = None,
        algorithm: str | None = None,
        confirm: bool = False,
        actor: str = "分析师",
        note: str = "",
    ) -> dict[str, Any]:
        """按当前订单/触点重算逐笔贡献。``confirm`` 时在同一把锁内落确认版本。"""
        if window_minutes is None:
            if window_hours is not None:
                window_minutes = float(window_hours) * 60
            elif window_days is not None:
                window_minutes = float(window_days) * 24 * 60
        with self._lock:
            version = self._build_version_locked(
                window_minutes=window_minutes,
                algorithm=algorithm,
                actor=actor,
                note=note,
            )
            if confirm:
                self._confirm_locked(version)
            return self._public_version(version, include_contributions=True)

    def confirm(self, version_id: int | str | None = None, *, actor: str = "分析师") -> dict[str, Any]:
        with self._lock:
            if version_id is None and self._versions:
                # 未显式指定时确认「最近一次重算」的版本，而不是当前生效版本，
                # 这样「重算 -> 确认」是一步自然的操作。
                version = max(self._versions.values(), key=lambda item: item["version_id"])
            else:
                version = self._resolve_version_locked(version_id)
            if version is None:
                raise ValueError("不存在可确认的归因明细版本")
            if version["status"] == "confirmed":
                return self._public_version(version, include_contributions=True)
            if version["status"] == "superseded":
                raise ValueError(f"归因版本 {version['version_label']} 已被后续版本替代，不能再次确认")
            self._confirm_locked(version, actor=actor)
            return self._public_version(version, include_contributions=True)

    def list_versions(self, *, status: str | None = None) -> list[dict[str, Any]]:
        with self._lock:
            versions = sorted(self._versions.values(), key=lambda item: item["version_id"])
            return [
                self._public_version(version, include_contributions=False)
                for version in versions
                if status is None or version["status"] == status
            ]

    def get_version(self, version_id: int | str, *, include_contributions: bool = True) -> dict[str, Any] | None:
        with self._lock:
            version = self._versions.get(int(version_id)) if str(version_id).isdigit() else None
            if version is None:
                for candidate in self._versions.values():
                    if candidate["version_label"] == str(version_id):
                        version = candidate
                        break
            if version is None:
                return None
            return self._public_version(version, include_contributions=include_contributions)

    def list_contributions(
        self,
        *,
        version_id: int | str | None = None,
        order_id: str | None = None,
        user_id: str | None = None,
        channel: str | None = None,
        confirmed_only: bool = False,
    ) -> list[dict[str, Any]]:
        """归因贡献视图：按订单列出窗口、触点与权重。"""
        with self._lock:
            version = self._resolve_version_locked(version_id, confirmed_only=confirmed_only)
            if version is None:
                return []
            rows: list[dict[str, Any]] = []
            for contribution in version["contributions"]:
                if order_id and contribution["order_id"] != order_id:
                    continue
                if user_id and contribution["user_id"] != user_id:
                    continue
                if channel:
                    weights = contribution.get("channel_weights", {})
                    if channel not in weights or float(weights.get(channel, 0)) <= 0:
                        continue
                item = _copy(contribution)
                item["version_id"] = version["version_id"]
                item["version_label"] = version["version_label"]
                item["version_status"] = version["status"]
                item["version_status_label"] = STATUS_LABELS.get(version["status"], version["status"])
                item["confirmed"] = version["status"] in CONFIRMED_STATUSES
                item["window_minutes"] = version["window_minutes"]
                rows.append(item)
            return rows

    def diff_versions(self, from_version: int | str | None, to_version: int | str | None) -> dict[str, Any]:
        with self._lock:
            source = self._resolve_version_locked(from_version)
            target = self._resolve_version_locked(to_version)
            if source is None or target is None:
                raise ValueError("用于对比的归因版本不存在")
            before = {row["order_id"]: row for row in source["contributions"]}
            after = {row["order_id"]: row for row in target["contributions"]}
            changed = []
            for order_id in sorted(set(before) | set(after)):
                old = before.get(order_id)
                new = after.get(order_id)
                if old is None or new is None or old.get("channel_weights") != new.get("channel_weights"):
                    changed.append({
                        "order_id": order_id,
                        "before": _copy(old),
                        "after": _copy(new),
                    })
            return {
                "from_version": source["version_label"],
                "to_version": target["version_label"],
                "changed_orders": changed,
                "changed_order_ids": [item["order_id"] for item in changed],
                "conversion_count_before": source["conversion_count"],
                "conversion_count_after": target["conversion_count"],
            }

    # ---------- 渠道复盘 ----------

    def create_review(
        self,
        payload: dict[str, Any],
        *,
        steps: list[str] | None = None,
        actor: str = "分析师",
    ) -> dict[str, Any]:
        """按渠道 + 一份已确认归因明细发起复盘。

        返回的 ``version_id`` / ``version_snapshot`` 是本次实际领取的归因版本，
        事后可从复盘记录里回溯当时的窗口和触点。
        """
        channel = str(payload.get("channel") or payload.get("渠道") or "").strip()
        if not channel:
            raise ValueError("缺少渠道 channel")
        version_ref = payload.get("version_id", payload.get("attribution_version"))
        batch_id = str(payload.get("batch_id") or payload.get("批次编号") or "").strip()
        strict = bool(payload.get("strict", False))
        requested_steps = steps if steps is not None else payload.get("steps")

        with self._lock:
            version = self._resolve_version_locked(version_ref, confirmed_only=True)
            if version is None:
                raise ValueError("渠道复盘只能按一份已确认的归因明细进行，请先确认归因版本")

            self._seq["review"] += 1
            review_id = self._seq["review"]
            batch = batch_id or DEFAULT_REVIEW_BATCH
            now = datetime.utcnow()

            selected = [
                contribution for contribution in version["contributions"]
                if float(contribution.get("channel_weights", {}).get(channel, 0.0)) > 0
            ]
            conversion_order_ids = [row["order_id"] for row in selected]
            population = sorted({row["user_id"] for row in selected})

            claims: list[dict[str, Any]] = []
            conflicts: list[dict[str, Any]] = []
            accepted: list[dict[str, Any]] = []
            batch_claims = self._claims.setdefault(batch, {})

            for contribution in selected:
                order_id = contribution["order_id"]
                existing = batch_claims.get(order_id)
                if existing is not None and int(existing["version_id"]) != int(version["version_id"]):
                    conflicts.append({
                        "order_id": order_id,
                        "channel": channel,
                        "reason": "归因更正过程中，同一批次的同一订单已按另一归因版本领取",
                        "claimed_by_channel": existing["channel"],
                        "claimed_version_id": existing["version_id"],
                        "claimed_version_label": existing["version_label"],
                        "requested_version_id": version["version_id"],
                        "requested_version_label": version["version_label"],
                    })
                    continue
                if existing is None:
                    batch_claims[order_id] = {
                        "order_id": order_id,
                        "version_id": version["version_id"],
                        "version_label": version["version_label"],
                        "channel": channel,
                        "channels": [channel],
                        "review_id": review_id,
                        "claimed_at": format_timestamp(now),
                    }
                else:
                    if channel not in existing["channels"]:
                        existing["channels"].append(channel)
                accepted.append(contribution)
                claims.append({
                    "order_id": order_id,
                    "channel": channel,
                    "version_id": version["version_id"],
                    "version_label": version["version_label"],
                    "status": "已领取",
                })

            steps_list = self._normalize_steps(requested_steps)
            events = payload.get("events") or payload.get("user_events")
            event_rows = self._event_rows_locked(events)
            funnel = self._compute_funnel_locked(accepted, steps_list, event_rows)

            try:
                checksum_ok = _stable_checksum({
                    "window_minutes": version["window_minutes"],
                    "algorithm": version["algorithm"],
                    "contributions": [
                        {
                            "order_id": row["order_id"],
                            "channel_weights": row["channel_weights"],
                            "touchpoints": [
                                [tp["touchpoint_id"], tp["channel"], tp["weight"]]
                                for tp in row["touchpoints"]
                            ],
                        }
                        for row in version["contributions"]
                    ],
                }) == version["checksum"]
            except (TypeError, KeyError):
                checksum_ok = False

            review = {
                "review_id": review_id,
                "review_label": f"RV-{review_id:04d}",
                "batch_id": batch,
                "channel": channel,
                "status": "部分领取" if conflicts else "已完成",
                "claimed_at": format_timestamp(now),
                "actor": actor,
                "attribution_version": {
                    "version_id": version["version_id"],
                    "version_label": version["version_label"],
                    "status": version["status"],
                    "confirmed_at": version["confirmed_at"],
                    "window_minutes": version["window_minutes"],
                    "window": _copy(version["window"]),
                    "algorithm": version["algorithm"],
                    "checksum": version["checksum"],
                },
                "version_verified": checksum_ok,
                "version_ids_used": [version["version_id"]],
                "conversion_order_ids": [row["order_id"] for row in accepted],
                "requested_order_ids": conversion_order_ids,
                "conversion_count": len(accepted),
                "population": {
                    "user_ids": sorted({row["user_id"] for row in accepted}),
                    "user_count": len({row["user_id"] for row in accepted}),
                },
                "conversion_event_range": funnel["event_range"],
                "orders": [
                    {
                        "order_id": row["order_id"],
                        "user_id": row["user_id"],
                        "order_time": row["order_time"],
                        "window": _copy(row["window"]),
                        "channel_weights": _copy(row["channel_weights"]),
                        "touchpoints": _copy(row["touchpoints"]),
                    }
                    for row in accepted
                ],
                "touchpoint_snapshot": [
                    tp for row in accepted for tp in _copy(row["touchpoints"])
                ],
                "steps": steps_list,
                "step_counts": funnel["step_counts"],
                "cumulative_counts": funnel["cumulative_counts"],
                "step_rates": funnel["step_rates"],
                "total_conversion_rate": funnel["total_conversion_rate"],
                "claims": claims,
                "conflicts": conflicts,
                "partial": bool(conflicts),
            }
            self._reviews[review_id] = review
            if conflicts and strict:
                raise ReviewConflict(review)
            return _copy(review)

    def compute_channel_funnel(self, payload: dict[str, Any]) -> dict[str, Any]:
        """只计算不复盘：按渠道 + 已确认归因明细算各步人数。

        与 ``create_review`` 的区别是不写入领取记录，供页面预览或临时核对。
        """
        channel = str(payload.get("channel") or payload.get("渠道") or "").strip()
        if not channel:
            raise ValueError("缺少渠道 channel")
        with self._lock:
            version = self._resolve_version_locked(
                payload.get("version_id", payload.get("attribution_version")),
                confirmed_only=True,
            )
            if version is None:
                raise ValueError("转化漏斗只能读取一份已确认的归因明细")
            selected = [
                row for row in version["contributions"]
                if float(row.get("channel_weights", {}).get(channel, 0.0)) > 0
            ]
            steps = self._normalize_steps(payload.get("steps"))
            events = self._event_rows_locked(payload.get("events") or payload.get("user_events"))
            funnel = _compute_funnel_for(selected, steps, events)
            return {
                "channel": channel,
                "attribution_version": version["version_label"],
                "version_id": version["version_id"],
                "window_minutes": version["window_minutes"],
                "conversion_order_ids": [row["order_id"] for row in selected],
                "population": {
                    "user_ids": sorted({row["user_id"] for row in selected}),
                    "user_count": len({row["user_id"] for row in selected}),
                },
                "conversion_event_range": funnel["event_range"],
                "steps": steps,
                "step_counts": funnel["step_counts"],
                "cumulative_counts": funnel["cumulative_counts"],
                "step_rates": funnel["step_rates"],
                "total_conversion_rate": funnel["total_conversion_rate"],
            }

    def list_reviews(self) -> list[dict[str, Any]]:
        with self._lock:
            return [_copy(review) for review in sorted(self._reviews.values(), key=lambda item: item["review_id"])]

    def get_review(self, review_id: int) -> dict[str, Any] | None:
        with self._lock:
            review = self._reviews.get(int(review_id))
            return _copy(review) if review else None

    def list_claims(self, batch_id: str | None = None) -> dict[str, Any]:
        """查看批次内的订单领取情况，用于核对复盘实际领取的归因版本。"""
        with self._lock:
            if batch_id is not None:
                claims = _copy(self._claims.get(batch_id, {}))
                return {"batch_id": batch_id, "claims": list(claims.values()),
                        "total": len(claims)}
            batches = {
                name: [_copy(claim) for claim in claims.values()]
                for name, claims in self._claims.items()
            }
            return {"batches": batches,
                    "total": sum(len(items) for items in batches.values())}

    def release_batch(self, batch_id: str) -> dict[str, Any]:
        with self._lock:
            released = self._claims.pop(batch_id, {})
            return {"ok": True, "batch_id": batch_id,
                    "released_orders": sorted(released), "message": "批次领取已释放"}

    # ---------- 内部 ----------

    def _public_order(self, order: dict[str, Any]) -> dict[str, Any]:
        return {key: value for key, value in order.items() if not key.startswith("_")}

    def _public_touchpoint(self, tp: dict[str, Any]) -> dict[str, Any]:
        return {key: value for key, value in tp.items() if not key.startswith("_")}

    def _public_event(self, event: dict[str, Any]) -> dict[str, Any]:
        return {key: value for key, value in event.items() if not key.startswith("_")}

    def _sorted_orders(self) -> list[dict[str, Any]]:
        return sorted(self._orders.values(), key=lambda item: (item["_order_dt"], item["order_id"]))

    def _conversion_count_locked(self) -> int:
        return sum(1 for order in self._orders.values() if _is_valid_order(order))

    def _resolve_version_locked(
        self,
        version_id: int | str | None,
        *,
        confirmed_only: bool = False,
    ) -> dict[str, Any] | None:
        if version_id is not None and str(version_id).strip() != "":
            text = str(version_id).strip()
            candidate = None
            if text.isdigit():
                candidate = self._versions.get(int(text))
            if candidate is None:
                for version in self._versions.values():
                    if version["version_label"] == text:
                        candidate = version
                        break
            if candidate is None:
                return None
            if confirmed_only and candidate["status"] not in CONFIRMED_STATUSES:
                return None
            return candidate

        candidates = [
            version for version in self._versions.values()
            if version["status"] in ("confirmed", "superseded")
        ]
        if confirmed_only:
            # 默认取「当前生效」的已确认版本；历史已确认版本（后来被更正
            # 版本替代）仍可按 id 精确复盘，以便事后还原当时的窗口与触点。
            active = [version for version in candidates if version.get("active")]
            if active:
                return max(active, key=lambda item: item["version_id"])
            return max(candidates, key=lambda item: item["version_id"]) if candidates else None

        if not candidates:
            drafts = list(self._versions.values())
            return max(drafts, key=lambda item: item["version_id"]) if drafts else None
        return max(candidates, key=lambda item: item["version_id"])

    def _build_version_locked(
        self,
        *,
        window_minutes: int | float | None,
        algorithm: str | None,
        actor: str,
        note: str,
    ) -> dict[str, Any]:
        self._correction_in_progress = True
        try:
            previous_confirmed = sorted(
                (
                    version for version in self._versions.values()
                    if version["status"] in CONFIRMED_STATUSES
                ),
                key=lambda item: item["version_id"],
            )
            base_window = (
                previous_confirmed[-1]["window_minutes"] if previous_confirmed
                else DEFAULT_WINDOW_MINUTES
            )
            window = int(window_minutes if window_minutes is not None else base_window)
            if window <= 0:
                raise ValueError("归因窗口必须大于 0 分钟")
            base_algorithm = (
                previous_confirmed[-1]["algorithm"] if previous_confirmed
                else "linear"
            )
            algorithm_value = _normalize_algorithm(algorithm or base_algorithm)

            contributions: list[dict[str, Any]] = []
            for order in self._sorted_orders():
                if not _is_valid_order(order):
                    continue
                order_dt: datetime = order["_order_dt"]
                window_start = order_dt - timedelta(minutes=window)
                usable = [
                    tp for tp in self._touchpoints.values()
                    if tp["user_id"] == order["user_id"]
                    and window_start <= tp["_dt"] <= order_dt
                ]
                weighted = compute_order_weights(usable, order_dt, algorithm_value)
                if not weighted:
                    weighted = [{
                        "touchpoint_id": f"DIRECT-{order['order_id']}",
                        "user_id": order["user_id"],
                        "channel": DIRECT_CHANNEL,
                        "timestamp": order["order_time"],
                        "source": "fallback",
                        "weight": 1.0,
                    }]
                channel_weights: dict[str, float] = {}
                for tp in weighted:
                    channel_weights[tp["channel"]] = round(
                        channel_weights.get(tp["channel"], 0.0) + float(tp["weight"]), 6
                    )
                total = round(sum(channel_weights.values()), 6)
                if total != 1.0:
                    # 防御性归一，任何情况下每笔有效转化的渠道权重合计都是 1。
                    first_channel = next(iter(channel_weights))
                    channel_weights[first_channel] = round(
                        channel_weights[first_channel] + (1.0 - total), 6
                    )
                contributions.append({
                    "order_id": order["order_id"],
                    "user_id": order["user_id"],
                    "order_time": order["order_time"],
                    "amount": order.get("amount", 0),
                    "status": order.get("status", "有效"),
                    "valid": True,
                    "window": {
                        "start": format_timestamp(window_start),
                        "end": order["order_time"],
                        "minutes": window,
                    },
                    "touchpoints": _copy(weighted),
                    "channel_weights": channel_weights,
                    "total_weight": 1.0,
                })

            previous = max(
                self._versions.values(),
                key=lambda item: item["version_id"],
                default=None,
            )
            changed_orders: list[str] = []
            if previous is not None:
                before = {row["order_id"]: row["channel_weights"] for row in previous["contributions"]}
                after = {row["order_id"]: row["channel_weights"] for row in contributions}
                changed_orders = sorted(
                    order_id for order_id in set(before) | set(after)
                    if before.get(order_id) != after.get(order_id)
                )

            self._seq["version"] += 1
            version_id = self._seq["version"]
            checksum = _stable_checksum({
                "window_minutes": window,
                "algorithm": algorithm_value,
                "contributions": [
                    {
                        "order_id": row["order_id"],
                        "channel_weights": row["channel_weights"],
                        "touchpoints": [
                            [tp["touchpoint_id"], tp["channel"], tp["weight"]]
                            for tp in row["touchpoints"]
                        ],
                    }
                    for row in contributions
                ],
            })
            version = {
                "version_id": version_id,
                "version_label": f"v{version_id}",
                "status": "draft",
                "active": False,
                "algorithm": algorithm_value,
                "window_minutes": window,
                "window": {"minutes": window, "lookback_only": True},
                "created_at": format_timestamp(datetime.utcnow()),
                "confirmed_at": None,
                "confirmed_by": None,
                "superseded_by": None,
                "created_by": actor,
                "note": note,
                "orders": [self._public_order(order) for order in self._sorted_orders() if _is_valid_order(order)],
                "contributions": contributions,
                "conversion_count": len(contributions),
                "changed_order_ids": changed_orders,
                "checksum": checksum,
            }
            self._versions[version_id] = version
            return version
        finally:
            self._correction_in_progress = False

    def _confirm_locked(self, version: dict[str, Any], *, actor: str = "分析师") -> None:
        for other in self._versions.values():
            if other["version_id"] == version["version_id"]:
                continue
            if other.get("active"):
                other["active"] = False
            if other["status"] == "confirmed" and other["version_id"] < version["version_id"]:
                other["status"] = "superseded"
                other["superseded_by"] = version["version_id"]
        version["status"] = "confirmed"
        version["active"] = True
        version["confirmed_at"] = format_timestamp(datetime.utcnow())
        version["confirmed_by"] = actor
        # 新的确认版本开启新一轮默认批次，避免更正前的订单领取挡住新口径复盘。
        self._claims.pop(DEFAULT_REVIEW_BATCH, None)
        version["changed_order_ids"] = [
            order_id for order_id in version["changed_order_ids"]
            if any(row["order_id"] == order_id for row in version["contributions"])
        ]

    def _public_version(self, version: dict[str, Any], *, include_contributions: bool) -> dict[str, Any]:
        item = {key: value for key, value in version.items() if key != "contributions"}
        item["status_label"] = STATUS_LABELS.get(version["status"], version["status"])
        item["confirmed"] = version["status"] in CONFIRMED_STATUSES
        if include_contributions:
            item["contributions"] = _copy(version["contributions"])
            item["contribution_count"] = len(version["contributions"])
        return _copy(item)

    def _normalize_steps(self, steps: Any) -> list[str]:
        if not steps:
            return list(DEFAULT_FUNNEL_STEPS)
        normalized: list[str] = []
        for step in steps:
            if isinstance(step, dict):
                name = step.get("event_name") or step.get("name") or step.get("事件名称")
            else:
                name = step
            text = str(name or "").strip()
            if text:
                normalized.append(text)
        return normalized or list(DEFAULT_FUNNEL_STEPS)

    def _event_rows_locked(self, events: Any) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        if isinstance(events, dict):
            for user_id, items in events.items():
                for item in items or []:
                    timestamp = item.get("timestamp") or item.get("event_time")
                    name = item.get("event_name") or item.get("name")
                    if name and timestamp:
                        rows.append({
                            "user_id": str(item.get("user_id") or user_id),
                            "event_name": str(name),
                            "_dt": parse_timestamp(timestamp),
                        })
        elif isinstance(events, list):
            for item in events:
                timestamp = item.get("timestamp") or item.get("event_time")
                name = item.get("event_name") or item.get("name")
                user_id = item.get("user_id")
                if name and timestamp and user_id:
                    rows.append({
                        "user_id": str(user_id),
                        "event_name": str(name),
                        "_dt": parse_timestamp(timestamp),
                    })
        for event in self._events.values():
            rows.append({
                "user_id": event["user_id"],
                "event_name": event["event_name"],
                "_dt": event["_dt"],
            })
        return rows

    def _compute_funnel_locked(
        self,
        contributions: list[dict[str, Any]],
        steps: list[str],
        events: list[dict[str, Any]],
    ) -> dict[str, Any]:
        return _compute_funnel_for(contributions, steps, events)

    # ---------- 测试/演示辅助 ----------

    def reset(self) -> None:
        with self._lock:
            self._orders.clear()
            self._touchpoints.clear()
            self._events.clear()
            self._versions.clear()
            self._reviews.clear()
            self._claims.clear()
            self._seq = {key: 0 for key in self._seq}

    def state(self) -> dict[str, Any]:
        with self._lock:
            return {
                "orders": len(self._orders),
                "conversions": self._conversion_count_locked(),
                "touchpoints": len(self._touchpoints),
                "events": len(self._events),
                "versions": len(self._versions),
                "confirmed_versions": sum(
                    1 for version in self._versions.values() if version["status"] == "confirmed"
                ),
                "reviews": len(self._reviews),
                "correction_in_progress": self._correction_in_progress,
            }


def build_order_contributions(
    orders: Iterable[dict[str, Any]],
    touchpoints: Iterable[dict[str, Any]],
    *,
    window_minutes: int = DEFAULT_WINDOW_MINUTES,
    algorithm: str = "linear",
) -> list[dict[str, Any]]:
    """纯函数式入口：给定订单与触点，直接产出逐笔归因贡献明细。

    不写入全局账本，适合在离线分析或测试里按同一口径核对结果。
    """
    scratch = AttributionLedger()
    scratch.submit_orders(orders)
    scratch.add_touchpoints(touchpoints)
    version = scratch.recompute(
        window_minutes=window_minutes, algorithm=algorithm, confirm=True
    )
    return version["contributions"]


def review_channel_funnel(
    version: dict[str, Any],
    channel: str,
    events: Iterable[dict[str, Any]],
    steps: Iterable[str] | None = None,
) -> dict[str, Any]:
    """从一份已确认的归因明细出发，按渠道圈定人群与事件范围再算各步人数。"""
    if version.get("status") not in CONFIRMED_STATUSES:
        raise ValueError("渠道复盘需要一份已确认的归因明细")
    selected = [
        row for row in version.get("contributions", [])
        if float(row.get("channel_weights", {}).get(channel, 0.0)) > 0
    ]
    step_list = list(steps) if steps else list(DEFAULT_FUNNEL_STEPS)
    parsed_events = [
        {**event, "_dt": parse_timestamp(event["timestamp"])}
        for event in events
        if event.get("timestamp")
    ]
    funnel = _compute_funnel_for(selected, step_list, parsed_events)
    return {
        "channel": channel,
        "attribution_version": version.get("version_label"),
        "window_minutes": version.get("window_minutes"),
        "conversion_order_ids": [row["order_id"] for row in selected],
        "population": sorted({row["user_id"] for row in selected}),
        "conversion_event_range": funnel["event_range"],
        "steps": step_list,
        "step_counts": funnel["step_counts"],
        "cumulative_counts": funnel["cumulative_counts"],
        "step_rates": funnel["step_rates"],
        "total_conversion_rate": funnel["total_conversion_rate"],
    }


class ReviewConflict(Exception):
    """严格模式下，同一批次同一订单被不同归因版本领取时抛出。"""

    def __init__(self, review: dict[str, Any]) -> None:
        super().__init__("同一批次的同一订单已被另一归因版本领取")
        self.review = review


ledger = AttributionLedger()


# 便于领域测试直接调用的薄封装
def submit_order(payload: dict[str, Any], **kwargs: Any) -> dict[str, Any]:
    return ledger.submit_order(payload, **kwargs)


def add_touchpoint(payload: dict[str, Any]) -> dict[str, Any]:
    return ledger.add_touchpoint(payload)


def add_event(payload: dict[str, Any]) -> dict[str, Any]:
    return ledger.add_event(payload)


def recompute(**kwargs: Any) -> dict[str, Any]:
    return ledger.recompute(**kwargs)


def confirm(version_id: int | str | None = None, **kwargs: Any) -> dict[str, Any]:
    return ledger.confirm(version_id, **kwargs)


def create_review(payload: dict[str, Any], **kwargs: Any) -> dict[str, Any]:
    return ledger.create_review(payload, **kwargs)


def list_contributions(**kwargs: Any) -> list[dict[str, Any]]:
    return ledger.list_contributions(**kwargs)



def _compute_funnel_for(
    contributions: list[dict[str, Any]],
    steps: list[str],
    events: list[dict[str, Any]],
) -> dict[str, Any]:
    """按已选订单的归因窗口圈定人群与事件范围，再算各步骤人数。"""
    population = {row["user_id"] for row in contributions}
    windows = [
        (parse_timestamp(row["window"]["start"]), parse_timestamp(row["window"]["end"]))
        for row in contributions
    ]
    if windows:
        range_start = min(start for start, _ in windows)
        range_end = max(end for _, end in windows)
    else:
        range_start = range_end = None

    if range_start is not None:
        relevant = [
            event for event in events
            if event["user_id"] in population
            and range_start <= event["_dt"] <= range_end
        ]
    else:
        relevant = []

    step_counts: list[int] = []
    for step in steps:
        users = {event["user_id"] for event in relevant if event["event_name"] == step}
        step_counts.append(len(users))

    # 严格漏斗：下一步必须发生在上一步之后，且同一用户逐步保留。
    cumulative: list[int] = []
    retained: dict[str, datetime] = {}
    for index, step in enumerate(steps):
        if index == 0:
            retained = {
                event["user_id"]: event["_dt"]
                for event in relevant
                if event["event_name"] == step
            }
        else:
            next_retained: dict[str, datetime] = {}
            for user_id, last_dt in retained.items():
                candidates = [
                    event["_dt"] for event in relevant
                    if event["user_id"] == user_id
                    and event["event_name"] == step
                    and event["_dt"] >= last_dt
                ]
                if candidates:
                    next_retained[user_id] = min(candidates)
            retained = next_retained
        cumulative.append(len(retained))

    rates: list[float] = []
    for index, count in enumerate(cumulative):
        if index == 0:
            rates.append(1.0 if count > 0 else 0.0)
        else:
            previous = cumulative[index - 1]
            rates.append(round(count / previous, 4) if previous else 0.0)

    total_rate = (
        round(cumulative[-1] / cumulative[0], 4)
        if cumulative and cumulative[0] > 0 else 0.0
    )
    return {
        "event_range": {
            "start": format_timestamp(range_start) if range_start else None,
            "end": format_timestamp(range_end) if range_end else None,
        },
        "step_counts": step_counts,
        "cumulative_counts": cumulative,
        "step_rates": rates,
        "total_conversion_rate": total_rate,
    }
