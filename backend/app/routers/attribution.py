"""归因明细与渠道复盘接口。

逐笔归因明细、触点/订单/事件登记、归因版本重算与确认走 attribution_model；
渠道复盘与漏斗步骤人数走 funnel_def。两组接口都注册在通用资源路由之前，
避免 ``/{module}/{entry_id}`` 的动态段抢先匹配。
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query

from app.attribution import ReviewConflict, ledger

router = APIRouter(prefix="/api", tags=["归因明细与渠道复盘"])


def _bad_request(error: Exception) -> HTTPException:
    return HTTPException(status_code=400, detail=str(error))


# ============================================================
# 归因明细（attribution_model）
# ============================================================

@router.get("/attribution_model/ledger")
def ledger_state() -> dict[str, Any]:
    return ledger.state()


@router.get("/attribution_model/orders")
def list_orders() -> dict[str, Any]:
    orders = ledger.list_orders()
    return {"items": orders, "total": len(orders), "conversions": ledger.state()["conversions"]}


@router.post("/attribution_model/orders")
def submit_orders(payload: dict[str, Any] | list[dict[str, Any]]) -> dict[str, Any]:
    """幂等登记订单；body 可以是单笔对象，也可以是 ``{"orders": [...]}`` 或数组。"""
    try:
        if isinstance(payload, list):
            items = payload
            batch = False
        elif isinstance(payload.get("orders"), list):
            items = payload["orders"]
            batch = True
        else:
            items = [payload]
            batch = False
        results = ledger.submit_orders(items, actor=str(
            (payload.get("actor") if isinstance(payload, dict) else "") or "分析师"
        ))
    except ValueError as error:
        raise _bad_request(error) from error
    if batch or len(results) > 1:
        return {"ok": True, "results": results, "total_conversions": ledger.state()["conversions"]}
    result = dict(results[0])
    result["total_conversions"] = ledger.state()["conversions"]
    return result


@router.get("/attribution_model/touchpoints")
def list_touchpoints(user_id: str | None = Query(default=None)) -> dict[str, Any]:
    items = ledger.list_touchpoints(user_id=user_id)
    return {"items": items, "total": len(items)}


@router.post("/attribution_model/touchpoints")
def add_touchpoints(payload: dict[str, Any] | list[dict[str, Any]]) -> dict[str, Any]:
    try:
        if isinstance(payload, list):
            items = payload
            batch = True
        elif isinstance(payload.get("touchpoints"), list):
            items = payload["touchpoints"]
            batch = True
        else:
            items = [payload]
            batch = False
        results = ledger.add_touchpoints(items)
    except ValueError as error:
        raise _bad_request(error) from error
    if batch or len(results) > 1:
        return {"ok": True, "results": results, "total": len(ledger.list_touchpoints())}
    return results[0]


@router.get("/attribution_model/events")
def list_events() -> dict[str, Any]:
    items = ledger.list_events()
    return {"items": items, "total": len(items)}


@router.post("/attribution_model/events")
def add_events(payload: dict[str, Any] | list[dict[str, Any]]) -> dict[str, Any]:
    try:
        if isinstance(payload, list):
            items = payload
            batch = True
        elif isinstance(payload.get("events"), list):
            items = payload["events"]
            batch = True
        else:
            items = [payload]
            batch = False
        results = [ledger.add_event(item) for item in items]
    except ValueError as error:
        raise _bad_request(error) from error
    if batch or len(results) > 1:
        return {"ok": True, "results": results, "total": len(ledger.list_events())}
    return results[0]


@router.post("/attribution_model/recompute")
def recompute(payload: dict[str, Any]) -> dict[str, Any]:
    """归因重算；``confirm=true`` 时同一次加锁内直接落已确认版本。"""
    try:
        return ledger.recompute(
            window_minutes=payload.get("window_minutes"),
            window_hours=payload.get("window_hours"),
            window_days=payload.get("window_days"),
            algorithm=payload.get("algorithm"),
            confirm=bool(payload.get("confirm")),
            actor=str(payload.get("actor") or "分析师"),
            note=str(payload.get("note") or ""),
        )
    except ValueError as error:
        raise _bad_request(error) from error


@router.post("/attribution_model/confirm")
def confirm_version(payload: dict[str, Any] | None = None) -> dict[str, Any]:
    body = payload or {}
    version_id = body.get("version_id", body.get("version"))
    try:
        return ledger.confirm(version_id, actor=str(body.get("actor") or "分析师"))
    except ValueError as error:
        raise _bad_request(error) from error


@router.get("/attribution_model/versions")
def list_versions(status: str | None = Query(default=None)) -> dict[str, Any]:
    items = ledger.list_versions(status=status)
    return {"items": items, "total": len(items)}


@router.get("/attribution_model/versions/diff")
def diff_versions(
    from_version: str | None = Query(default=None, alias="from"),
    to_version: str | None = Query(default=None, alias="to"),
) -> dict[str, Any]:
    try:
        return ledger.diff_versions(from_version, to_version)
    except ValueError as error:
        raise _bad_request(error) from error


@router.get("/attribution_model/versions/{version_id}")
def get_version(version_id: str) -> dict[str, Any]:
    version = ledger.get_version(version_id)
    if version is None:
        raise HTTPException(status_code=404, detail=f"归因版本 {version_id} 不存在")
    return version


@router.get("/attribution_model/versions/{from_version}/diff/{to_version}")
def diff_two_versions(from_version: str, to_version: str) -> dict[str, Any]:
    try:
        return ledger.diff_versions(from_version, to_version)
    except ValueError as error:
        raise _bad_request(error) from error


@router.get("/attribution_model/contributions")
def list_contributions(
    version_id: str | None = Query(default=None, alias="version_id"),
    order_id: str | None = Query(default=None),
    user_id: str | None = Query(default=None),
    channel: str | None = Query(default=None),
    confirmed_only: bool = Query(default=False),
) -> dict[str, Any]:
    """归因贡献视图：每笔订单的窗口、关联渠道触点与权重。"""
    items = ledger.list_contributions(
        version_id=version_id,
        order_id=order_id,
        user_id=user_id,
        channel=channel,
        confirmed_only=confirmed_only,
    )
    total_weight = round(sum(row.get("total_weight", 0.0) for row in items), 6)
    return {
        "items": items,
        "total": len(items),
        "total_weight": total_weight,
        "conversions": len(items),
    }


@router.get("/attribution_model/ledger/demo")
def demo_scenario(user_id: str = Query(default="u-demo")) -> dict[str, Any]:
    """载入一段可复现的演示数据：同一用户 09:00 / 12:00 两笔订单。

    触点先给到 10:30，随后可补 11:45 的迟到触点并重算，验证只有第二笔
    的贡献发生变化、转化总数仍然为两笔。
    """
    orders = [
        {"order_id": f"{user_id}-0900", "user_id": user_id,
         "order_time": "2026-11-24T09:00:00", "amount": 100, "status": "有效"},
        {"order_id": f"{user_id}-1200", "user_id": user_id,
         "order_time": "2026-11-24T12:00:00", "amount": 200, "status": "有效"},
    ]
    touchpoints = [
        {"touchpoint_id": f"TP-{user_id}-0830", "user_id": user_id, "channel": "自然搜索",
         "timestamp": "2026-11-24T08:30:00"},
        {"touchpoint_id": f"TP-{user_id}-1030", "user_id": user_id, "channel": "信息流广告",
         "timestamp": "2026-11-24T10:30:00"},
    ]
    events = [
        {"event_id": f"EV-{user_id}-0900-visit", "user_id": user_id, "event_name": "访问",
         "timestamp": "2026-11-24T08:20:00"},
        {"event_id": f"EV-{user_id}-0900-cart", "user_id": user_id, "event_name": "加购",
         "timestamp": "2026-11-24T08:40:00"},
        {"event_id": f"EV-{user_id}-0900-order", "user_id": user_id, "event_name": "下单",
         "timestamp": "2026-11-24T09:00:00"},
        {"event_id": f"EV-{user_id}-1200-visit", "user_id": user_id, "event_name": "访问",
         "timestamp": "2026-11-24T10:20:00"},
        {"event_id": f"EV-{user_id}-1200-cart", "user_id": user_id, "event_name": "加购",
         "timestamp": "2026-11-24T11:30:00"},
        {"event_id": f"EV-{user_id}-1200-order", "user_id": user_id, "event_name": "下单",
         "timestamp": "2026-11-24T12:00:00"},
        {"event_id": f"EV-{user_id}-1200-pay", "user_id": user_id, "event_name": "支付",
         "timestamp": "2026-11-24T12:05:00"},
    ]
    ledger.submit_orders(orders)
    ledger.add_touchpoints(touchpoints)
    for event in events:
        ledger.add_event(event)
    version = ledger.recompute(window_minutes=120, algorithm="linear", confirm=True, note="演示初始化")
    return {"ok": True, "user_id": user_id, "version": version, "orders": orders,
            "touchpoints": touchpoints, "events": events}


@router.post("/attribution_model/ledger/demo", include_in_schema=False)
def demo_scenario_post(user_id: str = Query(default="u-demo")) -> dict[str, Any]:
    return demo_scenario(user_id)


# ============================================================
# 渠道复盘（funnel_def）
# ============================================================

@router.get("/funnel_def/funnel")
def compute_funnel(channel: str, version_id: str | None = None,
                   steps: str | None = None) -> dict[str, Any]:
    try:
        return ledger.compute_channel_funnel({
            "channel": channel,
            "version_id": version_id,
            "steps": [item.strip() for item in steps.split(",") if item.strip()] if steps else None,
        })
    except ValueError as error:
        raise _bad_request(error) from error


@router.post("/funnel_def/compute")
def compute_funnel_post(payload: dict[str, Any]) -> dict[str, Any]:
    try:
        return ledger.compute_channel_funnel(payload)
    except ValueError as error:
        raise _bad_request(error) from error


@router.get("/funnel_def/reviews")
def list_reviews() -> dict[str, Any]:
    items = ledger.list_reviews()
    return {"items": items, "total": len(items)}


@router.post("/funnel_def/reviews")
def create_review(payload: dict[str, Any]) -> dict[str, Any]:
    """按渠道 + 一份已确认归因明细复盘，返回实际领取的版本快照。"""
    try:
        return ledger.create_review(payload, actor=str(payload.get("actor") or "分析师"))
    except ValueError as error:
        raise _bad_request(error) from error
    except ReviewConflict as conflict:
        raise HTTPException(status_code=409, detail={
            "message": str(conflict),
            "review": conflict.review,
        }) from conflict


@router.get("/funnel_def/reviews/{review_id}")
def get_review(review_id: int) -> dict[str, Any]:
    review = ledger.get_review(review_id)
    if review is None:
        raise HTTPException(status_code=404, detail=f"渠道复盘 {review_id} 不存在")
    return review


@router.get("/funnel_def/claims")
def list_claims(batch_id: str | None = Query(default=None)) -> dict[str, Any]:
    return ledger.list_claims(batch_id)


@router.get("/attribution_model/claims", include_in_schema=False)
def list_claims_alias(batch_id: str | None = Query(default=None)) -> dict[str, Any]:
    return ledger.list_claims(batch_id)


@router.post("/funnel_def/batches/{batch_id}/release")
def release_batch(batch_id: str) -> dict[str, Any]:
    return ledger.release_batch(batch_id)


# 同一实现的别名，方便调用方按业务语义取数。
@router.get("/funnel_def/channel_reviews", include_in_schema=False)
def list_channel_reviews() -> dict[str, Any]:
    return list_reviews()


@router.post("/funnel_def/channel_reviews", include_in_schema=False)
def create_channel_review(payload: dict[str, Any]) -> dict[str, Any]:
    return create_review(payload)
