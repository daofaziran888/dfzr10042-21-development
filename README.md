# 洞察工坊 · 多维交互分析与实验度量平台

面向数据分析师的多维下钻分析、指标口径管理、A/B实验评估、群体圈选分群、归因分析与数据看板构建的交互分析平台。

这是一个前后端分离的管理平台：前端 Vue 3 + Vite + TypeScript，后端 FastAPI（Python）。
两边各自独立启动，前端 dev server 已关掉自动打开页面，启动后按终端打印的地址手工打开。

## 目录结构

```text
.
├── frontend/                 Vue 3 + Vite + TypeScript 前端
│   ├── src/views/WorkbenchView.vue|tsx  全部模块共享工作台
│   ├── src/data/modules.ts              18个业务模块配置
│   ├── src/api/              统一请求封装
│   └── vite.config.ts        dev server 配置（open: false）
├── backend/                  FastAPI（Python） 后端
│   ├── app/routers/resource.py  通用查询/导出/登记/动作接口
│   ├── app/services/<key>.py    每个模块独立领域逻辑
│   ├── app/services/base.py     共享服务骨架
│   └── app/store.py          内存数据仓库与示例数据
├── .gitignore
└── docker-compose.yml
```

## 启动

### 后端

```bash
cd backend
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
./run.sh
```

健康检查：`curl http://127.0.0.1:8000/api/health`

### 前端

```bash
cd frontend
npm ci
npm run dev
```

前端默认监听 `http://127.0.0.1:5173/`，dev server 不会自动打开浏览器，
需要自己访问。`/api` 由 vite 代理到后端 `http://127.0.0.1:8000`。

## 业务模块

| 模块 | 目录 | 业务对象 | 主要字段 |
| --- | --- | --- | --- |
| 指标定义 | `metric_def` | 指标口径 | 指标编号、指标名称、计算口径 |
| 维度定义 | `dimension_def` | 分析维度 | 维度编号、维度名称、字段来源 |
| 多维查询 | `olap_query` | 查询方案 | 方案编号、查询名称、选择指标 |
| 群体圈选 | `segment_builder` | 人群包 | 人群编号、人群名称、圈选条件 |
| A/B实验 | `ab_experiment` | 实验方案 | 方案编号、实验名称、实验分组 |
| 实验分析 | `experiment_result` | 实验报告 | 报告编号、关联实验、指标名称 |
| 归因分析 | `attribution_model` | 归因模型 | 模型编号、模型名称、归因算法 |
| 看板定义 | `dashboard_def` | 数据看板 | 看板编号、看板名称、布局配置 |
| 下钻路径 | `drill_path` | 下钻方案 | 方案编号、父级维度、子级维度 |
| 同期群分析 | `cohort_analysis` | 同期群 | 群组编号、群组名称、划分周期 |
| 数据导出 | `data_export` | 导出任务 | 任务编号、数据来源、导出格式 |
| 查询缓存 | `query_cache` | 缓存条目 | 条目编号、查询哈希、结果行数 |
| 定时分析 | `scheduled_analysis` | 调度任务 | 任务编号、分析方案、Cron表达式 |
| 指标对比 | `metric_compare` | 对比方案 | 方案编号、主指标、对比指标 |
| 异常检测 | `anomaly_detect` | 检测规则 | 规则编号、目标指标、检测算法 |
| 分析快照 | `data_snapshot` | 数据快照 | 快照编号、分析方案、数据行数 |
| 分群比较 | `user_segment` | 比较方案 | 方案编号、群体A、群体B |
| 事件追踪 | `event_track` | 事件定义 | 事件编号、事件名称、事件属性 |
| 排行榜 | `ranking_board` | 排行方案 | 方案编号、排行指标、排行维度 |
| 漏斗分析 | `funnel_def` | 漏斗方案 | 方案编号、步骤序列、转化窗口 |
| 数据集关联 | `dataset_join` | 关联方案 | 方案编号、主数据集、关联数据集 |
| 报告生成 | `report_builder` | 报告模板 | 模板编号、报告名称、引用指标 |

## 归因逐笔明细与渠道复盘

归因分析在模型配置之外提供订单粒度的贡献明细，供漏斗复盘按渠道取数：

- 订单按 `order_id` 幂等登记，重复提交只算一笔有效转化；无效/取消订单
  不进入明细。
- 每笔订单只在自身「回看窗口」内取该用户触点（窗口只回看、不前瞻），
  因此同一用户 09:00 与 12:00 两笔订单互不串窗；11:45 的迟到触点只会
  进入 12:00 那笔的窗口。
- 每笔有效转化的渠道权重合计恒为 1；没有命中触点时记到 `direct`。
- 重算结果先落草稿版本，确认后成为只读快照；渠道复盘只能读取已确认
  版本，并在复盘记录里保存当时的窗口、触点与版本校验值，事后可回溯。
- 复盘批次用于防止更正中途串版本：同一批次内同一订单只能绑定一个已
  确认版本；被更正的订单不会在同批次内被另一渠道按不同版本重复领取。
  确认新版本会释放默认批次 `DEFAULT`。

接口：

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| POST | `/api/attribution_model/orders` | 登记订单（幂等） |
| POST | `/api/attribution_model/touchpoints` | 登记触点（幂等） |
| POST | `/api/attribution_model/recompute` | 归因重算，`confirm=true` 同时确认 |
| POST | `/api/attribution_model/confirm` | 确认最近一次重算版本 |
| GET | `/api/attribution_model/contributions` | 逐笔贡献视图（窗口/触点/权重） |
| GET | `/api/attribution_model/versions` | 归因版本列表 |
| GET | `/api/attribution_model/versions/diff?from=&to=` | 版本差异（哪些订单变了） |
| GET | `/api/funnel_def/funnel?channel=&version_id=` | 按渠道算漏斗各步人数（不写领取） |
| POST | `/api/funnel_def/reviews` | 渠道复盘并登记领取（绑定已确认版本） |
| GET | `/api/funnel_def/reviews` | 复盘记录（含领取版本与快照） |
| GET | `/api/funnel_def/claims` | 批次内订单领取情况 |
| POST | `/api/funnel_def/batches/{batch}/release` | 释放批次领取 |
| GET | `/api/attribution_model/ledger/demo` | 载入 09:00 / 12:00 两笔订单演示数据 |

前端在「归因分析」页提供逐笔贡献视图，在「漏斗分析」页提供渠道复盘与
批次领取核对。

## 约定

- 全部模块共享`frontend/src/views/WorkbenchView.vue|tsx`和一套资源路由。
- 每个模块仍保留独立`backend/app/services/<模块>.py`；纯前端保留
  `frontend/src/domain/<模块>.ts`，用于承载真实业务差异并维持36题位容量。
- 列表接口统一返回 `{ items, total, page, size }`，动作接口统一返回 `{ ok, message }`。
- 状态流转只允许在 `app/services` 里改，路由层不做业务判断。

领域：数据工程
子方向：数据分析与数据应用
