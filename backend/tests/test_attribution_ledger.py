"""逐笔订单归因明细与渠道复盘测试。

覆盖题面里的验收场景：
* 同一用户 09:00 / 12:00 两笔订单，11:45 的迟到触点只进入第二笔窗口；
* 每笔有效转化贡献合计为 1，重复提交订单只记一次；
* 渠道复盘只能用已确认的归因明细，且能回溯当时的窗口与触点；
* 更正过程中同批次同一订单不能被两个渠道按不同版本同时领取。
"""
from __future__ import annotations

import threading

import pytest

from app.attribution import AttributionLedger, ReviewConflict


@pytest.fixture
def ledger():
    instance = AttributionLedger()
    instance.submit_orders([
        {"order_id": "O-0900", "user_id": "u1", "order_time": "2026-11-24T09:00:00", "amount": 100},
        {"order_id": "O-1200", "user_id": "u1", "order_time": "2026-11-24T12:00:00", "amount": 200},
    ])
    instance.add_touchpoints([
        {"touchpoint_id": "TP-0830", "user_id": "u1", "channel": "自然搜索",
         "timestamp": "2026-11-24T08:30:00"},
        {"touchpoint_id": "TP-1030", "user_id": "u1", "channel": "信息流广告",
         "timestamp": "2026-11-24T10:30:00"},
    ])
    return instance


def _by_order(version):
    return {row["order_id"]: row for row in version["contributions"]}


class TestOrderLedger:
    def test_duplicate_order_counted_once(self, ledger):
        result = ledger.submit_order(
            {"order_id": "O-1200", "user_id": "u1", "order_time": "2026-11-24T12:00:00"}
        )
        assert result["duplicate"] is True
        assert result["total_conversions"] == 2
        assert len(ledger.list_orders()) == 2

    def test_each_conversion_weights_sum_to_one(self, ledger):
        version = ledger.recompute(window_minutes=120, algorithm="linear", confirm=True)
        assert version["conversion_count"] == 2
        for row in version["contributions"]:
            assert round(sum(row["channel_weights"].values()), 6) == 1.0

    def test_invalid_order_excluded(self, ledger):
        ledger.submit_order({
            "order_id": "O-CANCEL", "user_id": "u1",
            "order_time": "2026-11-24T13:00:00", "status": "已取消",
        })
        version = ledger.recompute(window_minutes=120, algorithm="linear", confirm=True)
        assert version["conversion_count"] == 2
        assert "O-CANCEL" not in _by_order(version)


class TestLateTouchpoint:
    def test_late_touchpoint_only_lands_in_second_window(self, ledger):
        ledger.recompute(window_minutes=120, algorithm="linear", confirm=True)
        ledger.add_touchpoint({
            "touchpoint_id": "TP-1145", "user_id": "u1", "channel": "邮件召回",
            "timestamp": "2026-11-24T11:45:00",
        })
        version = ledger.recompute(window_minutes=120, algorithm="linear", confirm=True)
        rows = _by_order(version)
        assert rows["O-0900"]["channel_weights"] == {"自然搜索": 1.0}
        assert rows["O-1200"]["channel_weights"] == {"信息流广告": 0.5, "邮件召回": 0.5}
        assert [tp["touchpoint_id"] for tp in rows["O-0900"]["touchpoints"]] == ["TP-0830"]
        assert [tp["touchpoint_id"] for tp in rows["O-1200"]["touchpoints"]] == ["TP-1030", "TP-1145"]
        assert version["conversion_count"] == 2

    def test_only_second_order_changes(self, ledger):
        first = ledger.recompute(window_minutes=120, algorithm="linear", confirm=True)
        ledger.add_touchpoint({
            "touchpoint_id": "TP-1145", "user_id": "u1", "channel": "邮件召回",
            "timestamp": "2026-11-24T11:45:00",
        })
        second = ledger.recompute(window_minutes=120, algorithm="linear", confirm=True)
        assert first["changed_order_ids"] == []
        assert second["changed_order_ids"] == ["O-1200"]

    def test_window_is_lookback_only(self, ledger):
        ledger.add_touchpoint({
            "touchpoint_id": "TP-1220", "user_id": "u1", "channel": "迟到触点",
            "timestamp": "2026-11-24T12:20:00",
        })
        version = ledger.recompute(window_minutes=20, algorithm="linear", confirm=True)
        rows = _by_order(version)
        # 20 分钟回看窗口内两笔订单都没有触点，回落到直接访问；
        # 12:20 的触点发生在 12:00 订单之后，不会前瞻进入任何窗口。
        assert rows["O-0900"]["channel_weights"] == {"direct": 1.0}
        assert rows["O-1200"]["channel_weights"] == {"direct": 1.0}


