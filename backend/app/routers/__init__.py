"""业务路由汇总：归因明细/渠道复盘的专用路径先注册，通用资源路由兜底。"""
from __future__ import annotations

from app.routers import attribution, resource

ROUTERS = [attribution, resource]
