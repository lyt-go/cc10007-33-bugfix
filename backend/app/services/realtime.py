"""实时决策业务规则：状态流转、字段校验、动作裁决与筛选口径都收在这里。

状态口径：草稿 → 规则评审中 → 评审通过 → 已生效 → 待重新裁决/已暂停。
「生效规则」必须先完成评审（评审通过），不能从草稿或评审中直接跳到已生效；
生效前会重新读取关联策略的触发条件做一次动作冲突裁决，退役策略不进入候选动作。
"""
from __future__ import annotations

from datetime import datetime
from typing import Any

from app.store import store
from app.services import decision

MODULE = "realtime"
EXEC_MODULE = "realtime_exec"
REQUIRED_FIELDS = ["规则编号", "触发事件", "决策条件"]
STATUS_ORDER = ["草稿", "规则评审中", "评审通过", "已生效", "待重新裁决", "已暂停"]
ACTION_RULES = {
    "提交规则评审": "规则评审中",
    "评审通过": "评审通过",
    "生效规则": "已生效",
    "暂停规则": "已暂停",
}
NEGATIVE_ACTIONS = ["暂停规则"]
# 每个动作允许的前置状态；退役策略回写的「待重新裁决」只能通过重新评审后生效。
ALLOWED_FROM = {
    "提交规则评审": {"草稿", "已暂停", "待重新裁决"},
    "评审通过": {"规则评审中"},
    "生效规则": {"评审通过"},
    "暂停规则": {"已生效", "待重新裁决"},
}


