"""漏斗分析领域服务：漏斗方案配置 + 按已确认归因明细的渠道复盘。"""
from __future__ import annotations

from typing import Any

from app.attribution import ledger
from app.services.base import BaseModuleService


class FunnelDefService(BaseModuleService):
    MODULE = "funnel_def"
    ENTITY = "漏斗方案"
    OPERATION_TERM = "计算"
    LIST_FIELDS = ["方案编号", "步骤序列", "转化窗口", "开始事件", "结束事件", "各步转化率", "总转化率", "方案状态"]
    REQUIRED_FIELDS = ["方案编号", "步骤序列", "转化窗口"]
    STATUS_ORDER = ["草稿", "已生成", "步骤缺失", "已归档"]
    ACTION_RULES = {"生成漏斗": "已生成", "标记缺失": "步骤缺失", "归档方案": "已归档"}
    NEGATIVE_ACTIONS = []

    def create_review(self, payload: dict[str, Any], *, actor: str = "分析师") -> dict[str, Any]:
        """渠道复盘：读取一份已确认归因明细，圈定该渠道的转化人群后再算漏斗。"""
        steps = payload.get("steps")
        if steps is None and payload.get("scheme_id"):
            steps = self._steps_from_scheme(payload.get("scheme_id"))
        return ledger.create_review(payload, steps=steps, actor=actor)

    def compute_channel_funnel(self, payload: dict[str, Any]) -> dict[str, Any]:
        """只算漏斗不写领取记录：读取已确认归因明细后按渠道算各步人数。"""
        return ledger.compute_channel_funnel(payload)

    def list_reviews(self) -> list[dict[str, Any]]:
        return ledger.list_reviews()

    def get_review(self, review_id: int) -> dict[str, Any] | None:
        return ledger.get_review(review_id)

    def release_batch(self, batch_id: str) -> dict[str, Any]:
        return ledger.release_batch(batch_id)

    def _steps_from_scheme(self, scheme_id: Any) -> list[str] | None:
        entry = store_find(self.MODULE, scheme_id)
        if entry is None:
            return None
        raw = entry.get("步骤序列")
        if isinstance(raw, list):
            return [str(item) for item in raw]
        text = str(raw or "")
        for separator in ("->", "→", ",", "，", "|", ">", "、"):
            if separator in text:
                return [part.strip() for part in text.split(separator) if part.strip()]
        return [text.strip()] if text.strip() else None


def store_find(module: str, entry_id: Any) -> dict[str, Any] | None:
    from app.store import store

    text = str(entry_id or "").strip()
    if not text:
        return None
    if text.isdigit():
        found = store.find(module, int(text))
        if found is not None:
            return found
    for row in store.rows(module):
        if str(row.get("方案编号")) == text:
            return row
    return None
