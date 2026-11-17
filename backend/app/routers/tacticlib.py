"""策略库接口：维护运营策略，覆盖提交策略评审、发布策略、退役策略等动作。"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query

from app.schemas import ActionResult, EntryPayload, PageResult
from app.services.tacticlib import TacticlibService

router = APIRouter(prefix="/api/tacticlib", tags=["策略库"])

service = TacticlibService()

LIST_FIELDS = ["策略编号", "策略名称", "策略类型", "触发条件", "执行动作", "生效周期", "策略评分", "使用次数"]
STATUSES = ["草稿", "策略评审中", "已发布", "已退役"]


# 固定路径必须注册在 /{entry_id} 之前，否则 export 会被当成 id 解析并返回 422。
@router.get("/export")
def export_entries() -> dict[str, Any]:
    """导出策略库清单：返回当前过滤条件下的全量数据。"""
    items, total = service.list_entries(page=1, size=10000)
    return {"module": "tacticlib", "total": total, "items": items}


@router.get("/retirements", response_model=dict)
def list_retirements(
    policy_code: str | None = Query(default=None, description="按策略编号过滤退役回执"),
) -> dict[str, Any]:
    """策略退役记录：状态流转、撤回命中与规则传播的完整回执。"""
    receipts = service.retirement_receipts(policy_code)
    return {"total": len(receipts), "items": receipts}


@router.get("", response_model=PageResult[dict])
def list_entries(
    keyword: str | None = Query(default=None, description="按策略编号检索"),
    status: str | None = Query(default=None, description="草稿、策略评审中、已发布、已退役"),
    page: int = 1,
    size: int = 20,
) -> PageResult[dict]:
    """按策略编号与状态过滤策略库列表；没有数据时返回空页，不报错。"""
    if size > 200:
        raise HTTPException(status_code=400, detail="每页最多 200 条，请缩小分页范围")
    items, total = service.list_entries(keyword=keyword, status=status, page=page, size=size)
    return PageResult(items=items, total=total, page=page, size=size)


@router.get("/{entry_id}", response_model=dict)
def get_entry(entry_id: int) -> dict:
    """读取单条运营策略明细；不存在时给出可读的错误说明。"""
    entry = service.get_entry(entry_id)
    if entry is None:
        raise HTTPException(status_code=404, detail=f"运营策略 {entry_id} 不存在或已归档")
    return entry


@router.get("/{entry_id}/reconcile", response_model=dict)
def reconcile(entry_id: int) -> dict:
    """批次核对：以策略为对象串起策略状态、关联规则裁决、执行明细与退役回执。"""
    result = service.reconcile(entry_id)
    if result is None:
        raise HTTPException(status_code=404, detail=f"运营策略 {entry_id} 不存在或已归档")
    return result


@router.post("", response_model=ActionResult)
def create_entry(payload: EntryPayload) -> ActionResult:
    """登记一条运营策略，缺字段时说明原因而不是静默丢弃。"""
    entry, missing = service.create_entry(payload.values)
    if missing:
        return ActionResult(ok=False, message=f"缺少必填字段：{'、'.join(missing)}")
    return ActionResult(ok=True, message="运营策略已登记", entry=entry)


@router.post("/{entry_id}/actions", response_model=ActionResult)
def run_action(entry_id: int, payload: EntryPayload) -> ActionResult:
    """执行策略状态动作；草稿不能直接发布，退役后不能回退。"""
    action = str(payload.values.get("action") or "").strip()
    entry, message = service.run_action(entry_id, action, payload.values)
    if entry is None:
        return ActionResult(ok=False, message=message)
    return ActionResult(ok=True, message=message, entry=entry)