class TestVersionsAndReviews:
    def test_review_requires_confirmed_version(self, ledger):
        ledger.recompute(window_minutes=120, algorithm="linear")
        with pytest.raises(ValueError):
            ledger.create_review({"channel": "自然搜索"})

    def test_review_uses_confirmed_version_and_snapshots_window(self, ledger):
        ledger.recompute(window_minutes=120, algorithm="linear", confirm=True)
        review = ledger.create_review({"channel": "信息流广告"})
        assert review["version_verified"] is True
        assert review["conversion_order_ids"] == ["O-1200"]
        assert review["attribution_version"]["window_minutes"] == 120
        assert review["orders"][0]["touchpoints"][0]["touchpoint_id"] == "TP-1030"
        assert review["touchpoint_snapshot"]

    def test_review_filters_population_and_event_range(self, ledger):
        ledger.add_events([
            {"event_id": "E1", "user_id": "u1", "event_name": "访问", "timestamp": "2026-11-24T10:20:00"},
            {"event_id": "E2", "user_id": "u1", "event_name": "加购", "timestamp": "2026-11-24T11:30:00"},
            {"event_id": "E3", "user_id": "u1", "event_name": "下单", "timestamp": "2026-11-24T12:00:00"},
            {"event_id": "E4", "user_id": "u1", "event_name": "支付", "timestamp": "2026-11-24T12:05:00"},
        ])
        ledger.recompute(window_minutes=120, algorithm="linear", confirm=True)
        review = ledger.create_review({
            "channel": "信息流广告",
            "steps": ["访问", "加购", "下单", "支付"],
        })
        assert review["population"] == {"user_ids": ["u1"], "user_count": 1}
        assert review["conversion_event_range"] == {
            "start": "2026-11-24T10:00:00", "end": "2026-11-24T12:00:00",
        }
        assert review["step_counts"] == [1, 1, 1, 0]

    def test_same_batch_cannot_claim_two_versions_mid_correction(self, ledger):
        first = ledger.recompute(window_minutes=120, algorithm="linear", confirm=True)
        ledger.add_touchpoint({
            "touchpoint_id": "TP-1145", "user_id": "u1", "channel": "邮件召回",
            "timestamp": "2026-11-24T11:45:00",
        })
        second = ledger.recompute(window_minutes=120, algorithm="linear", confirm=True)
        assert second["version_id"] > first["version_id"]

        early = ledger.create_review({"channel": "信息流广告", "version_id": first["version_id"], "batch_id": "B-1"})
        late = ledger.create_review({"channel": "邮件召回", "version_id": second["version_id"], "batch_id": "B-1"})
        # v1 中 O-1200 已由信息流广告领取，v2 中邮件召回不能在同一批次重复领取该订单。
        assert early["conversion_order_ids"] == ["O-1200"]
        assert late["status"] == "部分领取"
        assert late["conversion_order_ids"] == []
        assert late["conflicts"][0]["order_id"] == "O-1200"
        assert late["conflicts"][0]["claimed_version_label"] == "v1"
        assert late["conflicts"][0]["requested_version_label"] == "v2"

    def test_strict_mode_raises_conflict(self, ledger):
        first = ledger.recompute(window_minutes=120, algorithm="linear", confirm=True)
        ledger.add_touchpoint({
            "touchpoint_id": "TP-1145", "user_id": "u1", "channel": "邮件召回",
            "timestamp": "2026-11-24T11:45:00",
        })
        second = ledger.recompute(window_minutes=120, algorithm="linear", confirm=True)
        ledger.create_review({"channel": "信息流广告", "version_id": first["version_id"], "batch_id": "B-2"})
        with pytest.raises(ReviewConflict):
            ledger.create_review({
                "channel": "邮件召回", "version_id": second["version_id"],
                "batch_id": "B-2", "strict": True,
            })

    def test_distinct_batches_may_use_different_versions(self, ledger):
        first = ledger.recompute(window_minutes=120, algorithm="linear", confirm=True)
        ledger.add_touchpoint({
            "touchpoint_id": "TP-1145", "user_id": "u1", "channel": "邮件召回",
            "timestamp": "2026-11-24T11:45:00",
        })
        second = ledger.recompute(window_minutes=120, algorithm="linear", confirm=True)
        a = ledger.create_review({"channel": "信息流广告", "version_id": first["version_id"], "batch_id": "B-A"})
        b = ledger.create_review({"channel": "邮件召回", "version_id": second["version_id"], "batch_id": "B-B"})
        assert a["status"] == "已完成"
        assert b["status"] == "已完成"
        assert a["conversion_order_ids"] == ["O-1200"]
        assert b["conversion_order_ids"] == ["O-1200"]
        # 事后仍可回溯各自领取的窗口与触点。
        assert a["orders"][0]["touchpoints"][0]["touchpoint_id"] == "TP-1030"
        assert [tp["touchpoint_id"] for tp in b["orders"][0]["touchpoints"]] == ["TP-1030", "TP-1145"]


