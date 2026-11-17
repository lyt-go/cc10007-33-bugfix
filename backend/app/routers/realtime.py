"""实时决策接口：维护决策规则，覆盖提交规则评审、评审通过、生效规则、暂停规则等动作。"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query

from app.schemas import ActionResult, EntryPayload, PageResult
from app.services.realtime import RealtimeService

router = APIRouter(prefix="/api/realtime", tags=["实时决策"])

service = RealtimeService()

LIST_FIELDS = [
    "规则编号", "触发事件", "决策条件", "执行动作", "冷却时间秒",
    "优先级", "分流比例", "生效时段", "关联策略", "裁决状态", "当前执行动作",
]
STATUSES = ["草稿", "规则评审中", "评审通过", "已生效", "待重新裁决", "已暂停"]


# 固定路径必须注册在 /{entry_id} 之前，否则 export/executions 会被当成 id 解析并返回 422。
@router.get("/export")
def export_entries() -> dict[str, Any]:
    """导出实时决策清单：返回当前过滤条件下的全量数据。"""
    items, total = service.list_entries(page=1, size=10000)
    return {"module": "realtime", "total": total, "items": items}


@router.get("/executions", response_model=PageResult[dict])
def list_executions(
    rule_code: str | None = Query(default=None, description="按规则编号过滤"),
    policy_code: str | None = Query(default=None, description="按策略编号过滤"),
    page: int = 1,
    size: int = 200,
) -> PageResult[dict]:
    """实时决策执行明细数据集：退役策略只保留历史命中，当前命中随退役撤回。"""
    if size > 200:
        raise HTTPException(status_code=400, detail="每页最多 200 条，请缩小分页范围")
    items, total = service.list_executions(
        rule_code=rule_code, policy_code=policy_code, page=page, size=size
    )
    return PageResult(items=items, total=total, page=page, size=size)


@router.get("", response_model=PageResult[dict])
def list_entries(
    keyword: str | None = Query(default=None, description="按规则编号检索"),
    status: str | None = Query(default=None, description="草稿、规则评审中、评审通过、已生效、待重新裁决、已暂停"),
    page: int = 1,
    size: int = 20,
) -> PageResult[dict]:
    """按规则编号与状态过滤实时决策列表；没有数据时返回空页，不报错。"""
    if size > 200:
        raise HTTPException(status_code=400, detail="每页最多 200 条，请缩小分页范围")
    items, total = service.list_entries(keyword=keyword, status=status, page=page, size=size)
    return PageResult(items=items, total=total, page=page, size=size)


@router.get("/{entry_id}", response_model=dict)
def get_entry(entry_id: int) -> dict:
    """读取单条决策规则明细；不存在时给出可读的错误说明。"""
    entry = service.get_entry(entry_id)
    if entry is None:
        raise HTTPException(status_code=404, detail=f"决策规则 {entry_id} 不存在或已归档")
    return entry


@router.get("/{entry_id}/detail", response_model=dict)
def get_detail(entry_id: int) -> dict:
    """实时决策详情：规则状态、动作冲突裁决与执行明细一次返回。"""
    detail = service.detail(entry_id)
    if detail is None:
        raise HTTPException(status_code=404, detail=f"决策规则 {entry_id} 不存在或已归档")
    return detail


@router.post("", response_model=ActionResult)
def create_entry(payload: EntryPayload) -> ActionResult:
    """登记一条决策规则，缺字段时说明原因而不是静默丢弃。"""
    entry, missing = service.create_entry(payload.values)
    if missing:
        return ActionResult(ok=False, message=f"缺少必填字段：{'、'.join(missing)}")
    return ActionResult(ok=True, message="决策规则已登记", entry=entry)


@router.post("/{entry_id}/actions", response_model=ActionResult)
def run_action(entry_id: int, payload: EntryPayload) -> ActionResult:
    """执行规则状态动作；跳过评审直接生效、退役策略参与生效都会被拦下。"""
    action = str(payload.values.get("action") or "").strip()
    entry, message = service.run_action(entry_id, action)
    if entry is None:
        return ActionResult(ok=False, message=message)
    return ActionResult(ok=True, message=message, entry=entry)


@router.post("/{entry_id}/replay", response_model=ActionResult)
def replay_hit(entry_id: int, payload: EntryPayload) -> ActionResult:
    """回放一次命中：退役策略不参与新命中，命中结果落执行明细。"""
    trigger = str(payload.values.get("触发条件") or "").strip() or None
    idempotent_key = str(payload.values.get("幂等键") or "").strip() or None
    entry, message = service.replay_hit(entry_id, trigger=trigger, idempotent_key=idempotent_key)
    if entry is None:
        return ActionResult(ok=False, message=message)
    return ActionResult(ok=True, message=message, entry=entry)
