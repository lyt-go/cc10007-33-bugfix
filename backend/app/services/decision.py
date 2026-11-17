"""实时决策的动作冲突裁决。

裁决只依赖两份权威数据：策略库里的运营策略（触发条件、执行动作、状态）和实时决策里的
规则状态。每次裁决都从策略记录重新读取触发条件，不复用规则上缓存的旧动作，因此退役、
发布、再次裁决的先后顺序不会改变最终结果。

状态口径：
    草稿 / 规则评审中 / 评审通过 / 已生效 / 待重新裁决 / 已暂停
只有「已生效」的规则参与当前命中；「已退役」的策略不进入候选动作，只保留历史命中。
"""
from __future__ import annotations

from typing import Any

POLICY_PUBLISHED = "已发布"
POLICY_RETIRED = "已退役"
RULE_ACTIVE = "已生效"
RULE_PENDING = "待重新裁决"
RULE_FROZEN = ("草稿", "规则评审中", "评审通过", "已暂停")

DECIDED = "已裁决"
RE_ARBITRATE = "待重新裁决"


def _text(value: Any) -> str:
    return str(value or "").strip()


def find_bound_policy(rule: dict[str, Any], policies: list[dict[str, Any]]) -> dict[str, Any] | None:
    """按规则上的关联策略找到策略记录；关联缺失时退回按触发条件匹配。

    关联字段可能指向一条已经归档/改名的策略，因此这里同时保留触发条件比对结果，
    让调用方能看出“规则还挂着一个失效引用”。
    """
    code = _text(rule.get("关联策略"))
    if code:
        for policy in policies:
            if _text(policy.get("策略编号")) == code:
                return policy
    rule_trigger = _text(rule.get("决策条件"))
    if not rule_trigger:
        return None
    for policy in policies:
        if _text(policy.get("触发条件")) == rule_trigger:
            return policy
    return None


def arbitrate(rule: dict[str, Any], policies: list[dict[str, Any]]) -> dict[str, Any]:
    """读取策略与规则的最新状态，计算一次完整的动作冲突裁决。"""
    policy = find_bound_policy(rule, policies)
    policy_code = _text(policy.get("策略编号")) if policy else _text(rule.get("关联策略"))
    rule_trigger = _text(rule.get("决策条件"))
    policy_trigger = _text(policy.get("触发条件")) if policy else ""

    policy_check: dict[str, Any] = {
        "策略编号": policy_code,
        "策略状态": _text(policy.get("status")) if policy else "未关联",
        "策略触发条件": policy_trigger,
        "规则触发条件": rule_trigger,
        "触发条件一致": bool(policy and policy_trigger == rule_trigger),
        "可用": False,
        "不可用原因": "",
    }
    if policy is None:
        policy_check["不可用原因"] = "关联策略不存在" if policy_code else ""
    elif _text(policy.get("status")) == POLICY_RETIRED:
        policy_check["不可用原因"] = "策略已退役，不参与当前命中"
    elif _text(policy.get("status")) != POLICY_PUBLISHED:
        policy_check["不可用原因"] = f"策略状态为{_text(policy.get('status'))}，尚未发布"
    elif not policy_check["触发条件一致"]:
        policy_check["不可用原因"] = "策略触发条件与规则决策条件不一致"
    else:
        policy_check["可用"] = True

    # 关联策略曾绑定但当前不可用时，规则自身的旧执行动作也要一并失效，不能留着当兜底。
    binding_broken = bool(policy_code) and not policy_check["可用"]
    candidates: list[dict[str, str]] = []
    if policy_check["可用"]:
        candidates.append({"来源": f"策略库:{policy_code}", "动作": _text(policy.get("执行动作"))})
    rule_status = _text(rule.get("status"))
    if not binding_broken and rule_status == RULE_ACTIVE and _text(rule.get("执行动作")):
        candidates.append({"来源": f"决策规则:{_text(rule.get('规则编号'))}", "动作": _text(rule.get("执行动作"))})

    distinct = sorted({item["动作"] for item in candidates})
    conflict = len(distinct) > 1
    if not candidates:
        reason = policy_check["不可用原因"] or "规则未生效且没有已发布策略"
        if rule_status in RULE_FROZEN:
            # 尚未走到生效环节的规则只是在登记/评审/暂停，不需要挂待裁决。
            state = "未生效"
            action = ""
            source = ""
            basis = "规则未生效"
            reason = ""
        else:
            state = RE_ARBITRATE
            action = ""
            source = f"策略退役:{policy_code}" if policy_check["策略状态"] == POLICY_RETIRED else ""
            basis = "无可用动作来源"
    elif conflict:
        # 冲突裁决口径固定：策略库结论驱动实时决策详情，动作相同时合并来源。
        winner = next(item for item in candidates if item["来源"].startswith("策略库:"))
        state = DECIDED
        action = winner["动作"]
        source = winner["来源"]
        basis = "动作冲突，策略库结论优先"
        reason = f"规则动作与策略动作不一致：{'、'.join(distinct)}"
    else:
        state = DECIDED
        action = distinct[0]
        source = "、".join(item["来源"] for item in candidates)
        basis = "动作一致" if len(candidates) > 1 else "唯一可用动作来源"
        reason = ""

    return {
        "规则编号": _text(rule.get("规则编号")),
        "规则状态": rule_status,
        "关联策略": policy_code,
        "策略核对": policy_check,
        "候选动作": candidates,
        "动作冲突": conflict,
        "冲突动作": distinct if conflict else [],
        "裁决状态": state,
        "最终动作": action,
        "动作来源": source,
        "裁决依据": basis,
        "待裁决原因": reason,
    }


def apply_arbitration(rule: dict[str, Any], policies: list[dict[str, Any]]) -> dict[str, Any]:
    """把裁决结果写回规则：当前执行动作、动作来源、待裁决原因同时更新。

    已生效但不再有可用来源的规则会转成「待重新裁决」，避免继续返回旧动作。
    """
    snapshot = arbitrate(rule, policies)
    rule["裁决状态"] = snapshot["裁决状态"]
    rule["当前执行动作"] = snapshot["最终动作"]
    rule["动作来源"] = snapshot["动作来源"]
    rule["待裁决原因"] = snapshot["待裁决原因"]
    rule["裁决依据"] = snapshot["裁决依据"]
    if snapshot["裁决状态"] == RE_ARBITRATE and _text(rule.get("status")) == RULE_ACTIVE:
        rule["status"] = RULE_PENDING
        rule["pending"] = True
        snapshot["规则状态"] = RULE_PENDING
    return snapshot
