from datetime import timedelta
from unittest.mock import patch

from odoo import fields
from odoo.tests.common import TransactionCase, tagged


@tagged("post_install", "-at_install")
class TestCommunityIotDashboard(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.Box = cls.env["community_iot_box.iot_box"]
        cls.Device = cls.env["community_iot_box.iot_device"]
        cls.Job = cls.env["community_iot_box.iot_job"]
        cls.env["ir.config_parameter"].sudo().set_param(
            "community_iot_box.heartbeat_timeout_seconds", "180"
        )

        cls.online_box = cls.Box.create(
            {
                "name": "Dashboard Online",
                "state": "online",
                "last_seen": fields.Datetime.now(),
                "agent_version": "1.0.0",
                "ip_address": "192.0.2.10",
            }
        )
        cls.offline_box = cls.Box.create(
            {
                "name": "Dashboard Offline",
                "state": "offline",
            }
        )
        cls.device = cls.Device.create(
            {
                "name": "Dashboard Printer",
                "box_id": cls.online_box.id,
                "device_key": "dashboard_printer",
                "type": "standard_printer",
                "backend": "cups",
                "interface": "cups",
            }
        )
        cls.pending_job = cls.Job.create(
            {
                "name": "Dashboard Pending",
                "box_id": cls.online_box.id,
                "device_id": cls.device.id,
                "job_type": "test_ticket",
                "payload": "{}",
            }
        )
        cls.error_job = cls.Job.create(
            {
                "name": "Dashboard Error",
                "box_id": cls.online_box.id,
                "device_id": cls.device.id,
                "job_type": "test_ticket",
                "state": "error",
                "result_status": "error",
                "payload": "{}",
            }
        )

    def test_dashboard_returns_real_metrics_without_tokens(self):
        data = self.Box.get_dashboard_data()

        self.assertGreaterEqual(data["metrics"]["boxes_total"], 2)
        self.assertGreaterEqual(data["metrics"]["boxes_online"], 1)
        self.assertGreaterEqual(data["metrics"]["devices_total"], 1)
        self.assertGreaterEqual(data["metrics"]["jobs_pending"], 1)
        self.assertGreaterEqual(data["metrics"]["attention"], 2)

        online_card = next(
            item for item in data["boxes"] if item["id"] == self.online_box.id
        )
        self.assertTrue(online_card["token_configured"])
        self.assertNotIn("token", online_card)
        self.assertNotIn(self.online_box.token, repr(data))

    def test_dashboard_recent_jobs_and_alerts_are_serializable(self):
        data = self.Box.get_dashboard_data()

        job_ids = {item["id"] for item in data["jobs"]}
        self.assertIn(self.pending_job.id, job_ids)
        self.assertIn(self.error_job.id, job_ids)
        self.assertTrue(data["alerts"])

        for alert in data["alerts"]:
            self.assertIsInstance(alert["key"], str)
            self.assertIn(alert["level"], {"warning", "danger"})

    def test_stale_heartbeat_updates_metrics_cards_alerts_and_connection_test(self):
        now = fields.Datetime.now()
        self.online_box.last_seen = now - timedelta(hours=3)
        with patch.object(fields.Datetime, "now", return_value=now):
            data = self.Box.get_dashboard_data()
            self.assertEqual(self.online_box.state, "offline")
            self.assertEqual(self.online_box.read(["state"])[0]["state"], "offline")
            card = next(item for item in data["boxes"] if item["id"] == self.online_box.id)
            self.assertEqual(card["state"], "offline")
            self.assertEqual(data["metrics"]["boxes_online"], self.Box.search_count([
                ("company_id", "in", self.env.companies.ids), ("state", "=", "online"),
            ]))
            self.assertGreaterEqual(data["metrics"]["attention"], 3)
            alert = next(item for item in data["alerts"] if item["key"] == f"box-{self.online_box.id}")
            self.assertEqual(alert["level"], "warning")
            self.assertEqual(self.online_box.action_test_connection()["params"]["type"], "warning")

    def test_connection_test_uses_grace_period_before_cron_runs(self):
        now = fields.Datetime.now()
        with patch.object(fields.Datetime, "now", return_value=now):
            self.online_box.last_seen = now - timedelta(seconds=120)
            self.assertEqual(self.online_box.action_test_connection()["params"]["type"], "success")
            self.online_box.last_seen = now - timedelta(seconds=181)
            self.assertEqual(self.online_box.action_test_connection()["params"]["type"], "warning")
            self.assertEqual(self.online_box.state, "offline")
