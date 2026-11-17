"""策略库业务规则：状态流转、字段校验、退役传播与筛选口径都收在这里。

状态口径：草稿 → 策略评审中 → 已发布 → 已退役。只有「策略评审中」可以发布，
只有「已发布」可以退役；已退役策略不可回退、不可再次发布，也不再参与任何新命中。
"""
from __future__ import annotations

from datetime import datetime
from typing import Any

from app.store import store
from app.services import decision

MODULE = "tacticlib"
EXEC_MODULE = "realtime_exec"
RETIRE_MODULE = "tactic_retire"
REQUIRED_FIELDS = ["策略编号", "策略名称", "策略类型"]
STATUS_ORDER = ["草稿", "策略评审中", "已发布", "已退役"]
ACTION_RULES = {"提交策略评审": "策略评审中", "发布策略": "已发布", "退役策略": "已退役"}
NEGATIVE_ACTIONS = []

# 规范口径：策略评分 0~100、使用次数非负、生效周期为 起~止 且起不晚于止。
SCORE_MIN = 0.0
SCORE_MAX = 100.0
USAGE_MAX = 1_000_000


def _text(value: Any) -> str:
    return str(value or "").strip()


def _number(value: Any, *, label: str) -> tuple[float | None, str]:
    if value is None or _text(value) == "":
        return None, ""
    try:
        return float(value), ""
    except (TypeError, ValueError):
        return None, f"{label}必须是数值"