class RealtimeService:
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
            rows = [row for row in rows if keyword in str(row.get("规则编号", ""))]
        if status:
            rows = [row for row in rows if row.get("status") == status]
        total = len(rows)
        start = max(page - 1, 0) * size
        policies = store.rows("tacticlib")
        return [self._view(row, policies) for row in rows[start:start + size]], total

    def _view(self, row: dict[str, Any], policies: list[dict[str, Any]] | None = None) -> dict[str, Any]:
        """列表/详情共用一份展示口径，当前动作一律取自最新裁决，不读旧缓存。"""
        view = dict(row)
        snapshot = decision.arbitrate(view, policies if policies is not None else store.rows("tacticlib"))
        view["裁决状态"] = snapshot["裁决状态"]
        view["当前执行动作"] = snapshot["最终动作"]
        view["动作来源"] = snapshot["动作来源"]
        view["待裁决原因"] = snapshot["待裁决原因"]
        view["裁决依据"] = snapshot["裁决依据"]
        view["动作冲突"] = snapshot["动作冲突"]
        # 仍在生效状态但没有可用动作来源时，展示口径同步转「待重新裁决」，避免页面自相矛盾。
        if view.get("status") == decision.RULE_ACTIVE and snapshot["裁决状态"] == decision.RE_ARBITRATE:
            view["status"] = decision.RULE_PENDING
            view["pending"] = True
        # 未生效（草稿/评审中/评审通过/已暂停）的规则不展示执行动作，裁决结果只作预演。
        if view["status"] not in (decision.RULE_ACTIVE, decision.RULE_PENDING):
            view["当前执行动作"] = ""
            view["动作来源"] = ""
        return view

    def get_entry(self, entry_id: int) -> dict[str, Any] | None:
        entry = store.find(MODULE, entry_id)
        if entry is None:
            return None
        return self._view(entry)

    def arbitrate_entry(self, entry_id: int) -> dict[str, Any] | None:
        """按最新策略与规则状态重算裁决，不直接改写规则。"""
        entry = store.find(MODULE, entry_id)
        if entry is None:
            return None
        return decision.arbitrate(entry, store.rows("tacticlib"))

    def detail(self, entry_id: int) -> dict[str, Any] | None:
        """实时决策详情：规则状态 + 动作冲突裁决 + 执行明细。"""
        entry = store.find(MODULE, entry_id)
        if entry is None:
            return None
        snapshot = decision.arbitrate(entry, store.rows("tacticlib"))
        executions = [
            dict(row) for row in store.rows(EXEC_MODULE)
            if str(row.get("规则编号") or "").strip() == str(entry.get("规则编号") or "").strip()
        ]
        return {
            "rule": self._view(entry),
            "arbitration": snapshot,
            "executions": executions,
        }

    def create_entry(self, values: dict[str, Any]) -> tuple[dict[str, Any] | None, list[str]]:
        missing = [field for field in REQUIRED_FIELDS if not str(values.get(field) or "").strip()]
        if missing:
            return None, missing
        rows = store.rows(MODULE)
        if any(str(row.get("规则编号") or "").strip() == str(values.get("规则编号") or "").strip()
               for row in rows):
            return None, ["规则编号已存在"]
        priority = values.get("优先级")
        if priority not in (None, ""):
            try:
                priority = int(priority)
            except (TypeError, ValueError):
                return None, ["优先级必须是非负整数"]
            if priority < 0 or priority > 100:
                return None, ["优先级需在 0~100 之间"]
        else:
            priority = 0
        entry = {"id": max((int(row.get("id", 0)) for row in rows), default=0) + 1}
        entry.update({field: values.get(field) for field in REQUIRED_FIELDS})
        entry.update({
            "执行动作": values.get("执行动作"),
            "冷却时间秒": values.get("冷却时间秒"),
            "优先级": priority,
            "分流比例": values.get("分流比例"),
            "生效时段": values.get("生效时段"),
            "关联策略": str(values.get("关联策略") or "").strip(),
        })
        entry["status"] = STATUS_ORDER[0]
        entry["pending"] = True
        entry["abnormal"] = False
        entry["裁决状态"] = "未生效"
        entry["当前执行动作"] = ""
        entry["动作来源"] = ""
        entry["待裁决原因"] = ""
        rows.append(entry)
        return self._view(entry), []

    def run_action(self, entry_id: int, action: str) -> tuple[dict[str, Any] | None, str]:
        entry = store.find(MODULE, entry_id)
        if entry is None:
            return None, f"决策规则 {entry_id} 不存在或已归档"
        if action not in ACTION_RULES:
            return None, f"动作「{action}」不属于实时决策可执行范围"
        current = str(entry.get("status") or "").strip()
        allowed = ALLOWED_FROM[action]
        if current not in allowed:
            return None, (
                f"决策规则当前状态为{current}，不能执行「{action}」；"
                f"该动作仅允许从{'、'.join(sorted(allowed))}发起"
            )
        policies = store.rows("tacticlib")
        if action == "生效规则":
            snapshot = decision.arbitrate(entry, policies)
            if snapshot["裁决状态"] != decision.DECIDED:
                reason = snapshot["待裁决原因"] or snapshot["策略核对"]["不可用原因"] or "动作来源未确定"
                return None, f"关联策略不可用，无法生效：{reason}"
        entry["status"] = ACTION_RULES[action]
        entry["pending"] = entry["status"] not in ("已生效",)
        entry["abnormal"] = action in NEGATIVE_ACTIONS
        decision.apply_arbitration(entry, policies)
        return self._view(entry), f"决策规则已{action}"

    # ------------------------------------------------------------------ 执行明细
    def list_executions(
        self,
        *,
        rule_code: str | None = None,
        policy_code: str | None = None,
        page: int = 1,
        size: int = 20,
    ) -> tuple[list[dict[str, Any]], int]:
        rows = store.rows(EXEC_MODULE)
        if rule_code:
            rows = [row for row in rows if row.get("规则编号") == rule_code]
        if policy_code:
            rows = [row for row in rows if row.get("策略编号") == policy_code]
        total = len(rows)
        start = max(page - 1, 0) * size
        return [dict(row) for row in rows[start:start + size]], total

    def replay_hit(
        self,
        entry_id: int,
        *,
        trigger: str | None = None,
        idempotent_key: str | None = None,
    ) -> tuple[dict[str, Any] | None, str]:
        """回放一次命中：只有已生效且裁决确定的规则才会产生执行明细。

        退役策略不参与新命中；带幂等键的重复命中只归并到已有明细，不重复累计使用次数。
        """
        entry = store.find(MODULE, entry_id)
        if entry is None:
            return None, f"决策规则 {entry_id} 不存在或已归档"
        policies = store.rows("tacticlib")
        snapshot = decision.apply_arbitration(entry, policies)
        rule_code = str(entry.get("规则编号") or "").strip()
        incoming = str(trigger or entry.get("决策条件") or "").strip()
        rows = store.rows(EXEC_MODULE)
        if idempotent_key:
            existing = next(
                (row for row in rows
                 if row.get("幂等键") == idempotent_key and row.get("规则编号") == rule_code),
                None,
            )
            if existing is not None:
                return dict(existing), "重复命中已按幂等键归并，未新增执行明细"
        if snapshot["裁决状态"] != decision.DECIDED:
            return None, f"规则{rule_code}当前不可命中：{snapshot['待裁决原因'] or '待重新裁决'}"
        if snapshot["策略核对"]["策略状态"] == decision.POLICY_RETIRED:
            return None, f"策略{snapshot['关联策略']}已退役，不参与新命中"
        if not snapshot["策略核对"]["可用"]:
            return None, f"关联策略不可用：{snapshot['策略核对']['不可用原因']}"
        if incoming != snapshot["策略核对"]["策略触发条件"]:
            return None, "触发条件不匹配，未命中"
        if str(entry.get("status") or "") != decision.RULE_ACTIVE:
            return None, f"规则{rule_code}状态为{entry.get('status')}，不参与当前命中"

        policy_code = snapshot["关联策略"]
        policy = decision.find_bound_policy(entry, policies) or {}
        row = {
            "id": max((int(item.get("id", 0)) for item in rows), default=0) + 1,
            "明细编号": f"EXEC-{len(rows) + 1:04d}",
            "规则编号": rule_code,
            "策略编号": policy_code,
            "触发条件": incoming,
            "命中动作": snapshot["最终动作"],
            "命中时间": datetime.now().replace(microsecond=0).isoformat(),
            "命中类型": "当前命中",
            "执行状态": "已执行",
            "执行结果": "已按裁决动作执行",
            "幂等键": idempotent_key or f"HIT-{rule_code}-{len(rows) + 1:04d}",
            "来源": snapshot["动作来源"],
        }
        rows.append(row)
        policy["使用次数"] = int(policy.get("使用次数") or 0) + 1
        decision.apply_arbitration(entry, policies)
        return dict(row), "命中已回放并写入执行明细"
