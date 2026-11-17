# 智能运营决策分析平台

面向用户行为序列分析、RFM分层、流失预警、智能推荐、归因建模、活动效果评估与实时运营决策的数据分析工程平台。

这是一个前后端分离的管理平台：前端 React + Vite + TypeScript，后端 FastAPI（Python）。
两边各自独立启动，前端 dev server 已关掉自动打开页面，启动后按终端打印的地址手工打开。

## 目录结构

```text
.
├── frontend/                 React + Vite + TypeScript 前端
│   ├── src/views/            每个业务模块一个页面
│   ├── src/api/              统一请求封装
│   ├── src/stores/           会话与筛选状态
│   └── vite.config.ts        dev server 配置（open: false）
├── backend/                  FastAPI（Python） 后端
│   ├── app/routers/          每个业务模块一组接口
│   ├── app/services/         业务规则与状态流转
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
npm install
npm run dev
```

前端默认监听 `http://127.0.0.1:5173/`，dev server 不会自动打开浏览器，
需要自己访问。`/api` 由 vite 代理到后端 `http://127.0.0.1:8000`。

## 业务模块

| 模块 | 目录 | 业务对象 | 主要字段 |
| --- | --- | --- | --- |
| 行为序列 | `sequence` | 序列模式 | 模式编号、用户分群、事件序列 |
| RFM分层 | `rfm` | 分层规则 | 规则编号、最近消费天界、消费频次界 |
| 流失预警 | `churn` | 预警模型 | 模型编号、模型名称、特征变量 |
| 智能推荐 | `recommend` | 推荐策略 | 策略编号、推荐场景、召回算法 |
| 归因建模 | `attrmodel` | 归因模型 | 模型编号、模型名称、归因逻辑 |
| 活动评估 | `activity` | 活动方案 | 方案编号、活动名称、活动类型 |
| 实时决策 | `realtime` | 决策规则 | 规则编号、触发事件、决策条件 |
| 预算分配 | `budget` | 预算方案 | 方案编号、预算总额、渠道分配比例 |
| LTV估算 | `lifetime` | LTV模型 | 模型编号、模型名称、估算方法 |
| 实验变体 | `abvariant` | 变体方案 | 变体编号、关联实验、变体名称 |
| 运营看板 | `dashboard` | 看板视图 | 视图编号、视图名称、指标组合 |
| 评分卡 | `scorecard` | 评分卡 | 评分编号、评分对象、特征变量组 |
| 聚类分群 | `cluster` | 聚类任务 | 任务编号、聚类算法、特征维度 |
| 对比分析 | `comparison` | 对比组 | 对比编号、对比名称、实验组筛选 |
| 数据漂移 | `datadrift` | 漂移检测 | 检测编号、监控数据集、基线版本 |
| 推送计划 | `pushplan` | 推送方案 | 方案编号、推送内容、目标人群 |
| 指标归因 | `metricdrill` | 归因分析 | 分析编号、目标指标、波动方向 |
| 策略库 | `tacticlib` | 运营策略 | 策略编号、策略名称、策略类型 |

## 约定

- 每个模块的前端页面在 `frontend/src/views/<模块>/index.vue`，后端接口在
  `backend/app/routers/<模块>.py`，业务规则在 `backend/app/services/<模块>.py`。
- 列表接口统一返回 `{ items, total, page, size }`，动作接口统一返回 `{ ok, message }`。
- 状态流转只允许在 `app/services` 里改，路由层不做业务判断。

## 策略库与实时决策的联动口径

- 策略库状态：草稿 → 策略评审中 → 已发布 → 已退役。只有「策略评审中」可以发布，
  只有「已发布」可以退役，已退役不可回退或再次发布。
- 实时决策状态：草稿 → 规则评审中 → 评审通过 → 已生效 → 待重新裁决/已暂停。
  「生效规则」只能从「评审通过」发起，不能跳过评审。
- 动作冲突裁决在 `backend/app/services/decision.py`：每次都从策略记录重新读取触发条件，
  退役策略不进入候选动作。策略库结论驱动最终动作；规则与策略动作不一致时按策略库口径裁决，
  结果与「先解除退役动作 / 先写入新规则动作」的顺序无关。
- 策略退役会同时改写：策略状态、使用次数（只保留历史命中）、关联规则状态（转待重新裁决）、
  规则当前执行动作（清空），并留下退役回执与批次核对序列。
- 相关接口：
  - `GET /api/tacticlib/{id}/reconcile`：以策略为对象的批次核对（状态、规则裁决、执行明细、退役回执）。
  - `GET /api/tacticlib/retirements`：策略退役记录。
  - `GET /api/realtime/{id}/detail`：实时决策详情（规则状态 + 动作冲突裁决 + 执行明细）。
  - `GET /api/realtime/executions`：实时决策执行明细数据集，支持按 `rule_code`/`policy_code` 过滤。
  - `POST /api/realtime/{id}/replay`：回放命中，退役策略不参与；支持 `幂等键` 归并重复命中。

## 测试

```bash
cd backend && .venv/bin/python -m unittest discover -s tests
```

回归用例覆盖草稿发布、退役传播、重复命中与当前动作回放。


领域：数据工程
子方向：数据分析与数据应用
