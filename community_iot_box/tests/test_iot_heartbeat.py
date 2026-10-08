from datetime import timedelta
from unittest.mock import patch

from odoo import fields
from odoo.tests.common import TransactionCase, tagged


@tagged("post_install", "-at_install")
class TestCommunityIotHeartbeat(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.Box = cls.env["community_iot_box.iot_box"]
        cls.now = fields.Datetime.now()
        cls.env["ir.config_parameter"].sudo().set_param(
            "community_iot_box.heartbeat_timeout_seconds", "180"
        )

    def test_cron_expires_stale_and_missing_heartbeats_without_jobs(self):
        stale, missing, boundary, missed, draft, error, archived = self.Box.create([
            {"name": "Stale", "state": "online", "last_seen": self.now - timedelta(hours=3)},
            {"name": "Missing", "state": "online"},
            {"name": "Boundary", "state": "online", "last_seen": self.now - timedelta(seconds=180)},
            {"name": "Missed heartbeat", "state": "online", "last_seen": self.now - timedelta(seconds=90)},
            {"name": "Draft"},
            {"name": "Reported error", "state": "error", "last_seen": self.now - timedelta(hours=3)},
            {"name": "Archived", "state": "online", "active": False},
        ])
        with patch.object(fields.Datetime, "now", return_value=self.now):
            self.Box._cron_expire_stale_heartbeats()
            self.assertEqual(stale.state, "offline")
            self.assertEqual(missing.state, "offline")
            self.assertEqual(boundary.state, "online")
            self.assertEqual(missed.state, "online")
            self.assertEqual(draft.state, "draft")
            self.assertEqual(error.state, "error")
            self.assertEqual(archived.state, "online")
            self.assertFalse(stale.job_ids)
            self.assertEqual(self.Box._cron_expire_stale_heartbeats(), 0)

    def test_configured_timeout_and_invalid_values(self):
        box = self.Box.create({
            "name": "Configured timeout", "state": "online",
            "last_seen": self.now - timedelta(seconds=240),
        })
        params = self.env["ir.config_parameter"].sudo()
        with patch.object(fields.Datetime, "now", return_value=self.now):
            params.set_param("community_iot_box.heartbeat_timeout_seconds", "300")
            self.Box._cron_expire_stale_heartbeats()
            self.assertEqual(box.state, "online")
            params.set_param("community_iot_box.heartbeat_timeout_seconds", "200")
            self.Box._cron_expire_stale_heartbeats()
            self.assertEqual(box.state, "offline")
        for value in (False, "invalid", "0", "-60"):
            with self.subTest(value=value):
                params.set_param("community_iot_box.heartbeat_timeout_seconds", value)
                self.assertEqual(self.Box._heartbeat_timeout_seconds(), 180)

    def test_dashboard_expiration_is_company_scoped_and_cron_covers_all_companies(self):
        other_company = self.env["res.company"].create({"name": "Heartbeat other company"})
        other_box = self.Box.create({
            "name": "Other company stale", "company_id": other_company.id,
            "state": "online", "last_seen": self.now - timedelta(hours=3),
        })
        with patch.object(fields.Datetime, "now", return_value=self.now):
            data = self.Box.with_context(allowed_company_ids=[self.env.company.id]).get_dashboard_data()
            self.assertEqual(other_box.state, "online")
            self.assertNotIn(other_box.id, [box["id"] for box in data["boxes"]])
            self.Box._cron_expire_stale_heartbeats()
            self.assertEqual(other_box.state, "offline")

    def test_expiration_does_not_overwrite_heartbeat_renewed_after_search(self):
        box = self.Box.create({
            "name": "Renewed heartbeat", "state": "online",
            "last_seen": self.now - timedelta(hours=3),
        })
        original_search = type(self.Box).search

        def search_then_renew(recordset, *args, **kwargs):
            result = original_search(recordset, *args, **kwargs)
            box.last_seen = self.now
            box.flush_recordset(["last_seen"])
            return result

        with patch.object(fields.Datetime, "now", return_value=self.now), patch.object(
            type(self.Box), "search", search_then_renew,
        ):
            self.Box._expire_stale_heartbeats([("id", "=", box.id)])
        self.assertEqual(box.state, "online")
