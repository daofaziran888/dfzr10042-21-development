"""归因分析领域服务：模型配置 + 逐笔订单归因贡献明细。"""
from __future__ import annotations

from typing import Any

from app.attribution import ledger
from app.services.base import BaseModuleService


class AttributionModelService(BaseModuleService):
    MODULE = "attribution_model"
    ENTITY = "归因模型"
    OPERATION_TERM = "校验"
    LIST_FIELDS = ["模型编号", "模型名称", "归因算法", "归因窗口", "归因维度", "分母事件", "分子事件", "模型状态"]
    REQUIRED_FIELDS = ["模型编号", "模型名称", "归因算法"]
    STATUS_ORDER = ["草稿", "生效中", "模型失效", "已停用"]
    ACTION_RULES = {"启效模型": "生效中", "标记失效": "模型失效", "停用模型": "已停用"}
    NEGATIVE_ACTIONS = ["停用模型"]

    # ---------- 逐笔订单归因贡献 ----------

    def submit_orders(self, payloads: list[dict[str, Any]], *, actor: str = "分析师") -> list[dict[str, Any]]:
        if not isinstance(payloads, list) or not payloads:
            raise ValueError("请至少提交一笔订单")
        return ledger.submit_orders(payloads, actor=actor)

    def list_orders(self) -> list[dict[str, Any]]:
        return ledger.list_orders()

    def add_touchpoints(self, payloads: list[dict[str, Any]]) -> list[dict[str, Any]]:
        if not isinstance(payloads, list) or not payloads:
            raise ValueError("请至少提交一个触点")
        return ledger.add_touchpoints(payloads)

    def list_touchpoints(self, *, user_id: str | None = None) -> list[dict[str, Any]]:
        return ledger.list_touchpoints(user_id=user_id)

    def add_events(self, payloads: list[dict[str, Any]]) -> list[dict[str, Any]]:
        if not isinstance(payloads, list) or not payloads:
            raise ValueError("请至少提交一个事件")
        return [ledger.add_event(payload) for payload in payloads]

    def list_events(self) -> list[dict[str, Any]]:
        return ledger.list_events()

    def recompute(self, payload: dict[str, Any]) -> dict[str, Any]:
        """归因重算：整批订单 + 触点 + 窗口一次性生成新版本。"""
        return ledger.recompute(
            window_minutes=payload.get("window_minutes"),
            window_hours=payload.get("window_hours"),
            window_days=payload.get("window_days"),
            algorithm=payload.get("algorithm") or payload.get("attribution_algorithm"),
            confirm=bool(payload.get("confirm")),
            actor=str(payload.get("actor") or "分析师"),
            note=str(payload.get("note") or ""),
        )

    def confirm_version(self, version_id: int | str | None, *, actor: str = "分析师") -> dict[str, Any]:
        return ledger.confirm(version_id, actor=actor)

    def list_versions(self, *, status: str | None = None) -> list[dict[str, Any]]:
        return ledger.list_versions(status=status)

    def get_version(self, version_id: int | str) -> dict[str, Any] | None:
        return ledger.get_version(version_id)

    def list_contributions(
        self,
        *,
        version_id: int | str | None = None,
        order_id: str | None = None,
        user_id: str | None = None,
        channel: str | None = None,
        confirmed_only: bool = False,
    ) -> list[dict[str, Any]]:
        return ledger.list_contributions(
            version_id=version_id,
            order_id=order_id,
            user_id=user_id,
            channel=channel,
            confirmed_only=confirmed_only,
        )

    def diff_versions(self, from_version: int | str | None, to_version: int | str | None) -> dict[str, Any]:
        return ledger.diff_versions(from_version, to_version)

    def ledger_snapshot(self) -> dict[str, Any]:
        return {"module": self.MODULE, "entity": self.ENTITY, **ledger.state()}
