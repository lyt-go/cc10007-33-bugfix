"""策略库与实时决策的回归核对：草稿发布、退役传播、重复命中与当前动作回放。"""
from __future__ import annotations

import unittest
from typing import Any

from fastapi.testclient import TestClient

from app.main import app
from app.store import store


def reset_store() -> None:
    store.reset()


class DecisionFlowTest(unittest.TestCase):
    def setUp(self) -> None:
        reset_store()
        self.client = TestClient(app)

    def action(self, module: str, entry_id: int, name: str, **values: Any) -> dict[str, Any]:
        response = self.client.post(
            f"/api/{module}/{entry_id}/actions",
            json={"values": {"action": name, **values}},
        )
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def test_draft_cannot_be_published_directly(self) -> None:
        result = self.action("tacticlib", 1, "发布策略")
        self.assertFalse(result["ok"])
        self.assertIn("策略评审中", result["message"])
        entry = self.client.get("/api/tacticlib/1").json()
        self.assertEqual(entry["status"], "草稿")
        # 提交评审后才能发布，且发布动作会改写关联规则的动作来源。
        self.assertTrue(self.action("tacticlib", 1, "提交策略评审")["ok"])
        published = self.action("tacticlib", 1, "发布策略")
        self.assertTrue(published["ok"])
        self.assertEqual(published["entry"]["status"], "已发布")

    def test_retirement_propagates_and_blocks_new_hits(self) -> None:
        before = self.client.get("/api/realtime/3").json()
        self.assertEqual(before["当前执行动作"], "下发购物车提醒")

        retired = self.action("tacticlib", 3, "退役策略")
        self.assertTrue(retired["ok"], retired)

        rule = self.client.get("/api/realtime/3").json()
        self.assertEqual(rule["status"], "待重新裁决")
        self.assertEqual(rule["当前执行动作"], "")
        self.assertEqual(rule["动作来源"], "策略退役:TACT-0003")
        self.assertIn("已退役", rule["待裁决原因"])

        # 退役策略不参与新命中。
        replay = self.client.post(
            "/api/realtime/3/replay", json={"values": {"触发条件": "购物车停留超过15分钟"}}
        ).json()
        self.assertFalse(replay["ok"])

        executions = self.client.get("/api/realtime/executions?policy_code=TACT-0003").json()["items"]
        self.assertEqual(len(executions), 2)
        self.assertTrue(all(row["命中类型"] == "历史命中" for row in executions))

        receipt = self.client.get("/api/tacticlib/retirements?policy_code=TACT-0003").json()["items"][0]
        self.assertEqual(receipt["退役后使用次数"], 2)
        self.assertTrue(receipt["撤回当前命中"])
        self.assertEqual(receipt["影响规则"][0]["规则编号"], "REAL-0003")
        self.assertEqual(receipt["影响规则"][0]["退役前动作"], "下发购物车提醒")
        self.assertEqual(receipt["影响规则"][0]["退役后动作"], "")

    def test_rule_in_review_cannot_take_effect(self) -> None:
        result = self.action("realtime", 2, "生效规则")
        self.assertFalse(result["ok"])
        self.assertIn("评审通过", result["message"])
        rule = self.client.get("/api/realtime/2").json()
        self.assertEqual(rule["status"], "规则评审中")
        self.assertEqual(rule["当前执行动作"], "")

        self.assertTrue(self.action("realtime", 2, "评审通过")["ok"])
        blocked = self.action("realtime", 2, "生效规则")
        self.assertFalse(blocked["ok"])
        self.assertIn("尚未发布", blocked["message"])

        # 关联策略完成评审并发布后，规则才能带着确定的动作生效。
        self.assertTrue(self.action("tacticlib", 2, "发布策略")["ok"])
        self.assertTrue(self.action("realtime", 2, "生效规则")["ok"])
        rule = self.client.get("/api/realtime/2").json()
        self.assertEqual(rule["status"], "已生效")
        self.assertEqual(rule["当前执行动作"], "推荐相似商品")

    def test_action_conflict_resolves_to_policy_and_replay_is_idempotent(self) -> None:
        rule = store.find("realtime", 3)
        rule["执行动作"] = "推送优惠券"
        detail = self.client.get("/api/realtime/3/detail").json()
        self.assertTrue(detail["arbitration"]["动作冲突"])
        self.assertEqual(detail["arbitration"]["最终动作"], "下发购物车提醒")
        self.assertEqual(detail["arbitration"]["动作来源"], "策略库:TACT-0003")

        first = self.client.post(
            "/api/realtime/3/replay", json={"values": {"触发条件": "购物车停留超过15分钟"}}
        ).json()
        self.assertTrue(first["ok"])
        self.assertEqual(first["entry"]["命中动作"], "下发购物车提醒")
        self.assertEqual(store.find("tacticlib", 3)["使用次数"], 4)

        blocked = self.client.post(
            "/api/realtime/3/replay", json={"values": {"触发条件": "不匹配的条件"}}
        ).json()
        self.assertFalse(blocked["ok"])

        # 退役后当前命中撤回，使用次数只保留历史命中。
        self.action("tacticlib", 3, "退役策略")
        executions = self.client.get("/api/realtime/executions?policy_code=TACT-0003").json()["items"]
        self.assertEqual(len(executions), 2)
        self.assertEqual(store.find("tacticlib", 3)["使用次数"], 2)

    def test_duplicate_hit_with_same_key_is_merged(self) -> None:
        payload = {"values": {"触发条件": "购物车停留超过15分钟", "幂等键": "HIT-DUP-1"}}
        first = self.client.post("/api/realtime/3/replay", json=payload).json()
        self.assertTrue(first["ok"])
        usage_after_first = store.find("tacticlib", 3)["使用次数"]
        second = self.client.post("/api/realtime/3/replay", json=payload).json()
        self.assertTrue(second["ok"])
        self.assertIn("归并", second["message"])
        self.assertEqual(store.find("tacticlib", 3)["使用次数"], usage_after_first)
        self.assertEqual(
            len(self.client.get("/api/realtime/executions?policy_code=TACT-0003").json()["items"]), 4
        )

    def test_confirmed_hits_keep_original_policy(self) -> None:
        detail = self.client.get("/api/realtime/3/detail").json()
        historical = [row for row in detail["executions"] if row["命中类型"] == "历史命中"]
        self.assertTrue(historical)
        self.assertTrue(all(row["策略编号"] == "TACT-0003" for row in historical))
        # 已确认的历史命中不因再次裁决或退役而改写动作。
        self.action("tacticlib", 3, "退役策略")
        after = self.client.get("/api/realtime/executions?policy_code=TACT-0003").json()["items"]
        self.assertEqual(
            sorted(row["命中动作"] for row in after),
            sorted(row["命中动作"] for row in historical),
        )

    def test_rule_bound_to_retired_policy_is_never_hit(self) -> None:
        """规则仍指向已退役策略时，回放与列表都按待重新裁决处理。"""
        self.action("tacticlib", 3, "退役策略")
        store.find("realtime", 3)["status"] = "已生效"
        store.find("realtime", 3)["当前执行动作"] = "下发购物车提醒"
        rule = self.client.get("/api/realtime/3").json()
        self.assertEqual(rule["裁决状态"], "待重新裁决")
        self.assertEqual(rule["status"], "待重新裁决")
        self.assertEqual(rule["当前执行动作"], "")
        rows = self.client.get("/api/realtime").json()["items"]
        real3 = next(row for row in rows if row["规则编号"] == "REAL-0003")
        self.assertEqual(real3["status"], "待重新裁决")
        blocked = self.client.post(
            "/api/realtime/3/replay", json={"values": {"触发条件": "购物车停留超过15分钟"}}
        ).json()
        self.assertFalse(blocked["ok"])

    def test_export_routes_are_not_shadowed_by_id_route(self) -> None:
        for path in ("/api/tacticlib/export", "/api/realtime/export"):
            response = self.client.get(path)
            self.assertEqual(response.status_code, 200, path)

    def test_retirement_then_new_rule_keeps_same_outcome(self) -> None:
        """先解除退役动作或先写入新规则动作，最终执行动作与待裁决状态一致。"""
        self.action("tacticlib", 3, "退役策略")
        self.action("tacticlib", 1, "提交策略评审")
        self.action("tacticlib", 1, "发布策略")
        rule = self.client.get("/api/realtime/3").json()
        self.assertEqual(rule["status"], "待重新裁决")
        self.assertEqual(rule["当前执行动作"], "")
        # 关联规则改挂到新策略后，重新评审-生效才恢复命中，且动作来自新策略。
        entry = store.find("realtime", 3)
        entry["关联策略"] = "TACT-0001"
        entry["决策条件"] = "加购后30分钟未支付"
        self.action("realtime", 3, "提交规则评审")
        self.action("realtime", 3, "评审通过")
        self.assertTrue(self.action("realtime", 3, "生效规则")["ok"])
        rule = self.client.get("/api/realtime/3").json()
        self.assertEqual(rule["当前执行动作"], "推送优惠券")
        self.assertEqual(rule["动作来源"], "策略库:TACT-0001")

    def test_reconcile_batch_object(self) -> None:
        result = self.client.get("/api/tacticlib/3/reconcile").json()
        self.assertEqual(result["核对对象"], "TACT-0003")
        self.assertEqual(len(result["核对序列"]), 5)
        self.assertEqual(result["关联规则裁决"][0]["规则编号"], "REAL-0003")


if __name__ == "__main__":
    unittest.main()
