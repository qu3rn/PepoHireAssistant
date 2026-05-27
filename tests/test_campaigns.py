"""Tests for campaign service module."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from cv_sender.campaigns import (
    REACT_SPRINT_PRESET,
    attach_queue_items_to_campaign,
    build_campaign_queue,
    complete_campaign_if_target_reached,
    create_campaign,
    generate_campaign_summary,
    get_active_campaigns,
    get_campaign,
    get_campaign_progress,
    mark_campaign_sent,
    record_campaign_activity,
    update_campaign_status,
)
from cv_sender.models import (
    ApplyQueueItem,
    ApplyQueueItemStatus,
    Campaign,
    CampaignActivity,
    CampaignActivityType,
    CampaignGoalType,
    CampaignStatus,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_queue_item(
    item_id: str = "q1",
    offer_id: str = "o1",
    score: int = 70,
    source: str = "justjoin",
    status: ApplyQueueItemStatus = ApplyQueueItemStatus.QUEUED,
    campaign_id: str = "",
) -> ApplyQueueItem:
    return ApplyQueueItem(
        id=item_id,
        offer_id=offer_id,
        title="React Developer",
        company="ACME",
        source=source,
        url=f"https://example.com/{offer_id}",
        score=score,
        priority_score=float(score),
        status=status,
        campaign_id=campaign_id,
    )


def _make_campaign(
    campaign_id: str = "c1",
    name: str = "Test Sprint",
    target_count: int = 10,
    status: CampaignStatus = CampaignStatus.ACTIVE,
    sent_count: int = 0,
) -> Campaign:
    return Campaign(
        id=campaign_id,
        name=name,
        target_count=target_count,
        status=status,
        sent_count=sent_count,
    )


# ---------------------------------------------------------------------------
# create_campaign
# ---------------------------------------------------------------------------


class TestCreateCampaign:
    def test_creates_and_persists_campaign(self, tmp_path):
        with (
            patch("cv_sender.campaigns.add_campaign") as mock_add,
        ):
            campaign = create_campaign(
                "My Sprint",
                target_count=20,
                keywords=["React"],
            )

        assert campaign.name == "My Sprint"
        assert campaign.target_count == 20
        assert campaign.keywords == ["React"]
        assert campaign.status == CampaignStatus.ACTIVE
        mock_add.assert_called_once_with(campaign)

    def test_default_target_date_is_today(self):
        from datetime import date  # noqa: PLC0415

        with patch("cv_sender.campaigns.add_campaign"):
            campaign = create_campaign("Sprint")

        assert campaign.target_date == date.today().isoformat()

    def test_goal_type_default(self):
        with patch("cv_sender.campaigns.add_campaign"):
            campaign = create_campaign("Sprint")

        assert campaign.goal_type == CampaignGoalType.APPLICATIONS_SENT

    def test_started_at_set_on_creation(self):
        with patch("cv_sender.campaigns.add_campaign"):
            campaign = create_campaign("Sprint")

        assert campaign.started_at is not None

    def test_include_maybe_default_true(self):
        with patch("cv_sender.campaigns.add_campaign"):
            campaign = create_campaign("Sprint")

        assert campaign.include_maybe is True


# ---------------------------------------------------------------------------
# update_campaign_status
# ---------------------------------------------------------------------------


class TestUpdateCampaignStatus:
    def test_changes_status(self):
        campaign = _make_campaign()
        with (
            patch("cv_sender.campaigns.get_campaign_by_id", return_value=campaign),
            patch("cv_sender.campaigns.update_campaign") as mock_update,
        ):
            result = update_campaign_status("c1", CampaignStatus.PAUSED)

        assert result is not None
        assert result.status == CampaignStatus.PAUSED
        mock_update.assert_called_once()

    def test_sets_completed_at_on_complete(self):
        campaign = _make_campaign()
        with (
            patch("cv_sender.campaigns.get_campaign_by_id", return_value=campaign),
            patch("cv_sender.campaigns.update_campaign") as mock_update,
        ):
            result = update_campaign_status("c1", CampaignStatus.COMPLETED)

        assert result.completed_at is not None

    def test_returns_none_for_missing_campaign(self):
        with patch("cv_sender.campaigns.get_campaign_by_id", return_value=None):
            result = update_campaign_status("nonexistent", CampaignStatus.PAUSED)
        assert result is None


# ---------------------------------------------------------------------------
# record_campaign_activity
# ---------------------------------------------------------------------------


class TestRecordCampaignActivity:
    def test_persists_activity(self):
        campaign = _make_campaign()
        with (
            patch("cv_sender.campaigns.add_campaign_activity") as mock_add,
            patch("cv_sender.campaigns.get_campaign_by_id", return_value=campaign),
            patch("cv_sender.campaigns.update_campaign"),
        ):
            activity = record_campaign_activity(
                "c1",
                CampaignActivityType.SENT,
                offer_id="o1",
            )

        mock_add.assert_called_once_with(activity)
        assert activity.type == CampaignActivityType.SENT

    def test_sent_increments_sent_count(self):
        campaign = _make_campaign(sent_count=3)
        with (
            patch("cv_sender.campaigns.add_campaign_activity"),
            patch("cv_sender.campaigns.get_campaign_by_id", return_value=campaign),
            patch("cv_sender.campaigns.update_campaign") as mock_update,
        ):
            record_campaign_activity("c1", CampaignActivityType.SENT)

        saved = mock_update.call_args[0][0]
        assert saved.sent_count == 4

    def test_skipped_increments_skipped_count(self):
        campaign = _make_campaign()
        with (
            patch("cv_sender.campaigns.add_campaign_activity"),
            patch("cv_sender.campaigns.get_campaign_by_id", return_value=campaign),
            patch("cv_sender.campaigns.update_campaign") as mock_update,
        ):
            record_campaign_activity("c1", CampaignActivityType.SKIPPED)

        saved = mock_update.call_args[0][0]
        assert saved.skipped_count == 1

    def test_failed_increments_failed_count(self):
        campaign = _make_campaign()
        with (
            patch("cv_sender.campaigns.add_campaign_activity"),
            patch("cv_sender.campaigns.get_campaign_by_id", return_value=campaign),
            patch("cv_sender.campaigns.update_campaign") as mock_update,
        ):
            record_campaign_activity("c1", CampaignActivityType.FAILED)

        saved = mock_update.call_args[0][0]
        assert saved.failed_count == 1

    def test_queued_does_not_update_counter(self):
        campaign = _make_campaign()
        with (
            patch("cv_sender.campaigns.add_campaign_activity"),
            patch("cv_sender.campaigns.get_campaign_by_id", return_value=campaign),
            patch("cv_sender.campaigns.update_campaign") as mock_update,
        ):
            record_campaign_activity("c1", CampaignActivityType.QUEUED)

        # QUEUED is not in counter_map → update_campaign not called with counter change
        # (it IS called but without a counter increment)
        # We verify sent_count is still 0
        if mock_update.called:
            saved = mock_update.call_args[0][0]
            assert saved.sent_count == 0


# ---------------------------------------------------------------------------
# get_campaign_progress
# ---------------------------------------------------------------------------


class TestGetCampaignProgress:
    def test_basic_progress(self):
        campaign = _make_campaign(target_count=10)
        activities = [
            CampaignActivity(campaign_id="c1", type=CampaignActivityType.SENT),
            CampaignActivity(campaign_id="c1", type=CampaignActivityType.SENT),
            CampaignActivity(campaign_id="c1", type=CampaignActivityType.SKIPPED),
            CampaignActivity(campaign_id="c1", type=CampaignActivityType.FAILED),
        ]
        queue_items = [
            _make_queue_item("q1", campaign_id="c1"),  # QUEUED
            _make_queue_item("q2", "o2", campaign_id="c1"),  # QUEUED
        ]

        with (
            patch("cv_sender.campaigns.get_campaign_by_id", return_value=campaign),
            patch("cv_sender.campaigns.get_campaign_activities", return_value=activities),
            patch("cv_sender.campaigns.load_apply_queue", return_value=queue_items),
        ):
            progress = get_campaign_progress("c1")

        assert progress is not None
        assert progress.sent == 2
        assert progress.remaining == 8
        assert progress.skipped == 1
        assert progress.failed == 1
        assert progress.queued_available == 2
        assert progress.progress_pct == 20.0

    def test_remaining_never_negative(self):
        campaign = _make_campaign(target_count=5)
        activities = [
            CampaignActivity(campaign_id="c1", type=CampaignActivityType.SENT)
            for _ in range(7)
        ]
        with (
            patch("cv_sender.campaigns.get_campaign_by_id", return_value=campaign),
            patch("cv_sender.campaigns.get_campaign_activities", return_value=activities),
            patch("cv_sender.campaigns.load_apply_queue", return_value=[]),
        ):
            progress = get_campaign_progress("c1")

        assert progress.remaining == 0

    def test_progress_100_when_target_reached(self):
        campaign = _make_campaign(target_count=5)
        activities = [
            CampaignActivity(campaign_id="c1", type=CampaignActivityType.SENT)
            for _ in range(5)
        ]
        with (
            patch("cv_sender.campaigns.get_campaign_by_id", return_value=campaign),
            patch("cv_sender.campaigns.get_campaign_activities", return_value=activities),
            patch("cv_sender.campaigns.load_apply_queue", return_value=[]),
        ):
            progress = get_campaign_progress("c1")

        assert progress.progress_pct == 100.0

    def test_queue_shortage_warning(self):
        campaign = _make_campaign(target_count=10)
        activities = []  # 0 sent, 10 remaining
        queue_items = [_make_queue_item("q1", campaign_id="c1")]  # only 1 available

        with (
            patch("cv_sender.campaigns.get_campaign_by_id", return_value=campaign),
            patch("cv_sender.campaigns.get_campaign_activities", return_value=activities),
            patch("cv_sender.campaigns.load_apply_queue", return_value=queue_items),
        ):
            progress = get_campaign_progress("c1")

        assert progress.queue_shortage is True
        assert progress.queue_shortage_message != ""

    def test_no_shortage_when_queue_sufficient(self):
        campaign = _make_campaign(target_count=2)
        activities = []
        queue_items = [
            _make_queue_item("q1", campaign_id="c1"),
            _make_queue_item("q2", "o2", campaign_id="c1"),
            _make_queue_item("q3", "o3", campaign_id="c1"),
        ]

        with (
            patch("cv_sender.campaigns.get_campaign_by_id", return_value=campaign),
            patch("cv_sender.campaigns.get_campaign_activities", return_value=activities),
            patch("cv_sender.campaigns.load_apply_queue", return_value=queue_items),
        ):
            progress = get_campaign_progress("c1")

        assert progress.queue_shortage is False

    def test_returns_none_for_missing_campaign(self):
        with patch("cv_sender.campaigns.get_campaign_by_id", return_value=None):
            result = get_campaign_progress("nonexistent")
        assert result is None


# ---------------------------------------------------------------------------
# attach_queue_items_to_campaign
# ---------------------------------------------------------------------------


class TestAttachQueueItems:
    def test_attaches_items(self):
        items = [
            _make_queue_item("q1"),
            _make_queue_item("q2", "o2"),
        ]
        with (
            patch("cv_sender.storage.load_apply_queue", return_value=items),
            patch("cv_sender.storage.save_apply_queue") as mock_save,
        ):
            count = attach_queue_items_to_campaign("c1", ["q1", "q2"])

        assert count == 2
        saved = mock_save.call_args[0][0]
        for item in saved:
            assert item.campaign_id == "c1"

    def test_skips_already_attached(self):
        items = [
            _make_queue_item("q1", campaign_id="c1"),  # already attached to c1
            _make_queue_item("q2", "o2"),
        ]
        with (
            patch("cv_sender.storage.load_apply_queue", return_value=items),
            patch("cv_sender.storage.save_apply_queue"),
        ):
            count = attach_queue_items_to_campaign("c1", ["q1", "q2"])

        assert count == 1  # q1 was already attached

    def test_empty_id_list(self):
        items = [_make_queue_item("q1")]
        with (
            patch("cv_sender.storage.load_apply_queue", return_value=items),
            patch("cv_sender.storage.save_apply_queue") as mock_save,
        ):
            count = attach_queue_items_to_campaign("c1", [])

        assert count == 0
        saved = mock_save.call_args[0][0]
        assert saved[0].campaign_id == ""


# ---------------------------------------------------------------------------
# complete_campaign_if_target_reached
# ---------------------------------------------------------------------------


class TestCompleteCampaignIfTargetReached:
    def test_completes_when_target_reached(self):
        campaign = _make_campaign(target_count=5, sent_count=5)
        with (
            patch("cv_sender.campaigns.get_campaign_by_id", return_value=campaign),
            patch("cv_sender.campaigns.update_campaign_status") as mock_status,
        ):
            result = complete_campaign_if_target_reached("c1")

        assert result is True
        mock_status.assert_called_once_with("c1", CampaignStatus.COMPLETED)

    def test_does_not_complete_when_short(self):
        campaign = _make_campaign(target_count=10, sent_count=3)
        with (
            patch("cv_sender.campaigns.get_campaign_by_id", return_value=campaign),
            patch("cv_sender.campaigns.update_campaign_status") as mock_status,
        ):
            result = complete_campaign_if_target_reached("c1")

        assert result is False
        mock_status.assert_not_called()

    def test_does_not_complete_already_completed(self):
        campaign = _make_campaign(target_count=5, sent_count=5, status=CampaignStatus.COMPLETED)
        with (
            patch("cv_sender.campaigns.get_campaign_by_id", return_value=campaign),
            patch("cv_sender.campaigns.update_campaign_status") as mock_status,
        ):
            result = complete_campaign_if_target_reached("c1")

        assert result is False
        mock_status.assert_not_called()


# ---------------------------------------------------------------------------
# mark_campaign_sent
# ---------------------------------------------------------------------------


class TestMarkCampaignSent:
    def test_records_sent_activity(self):
        campaign = _make_campaign()
        with (
            patch("cv_sender.campaigns.record_campaign_activity") as mock_record,
            patch("cv_sender.campaigns.complete_campaign_if_target_reached"),
        ):
            mark_campaign_sent("c1", "app1", "offer1")

        mock_record.assert_called_once_with(
            "c1",
            CampaignActivityType.SENT,
            offer_id="offer1",
            application_id="app1",
            queue_item_id="",
        )

    def test_triggers_completion_check(self):
        with (
            patch("cv_sender.campaigns.record_campaign_activity"),
            patch("cv_sender.campaigns.complete_campaign_if_target_reached") as mock_complete,
        ):
            mark_campaign_sent("c1", "app1", "offer1")

        mock_complete.assert_called_once_with("c1")


# ---------------------------------------------------------------------------
# React Sprint preset
# ---------------------------------------------------------------------------


class TestReactSprintPreset:
    def test_preset_has_required_fields(self):
        assert REACT_SPRINT_PRESET["name"] == "React Frontend Sprint"
        assert REACT_SPRINT_PRESET["target_count"] == 25
        assert "React Developer" in REACT_SPRINT_PRESET["keywords"]
        assert "React" in REACT_SPRINT_PRESET["technologies"]

    def test_preset_sources_are_valid(self):
        valid = {"justjoin", "rocketjobs", "nofluffjobs", "pracuj", "linkedin"}
        for src in REACT_SPRINT_PRESET["sources"]:
            assert src in valid

    def test_preset_min_score(self):
        assert REACT_SPRINT_PRESET["min_score"] == 60

    def test_preset_include_maybe(self):
        assert REACT_SPRINT_PRESET["include_maybe"] is True

    def test_create_campaign_from_preset(self):
        preset = dict(REACT_SPRINT_PRESET)
        with patch("cv_sender.campaigns.add_campaign") as mock_add:
            campaign = create_campaign(
                preset.pop("name"),
                **{k: v for k, v in preset.items() if k != "goal_type"},
            )

        assert campaign.name == "React Frontend Sprint"
        assert campaign.target_count == 25
        assert campaign.min_score == 60


# ---------------------------------------------------------------------------
# build_campaign_queue
# ---------------------------------------------------------------------------


class TestBuildCampaignQueue:
    def test_attaches_unassigned_items_matching_source(self):
        campaign = _make_campaign()
        campaign = campaign.model_copy(update={"sources": ["justjoin"]})
        items = [
            _make_queue_item("q1", source="justjoin"),   # matches
            _make_queue_item("q2", "o2", source="pracuj"),  # does not match
        ]
        with (
            patch("cv_sender.campaigns.get_campaign_by_id", return_value=campaign),
            patch("cv_sender.storage.load_apply_queue", return_value=items),
            patch("cv_sender.storage.save_apply_queue"),
            patch("cv_sender.campaigns.attach_queue_items_to_campaign") as mock_attach,
        ):
            build_campaign_queue("c1")

        attached_ids = mock_attach.call_args[0][1]
        assert "q1" in attached_ids
        assert "q2" not in attached_ids

    def test_skips_already_claimed_items(self):
        campaign = _make_campaign()
        items = [
            _make_queue_item("q1"),                          # free
            _make_queue_item("q2", "o2", campaign_id="other"),  # claimed
        ]
        with (
            patch("cv_sender.campaigns.get_campaign_by_id", return_value=campaign),
            patch("cv_sender.storage.load_apply_queue", return_value=items),
            patch("cv_sender.storage.save_apply_queue"),
            patch("cv_sender.campaigns.attach_queue_items_to_campaign") as mock_attach,
        ):
            build_campaign_queue("c1")

        attached_ids = mock_attach.call_args[0][1]
        assert "q1" in attached_ids
        assert "q2" not in attached_ids

    def test_respects_min_score(self):
        campaign = _make_campaign()
        items = [
            _make_queue_item("hi", score=80),
            _make_queue_item("lo", "o2", score=30),
        ]
        with (
            patch("cv_sender.campaigns.get_campaign_by_id", return_value=campaign),
            patch("cv_sender.storage.load_apply_queue", return_value=items),
            patch("cv_sender.storage.save_apply_queue"),
            patch("cv_sender.campaigns.attach_queue_items_to_campaign") as mock_attach,
        ):
            build_campaign_queue("c1", min_score=60)

        attached_ids = mock_attach.call_args[0][1]
        assert "hi" in attached_ids
        assert "lo" not in attached_ids