class TacticlibService:
    def list_entries(
        self,
        *,
        keyword: str | None = None,
        status: str | None = None,
        page: int = 1,
        size: int = 20,
    ) -> tuple[list[dict[str, Any]], int]:
        rows = store.rows(MODULE)
        if keyword:
            rows = [row for row in rows if keyword in str(row.get("策略编号", ""))]
        if status:
            rows = [row for row in rows if row.get("status") == status]
        total = len(rows)
        start = max(page - 1, 0) * size
        # 返回副本，避免列表页拿到的行被后续动作原地改写而出现重复/丢失的错觉。
        return [dict(row) for row in rows[start:start + size]], total

    def get_entry(self, entry_id: int) -> dict[str, Any] | None:
        entry = store.find(MODULE, entry_id)
        return dict(entry) if entry else None

    def _validate(self, values: dict[str, Any]) -> tuple[dict[str, Any], list[str]]:
        cleaned: dict[str, Any] = {}
        problems: list[str] = []
        score, error = _number(values.get("策略评分"), label="策略评分")
        if error:
            problems.append(error)
        elif score is not None:
            if not SCORE_MIN <= score <= SCORE_MAX:
                problems.append(f"策略评分需在 {SCORE_MIN:g}~{SCORE_MAX:g} 之间")
            else:
                cleaned["策略评分"] = score
        usage, error = _number(values.get("使用次数"), label="使用次数")
        if error:
            problems.append(error)
        elif usage is not None:
            if usage < 0 or usage > USAGE_MAX:
                problems.append(f"使用次数需在 0~{USAGE_MAX} 之间")
            else:
                cleaned["使用次数"] = int(usage)
        cycle = _text(values.get("生效周期"))
        if cycle:
            parts = cycle.split("~")
            if len(parts) != 2 or not all(part.strip() for part in parts):
                problems.append("生效周期需按「起始日期~结束日期」填写")
            elif parts[0].strip() > parts[1].strip():
                problems.append("生效周期的起始日期不能晚于结束日期")
            else:
                cleaned["生效周期"] = cycle
        return cleaned, problems

    def create_entry(self, values: dict[str, Any]) -> tuple[dict[str, Any] | None, list[str]]:
        missing = [field for field in REQUIRED_FIELDS if not _text(values.get(field))]
        if missing:
            return None, missing
        cleaned, problems = self._validate(values)
        if problems:
            return None, problems
        rows = store.rows(MODULE)
        if any(_text(row.get("策略编号")) == _text(values.get("策略编号")) for row in rows):
            return None, ["策略编号已存在"]
        entry = {"id": max((int(row.get("id", 0)) for row in rows), default=0) + 1}
        entry.update({field: values.get(field) for field in REQUIRED_FIELDS})
        entry.update({
            "触发条件": values.get("触发条件"),
            "执行动作": values.get("执行动作"),
            "关联规则": _text(values.get("关联规则")),
        })
        entry.update({key: value for key, value in cleaned.items()})
        entry.setdefault("使用次数", 0)
        entry["status"] = STATUS_ORDER[0]
        entry["pending"] = True
        entry["abnormal"] = False
        rows.append(entry)
        return dict(entry), []

    def run_action(
        self,
        entry_id: int,
        action: str,
        values: dict[str, Any] | None = None,
    ) -> tuple[dict[str, Any] | None, str]:
        entry = store.find(MODULE, entry_id)
        if entry is None:
            return None, f"运营策略 {entry_id} 不存在或已归档"
        if action not in ACTION_RULES:
            return None, f"动作「{action}」不属于策略库可执行范围"
        current = _text(entry.get("status"))
        if current == "已退役":
            return None, "运营策略已退役，不能再次发布或回退状态"
        if action == "提交策略评审" and current != "草稿":
            return None, f"只有草稿可以提交策略评审，当前状态为{current}"
        if action == "发布策略" and current != "策略评审中":
            return None, f"只有策略评审中的策略可以发布，当前状态为{current}"
        if action == "退役策略" and current != "已发布":
            return None, f"只有已发布策略可以退役，当前状态为{current}"
        if action == "发布策略" and not (_text(entry.get("触发条件")) and _text(entry.get("执行动作"))):
            return None, "发布策略前需先补齐触发条件与执行动作"

        cleaned, problems = self._validate(values or {})
        if problems:
            return None, "；".join(problems)
        entry.update(cleaned)

        if action == "退役策略":
            message = self._retire(entry)
            return dict(entry), message

        entry["status"] = ACTION_RULES[action]
        entry["pending"] = entry["status"] != STATUS_ORDER[-1]
        entry["abnormal"] = action in NEGATIVE_ACTIONS
        self._propagate_policy(entry)
        return dict(entry), f"运营策略已{action}"

    # ------------------------------------------------------------------ 传播
    def _propagate_policy(self, policy: dict[str, Any]) -> None:
        """策略发布/改字段后，关联规则重新裁决，动作与裁决状态一起改写。"""
        policies = store.rows(MODULE)
        code = _text(policy.get("策略编号"))
        trigger = _text(policy.get("触发条件"))
        for rule in store.rows("realtime"):
            if _text(rule.get("关联策略")) != code and _text(rule.get("决策条件")) != trigger:
                continue
            decision.apply_arbitration(rule, policies)

    def _retire(self, policy: dict[str, Any]) -> str:
        """退役：冻结策略、撤回当前命中、关联规则转待重新裁决，并留下退役回执。"""
        now = datetime.now().replace(microsecond=0).isoformat()
        code = _text(policy.get("策略编号"))
        policies = store.rows(MODULE)
        retired_usage_before = int(policy.get("使用次数") or 0)

        # 先按退役前的状态拍快照，回执里的“退役前动作”才是真实执行动作。
        bound_rules = [
            rule for rule in store.rows("realtime")
            if _text(rule.get("关联策略")) == code
            or _text(rule.get("决策条件")) == _text(policy.get("触发条件"))
        ]
        before_snapshots = [(rule, decision.arbitrate(rule, policies)) for rule in bound_rules]

        policy["status"] = "已退役"
        policy["pending"] = False
        policy["abnormal"] = False
        policy["退役时间"] = now

        # 当前命中撤回：退役策略只保留历史命中，撤回的明细归档到退役回执里。
        exec_rows = store.rows(EXEC_MODULE)
        kept: list[dict[str, Any]] = []
        withdrawn: list[dict[str, Any]] = []
        for row in exec_rows:
            if _text(row.get("策略编号")) == code and row.get("命中类型") == "当前命中":
                withdrawn.append(dict(row))
            else:
                kept.append(row)
        store.replace_rows(EXEC_MODULE, kept)

        # 使用次数只统计仍然有效的命中，和最终动作、规则状态同一次改写。
        usage_after = sum(1 for row in kept if _text(row.get("策略编号")) == code)
        policy["使用次数"] = usage_after

        affected: list[dict[str, Any]] = []
        for rule, snapshot_before in before_snapshots:
            before_status = _text(rule.get("status"))
            before_action = snapshot_before["最终动作"]
            # 退役传播按最新策略状态重算：已生效规则转「待重新裁决」，冻结中的规则只清旧动作。
            decision.apply_arbitration(rule, policies)
            if _text(rule.get("status")) == decision.RULE_PENDING:
                rule["pending"] = True
                rule["待裁决原因"] = f"关联策略{code}已退役，不参与当前命中"
                rule["动作来源"] = f"策略退役:{code}"
            rule["当前执行动作"] = ""
            affected.append({
                "规则编号": _text(rule.get("规则编号")),
                "退役前状态": before_status,
                "退役后状态": _text(rule.get("status")),
                "退役前动作": before_action,
                "退役后动作": "",
                "裁决状态": _text(rule.get("裁决状态")),
            })

        receipt = {
            "id": max((int(row.get("id", 0)) for row in store.rows(RETIRE_MODULE)), default=0) + 1,
            "回执编号": f"RETR-{int(policy.get('id', 0)):04d}",
            "策略编号": code,
            "策略名称": _text(policy.get("策略名称")),
            "退役时间": now,
            "退役前使用次数": retired_usage_before,
            "退役后使用次数": usage_after,
            "撤回当前命中": withdrawn,
            "影响规则": affected,
            "核对序列": [
                {"序号": 1, "环节": "接收退役回执", "对象": code, "结果": "已接收"},
                {"序号": 2, "环节": "校验状态", "对象": code, "结果": "已发布→已退役"},
                {"序号": 3, "环节": "撤回当前命中", "对象": code,
                 "结果": f"撤回{len(withdrawn)}条，保留历史命中{usage_after}条"},
                {"序号": 4, "环节": "传播规则状态", "对象": "、".join(
                    item["规则编号"] for item in affected) or "无",
                 "结果": "已转待重新裁决" if affected else "无需传播"},
                {"序号": 5, "环节": "回写最终动作", "对象": "、".join(
                    item["规则编号"] for item in affected) or "无",
                 "结果": "已清空退役策略动作"},
            ],
        }
        store.rows(RETIRE_MODULE).append(receipt)
        return f"运营策略已退役，撤回当前命中{len(withdrawn)}条，影响规则{len(affected)}条"

    # ------------------------------------------------------------------ 核对
    def retirement_receipts(self, policy_code: str | None = None) -> list[dict[str, Any]]:
        rows = store.rows(RETIRE_MODULE)
        if policy_code:
            rows = [row for row in rows if _text(row.get("策略编号")) == policy_code]
        return [dict(row) for row in rows]

    def reconcile(self, entry_id: int) -> dict[str, Any] | None:
        """批次核对：以策略为核对对象，串起策略状态、规则裁决与执行明细。"""
        policy = store.find(MODULE, entry_id)
        if policy is None:
            return None
        code = _text(policy.get("策略编号"))
        policies = store.rows(MODULE)
        rules = [
            decision.arbitrate(rule, policies)
            for rule in store.rows("realtime")
            if _text(rule.get("关联策略")) == code
        ]
        executions = [
            dict(row) for row in store.rows(EXEC_MODULE)
            if _text(row.get("策略编号")) == code
        ]
        return {
            "核对对象": code,
            "策略": dict(policy),
            "关联规则裁决": rules,
            "执行明细": executions,
            "退役回执": self.retirement_receipts(code),
            "核对序列": [
                {"序号": 1, "环节": "读取策略状态", "对象": code,
                 "结果": _text(policy.get("status"))},
                {"序号": 2, "环节": "读取触发条件", "对象": code,
                 "结果": _text(policy.get("触发条件")) or "未填写"},
                {"序号": 3, "环节": "核对关联规则", "对象": "、".join(
                    item["规则编号"] for item in rules) or "无", "结果": f"{len(rules)}条"},
                {"序号": 4, "环节": "核对执行明细", "对象": code,
                 "结果": f"当前命中{sum(1 for row in executions if row.get('命中类型') == '当前命中')}条，"
                         f"历史命中{sum(1 for row in executions if row.get('命中类型') == '历史命中')}条"},
                {"序号": 5, "环节": "核对退役回执", "对象": code,
                 "结果": f"{len(self.retirement_receipts(code))}条"},
            ],
        }
