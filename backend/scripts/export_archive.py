"""生成故障处理后的实时决策执行明细归档资料。

按批次核对对象（默认 TACT-0003）回放既有调用链，输出：
    - 策略退役记录
    - 关联规则的最终动作裁决
    - 实时决策执行明细数据集（退役后只保留历史命中）
    - 批次核对序列

用法（在 backend 目录下）：
    .venv/bin/python scripts/export_archive.py [策略编号] [输出路径] [--retire]

加 --retire 会先在当前进程里走一遍退役调用链再归档；不加则直接归档现有状态
（对接已起服务的内存数据时用这一种）。
"""
from __future__ import annotations

import json
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.services.realtime import RealtimeService  # noqa: E402
from app.services.tacticlib import TacticlibService  # noqa: E402
from app.store import store  # noqa: E402


def find_policy(policy_code: str) -> dict:
    policy = next(
        (row for row in store.rows("tacticlib") if row.get("策略编号") == policy_code),
        None,
    )
    if policy is None:
        raise SystemExit(f"策略 {policy_code} 不存在")
    return policy


def build_archive(policy_code: str) -> dict:
    tactic = TacticlibService()
    realtime = RealtimeService()
    policy = find_policy(policy_code)

    reconcile = tactic.reconcile(int(policy["id"]))
    executions, _ = realtime.list_executions(policy_code=policy_code, page=1, size=200)
    rules = [
        realtime.detail(int(row["id"]))
        for row in store.rows("realtime")
        if str(row.get("关联策略") or "").strip() == policy_code
    ]
    return {
        "归档说明": "策略退役后的实时决策执行明细与核对序列",
        "归档时间": datetime.now().replace(microsecond=0).isoformat(),
        "批次核对对象": policy_code,
        "策略": {key: policy.get(key) for key in (
            "策略编号", "策略名称", "策略类型", "触发条件", "执行动作", "status", "使用次数"
        )},
        "规则裁决": [item["arbitration"] for item in rules if item],
        "执行明细数据集": executions,
        "退役记录": tactic.retirement_receipts(policy_code),
        "核对序列": reconcile["核对序列"] if reconcile else [],
    }


def main() -> None:
    args = [arg for arg in sys.argv[1:] if not arg.startswith("--")]
    retire = "--retire" in sys.argv
    policy_code = args[0] if args else "TACT-0003"
    target = (
        Path(args[1]) if len(args) > 1
        else Path("artifacts") / f"{policy_code}_reconciliation.json"
    )
    if retire:
        policy = find_policy(policy_code)
        _, message = TacticlibService().run_action(int(policy["id"]), "退役策略")
        print(message)
    target.parent.mkdir(parents=True, exist_ok=True)
    archive = build_archive(policy_code)
    target.write_text(json.dumps(archive, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"已归档 {len(archive['执行明细数据集'])} 条执行明细 -> {target}")


if __name__ == "__main__":
    main()