class TestConcurrency:
    def test_recompute_is_atomic_with_review(self, ledger):
        """重算与复盘并发时，复盘只能拿到完整的已确认版本。"""
        ledger.recompute(window_minutes=120, algorithm="linear", confirm=True)
        ledger.add_touchpoint({
            "touchpoint_id": "TP-1145", "user_id": "u1", "channel": "邮件召回",
            "timestamp": "2026-11-24T11:45:00",
        })
        errors: list[Exception] = []
        barrier = threading.Barrier(3)

        def recompute():
            try:
                barrier.wait()
                ledger.recompute(window_minutes=120, algorithm="linear", confirm=True)
            except Exception as error:  # pragma: no cover - 失败时由断言暴露
                errors.append(error)

        def review():
            try:
                barrier.wait()
                result = ledger.create_review({"channel": "信息流广告"})
                weights = result["orders"][0]["channel_weights"] if result["orders"] else {}
                # 无论领取更正前还是更正后的版本，权重都必须是完整的一份。
                assert round(sum(weights.values()), 6) in (1.0,)
            except Exception as error:  # pragma: no cover
                errors.append(error)

        threads = [threading.Thread(target=recompute), threading.Thread(target=recompute),
                   threading.Thread(target=review)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        assert errors == []
        versions = ledger.list_versions()
        assert sum(1 for version in versions if version["active"]) == 1


class TestModelLevelDetail:
    def test_attribute_orders_splits_windows_per_order(self):
        from app.models import AttributionAlgorithm, AttributionModel

        model = AttributionModel(
            model_id="ATTR-ORDER",
            model_name="按单归因",
            algorithm=AttributionAlgorithm.LINEAR,
            attribution_window_days=1,
        )
        touchpoints = [
            {"touchpoint_id": "TP-0830", "user_id": "u1", "channel": "自然搜索",
             "timestamp": "2026-11-24T08:30:00"},
            {"touchpoint_id": "TP-1030", "user_id": "u1", "channel": "信息流广告",
             "timestamp": "2026-11-24T10:30:00"},
            {"touchpoint_id": "TP-1145", "user_id": "u1", "channel": "邮件召回",
             "timestamp": "2026-11-24T11:45:00"},
        ]
        orders = [
            {"order_id": "O-0900", "user_id": "u1", "order_time": "2026-11-24T09:00:00"},
            {"order_id": "O-1200", "user_id": "u1", "order_time": "2026-11-24T12:00:00"},
            {"order_id": "O-1200", "user_id": "u1", "order_time": "2026-11-24T12:00:00"},
        ]
        contributions = model.attribute_orders(touchpoints, orders)
        assert [row["order_id"] for row in contributions] == ["O-0900", "O-1200"]
        # 09:00 订单的窗口在 09:00 截止，10:30 与 11:45 的触点都不进来。
        assert [tp["touchpoint_id"] for tp in contributions[0]["touchpoints"]] == ["TP-0830"]
        assert contributions[0]["channel_weights"] == {"自然搜索": 1.0}
        assert [tp["touchpoint_id"] for tp in contributions[1]["touchpoints"]] == [
            "TP-0830", "TP-1030", "TP-1145",
        ]
        for row in contributions:
            assert round(sum(row["channel_weights"].values()), 6) == 1.0

    def test_pipeline_exposes_per_order_contributions(self):
        from app.analysis_pipeline import pipeline_attribution_to_funnel
        from app.models import AttributionAlgorithm, AttributionModel

        model = AttributionModel(
            model_id="ATTR-PIPE", model_name="管道归因",
            algorithm=AttributionAlgorithm.LAST_TOUCH, attribution_window_days=1,
        )
        result = pipeline_attribution_to_funnel(
            [model],
            [
                {"user_id": "u1", "channel": "自然搜索", "timestamp": "2026-11-24T08:30:00"},
                {"user_id": "u1", "channel": "邮件召回", "timestamp": "2026-11-24T11:45:00"},
            ],
            [
                {"order_id": "O-0900", "user_id": "u1", "order_time": "2026-11-24T09:00:00"},
                {"order_id": "O-1200", "user_id": "u1", "order_time": "2026-11-24T12:00:00"},
            ],
            {},
        )
        attribution = result["attributions"][0]
        assert attribution["conversion_count"] == 2
        assert [row["order_id"] for row in attribution["contributions"]] == ["O-0900", "O-1200"]
        assert attribution["contributions"][0]["channel_weights"] == {"自然搜索": 1.0}
        assert attribution["contributions"][1]["channel_weights"] == {"邮件召回": 1.0}

    def test_pipeline_from_confirmed_version(self):
        from app.analysis_pipeline import pipeline_confirmed_attribution_to_channel_review

        ledger = AttributionLedger()
        ledger.submit_orders([
            {"order_id": "O-0900", "user_id": "u1", "order_time": "2026-11-24T09:00:00"},
            {"order_id": "O-1200", "user_id": "u1", "order_time": "2026-11-24T12:00:00"},
        ])
        ledger.add_touchpoints([
            {"touchpoint_id": "TP-1030", "user_id": "u1", "channel": "信息流广告",
             "timestamp": "2026-11-24T10:30:00"},
            {"touchpoint_id": "TP-1145", "user_id": "u1", "channel": "邮件召回",
             "timestamp": "2026-11-24T11:45:00"},
        ])
        version = ledger.recompute(window_minutes=120, algorithm="linear", confirm=True)
        events = [
            {"user_id": "u1", "event_name": "访问", "timestamp": "2026-11-24T10:20:00"},
            {"user_id": "u1", "event_name": "加购", "timestamp": "2026-11-24T11:30:00"},
            {"user_id": "u1", "event_name": "下单", "timestamp": "2026-11-24T12:00:00"},
        ]
        result = pipeline_confirmed_attribution_to_channel_review(
            version, "邮件召回", events, ["访问", "加购", "下单", "支付"],
        )
        assert result["conversion_order_ids"] == ["O-1200"]
        assert result["attribution_version"] == version["version_label"]
        assert result["step_counts"] == [1, 1, 1, 0]
        assert result["conversion_event_range"] == {
            "start": "2026-11-24T10:00:00", "end": "2026-11-24T12:00:00",
        }

    def test_pipeline_rejects_draft_version(self):
        from app.analysis_pipeline import pipeline_confirmed_attribution_to_channel_review

        ledger = AttributionLedger()
        ledger.submit_orders([
            {"order_id": "O-1", "user_id": "u1", "order_time": "2026-11-24T09:00:00"},
        ])
        version = ledger.recompute(window_minutes=120)
        with pytest.raises(ValueError):
            pipeline_confirmed_attribution_to_channel_review(version, "direct", [])


class TestFunctionalHelpers:
    def test_build_order_contributions_is_pure(self):
        from app.attribution import build_order_contributions, ledger

        ledger.reset()
        contributions = build_order_contributions(
            [
                {"order_id": "P-1", "user_id": "u9", "order_time": "2026-11-24T09:00:00"},
                {"order_id": "P-2", "user_id": "u9", "order_time": "2026-11-24T12:00:00"},
                {"order_id": "P-2", "user_id": "u9", "order_time": "2026-11-24T12:00:00"},
            ],
            [
                {"touchpoint_id": "T-1030", "user_id": "u9", "channel": "feed",
                 "timestamp": "2026-11-24T10:30:00"},
                {"touchpoint_id": "T-1145", "user_id": "u9", "channel": "email",
                 "timestamp": "2026-11-24T11:45:00"},
            ],
            window_minutes=120,
        )
        assert [row["order_id"] for row in contributions] == ["P-1", "P-2"]
        assert contributions[0]["channel_weights"] == {"direct": 1.0}
        assert contributions[1]["channel_weights"] == {"feed": 0.5, "email": 0.5}
        # 纯函数不污染全局账本
        assert ledger.state()["orders"] == 0

    def test_review_channel_funnel_requires_confirmed(self):
        from app.attribution import review_channel_funnel

        with pytest.raises(ValueError):
            review_channel_funnel({"status": "draft", "contributions": []}, "direct", [])

    def test_review_channel_funnel_counts_steps(self):
        from app.attribution import review_channel_funnel

        version = {
            "status": "confirmed",
            "version_label": "v9",
            "window_minutes": 120,
            "contributions": [
                {"order_id": "O1", "user_id": "u1", "order_time": "2026-11-24T12:00:00",
                 "window": {"start": "2026-11-24T10:00:00", "end": "2026-11-24T12:00:00"},
                 "channel_weights": {"feed": 1.0}, "touchpoints": []},
                {"order_id": "O2", "user_id": "u2", "order_time": "2026-11-24T12:00:00",
                 "window": {"start": "2026-11-24T10:00:00", "end": "2026-11-24T12:00:00"},
                 "channel_weights": {"email": 1.0}, "touchpoints": []},
            ],
        }
        events = [
            {"user_id": "u1", "event_name": "访问", "timestamp": "2026-11-24T10:10:00"},
            {"user_id": "u1", "event_name": "下单", "timestamp": "2026-11-24T11:50:00"},
            {"user_id": "u2", "event_name": "访问", "timestamp": "2026-11-24T10:15:00"},
        ]
        result = review_channel_funnel(version, "feed", events, ["访问", "下单"])
        assert result["attribution_version"] == "v9"
        assert result["population"] == ["u1"]
        assert result["step_counts"] == [1, 1]
        assert result["conversion_order_ids"] == ["O1"]
