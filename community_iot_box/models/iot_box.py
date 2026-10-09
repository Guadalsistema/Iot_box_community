import json
import secrets
from datetime import timedelta

from odoo import api, fields, models


class CommunityIotBox(models.Model):
    _name = "community_iot_box.iot_box"
    _description = "Community IoT Box"

    name = fields.Char(required=True)
    company_id = fields.Many2one(
        "res.company",
        required=True,
        default=lambda self: self.env.company,
    )
    active = fields.Boolean(default=True)
    token = fields.Char(
        string="IoT Token",
        groups="base.group_system",
        help="Authentication token used by the IoT Box to communicate with Odoo.",
    )

    box_uid = fields.Char(
        string="Agent UID",
        help="Unique identifier reported by the IoT Box (by the Python agent).",
    )
    hostname = fields.Char(
        string="Hostname",
        help="Hostname reported by the IoT Box.",
    )
    ip_address = fields.Char(
        string="Last IP",
        help="Last known IP address of the IoT Box.",
    )
    mac_address = fields.Char(
        string="MAC Address",
        help="MAC address reported by the IoT Box.",
    )
    agent_version = fields.Char(
        string="Agent Version",
        help="IoT agent version installed on the Raspberry Pi or host.",
    )
    agent_capabilities = fields.Text(
        string="Agent Capabilities",
        default="[]",
        readonly=True,
        help="JSON list of bounded capabilities advertised by the installed agent.",
    )
    pdf_print_capable = fields.Boolean(
        string="Document Printing Available",
        compute="_compute_pdf_print_capable",
        store=True,
    )
    state = fields.Selection(
        selection=[
            ("draft", "Draft"),
            ("online", "Online"),
            ("offline", "Offline"),
            ("error", "Error"),
        ],
        string="Status",
        default="draft",
        required=True,
        help="Latest reported status; Online expires when the heartbeat grace period is exceeded.",
    )
    last_seen = fields.Datetime(
        string="Last Seen",
        help="Date and time of the latest heartbeat received from the IoT Box.",
    )
    config_version = fields.Integer(
        string="Config Version",
        default=1,
        help="Configuration version number used to synchronize with the agent.",
    )

    device_ids = fields.One2many(
        "community_iot_box.iot_device",
        "box_id",
        string="Devices",
    )
    device_count = fields.Integer(
        string="Devices Count",
        compute="_compute_device_count",
        store=False,
    )
    job_ids = fields.One2many(
        "community_iot_box.iot_job",
        "box_id",
        string="IoT Jobs",
    )
    job_count = fields.Integer(
        string="Jobs Count",
        compute="_compute_job_count",
        store=False,
    )

    @api.depends("device_ids")
    def _compute_device_count(self):
        for box in self:
            box.device_count = len(box.device_ids)

    @api.depends("job_ids")
    def _compute_job_count(self):
        for box in self:
            box.job_count = len(box.job_ids)

    @api.depends("agent_capabilities")
    def _compute_pdf_print_capable(self):
        for box in self:
            box.pdf_print_capable = (
                box.supports_capability("pdf_print_v1")
                or box.supports_capability("pdf_print_v2")
                or box.supports_capability("document_print_v1")
            )

    def supports_capability(self, capability):
        self.ensure_one()
        try:
            capabilities = json.loads(self.agent_capabilities or "[]")
        except (TypeError, ValueError):
            capabilities = []
        return capability in capabilities

    @api.model
    def _heartbeat_timeout_seconds(self):
        value = self.env["ir.config_parameter"].sudo().get_param(
            "community_iot_box.heartbeat_timeout_seconds", "180"
        )
        try:
            seconds = int(value)
        except (TypeError, ValueError):
            return 180
        return seconds if seconds > 0 else 180

    @api.model
    def _expire_stale_heartbeats(self, domain=None):
        """Persist Offline, without overwriting a concurrent successful heartbeat."""
        now = fields.Datetime.now()
        cutoff = now - timedelta(seconds=self._heartbeat_timeout_seconds())
        self.flush_model(["state", "last_seen"])
        boxes = self.search((domain or []) + [
            ("state", "=", "online"),
            "|", ("last_seen", "=", False), ("last_seen", "<", cutoff),
        ])
        if not boxes:
            return 0

        # Recheck freshness in the UPDATE: the agent may have renewed last_seen
        # after the search. PostgreSQL serializes concurrent writes to the box.
        self.env.cr.execute(
            """
            UPDATE community_iot_box_iot_box
               SET state = 'offline', write_date = %s, write_uid = %s
             WHERE id IN %s AND state = 'online'
               AND (last_seen IS NULL OR last_seen < %s)
            RETURNING id
            """,
            (now, self.env.uid, tuple(boxes.ids), cutoff),
        )
        expired = self.browse([row[0] for row in self.env.cr.fetchall()])
        expired.invalidate_recordset(["state", "write_date", "write_uid"])
        expired.modified(["state"])
        return len(expired)

    @api.model
    def _cron_expire_stale_heartbeats(self):
        return self.sudo()._expire_stale_heartbeats()

    @api.model
    def get_dashboard_data(self):
        """Return the operational dashboard without exposing box tokens."""
        self.check_access_rights("read")
        Device = self.env["community_iot_box.iot_device"]
        Job = self.env["community_iot_box.iot_job"]
        Device.check_access_rights("read")
        Job.check_access_rights("read")

        company_ids = self.env.companies.ids
        box_domain = [("company_id", "in", company_ids)]
        job_domain = [("company_id", "in", company_ids)]
        device_domain = [("box_id.company_id", "in", company_ids)]

        self.sudo()._expire_stale_heartbeats(box_domain)
        boxes = self.search(box_domain, order="state, name, id", limit=6)
        now = fields.Datetime.to_datetime(fields.Datetime.now())
        freshness = timedelta(minutes=5)
        recent_jobs = Job.search(
            job_domain,
            order="create_date desc, id desc",
            limit=6,
        )

        boxes_total = self.search_count(box_domain)
        boxes_online = self.search_count(box_domain + [("state", "=", "online")])
        boxes_attention = self.search_count(
            box_domain + [("state", "in", ("offline", "error"))]
        )
        devices_total = Device.search_count(device_domain)
        jobs_pending = Job.search_count(
            job_domain + [("state", "in", ("pending", "processing"))]
        )
        jobs_error = Job.search_count(job_domain + [("state", "=", "error")])

        box_state_labels = {
            "draft": "Draft",
            "online": "Online",
            "offline": "Offline",
            "error": "Error",
        }
        job_state_labels = {
            "pending": "Pending",
            "processing": "Processing",
            "done": "Completed",
            "error": "Error",
            "cancelled": "Cancelled",
        }
        job_type_labels = dict(Job._fields["job_type"].selection)

        box_cards = []
        enabled_configured_total = reachable_total = 0
        all_devices = Device.with_context(active_test=False).search(device_domain)
        devices_by_box = {}
        for device in all_devices:
            devices_by_box.setdefault(device.box_id.id, Device.browse())
            devices_by_box[device.box_id.id] |= device
        for device in all_devices:
            checked = fields.Datetime.to_datetime(device.health_checked_at) if device.health_checked_at else None
            if device.active:
                enabled_configured_total += 1
                if device.health_status == "connected" and checked and timedelta(0) <= now - checked <= freshness:
                    reachable_total += 1
        for box in boxes:
            heartbeat = fields.Datetime.to_datetime(box.last_seen) if box.last_seen else None
            heartbeat_fresh = bool(heartbeat and timedelta(0) <= now - heartbeat <= freshness)
            devices = devices_by_box.get(box.id, Device.browse())
            enabled = devices.filtered("active")
            reachable = enabled.filtered(
                lambda device: device.health_status == "connected"
                and device.health_checked_at
                and timedelta(0) <= now - fields.Datetime.to_datetime(device.health_checked_at) <= freshness
            )
            if not heartbeat_fresh:
                health_state, health_label = "red", "Offline"
            elif not enabled or len(reachable) != len(enabled):
                health_state, health_label = "yellow", "Attention"
            else:
                health_state, health_label = "normal", "Ready"
            device_cards = []
            for device in devices:
                checked = fields.Datetime.to_datetime(device.health_checked_at) if device.health_checked_at else None
                current = bool(checked and timedelta(0) <= now - checked <= freshness)
                status = device.health_status if current else "unknown"
                device_cards.append({
                    "id": device.id, "name": device.name, "active": device.active,
                    "health_status": status,
                    "health_label": {"connected": "Connected", "disconnected": "Disconnected", "unknown": "Unknown"}[status],
                    "health_checked_at": fields.Datetime.to_string(device.health_checked_at) if device.health_checked_at else False,
                })
            box_cards.append(
                {
                    "id": box.id,
                    "name": box.name,
                    "company": box.company_id.name,
                    "state": box.state,
                    "state_label": box_state_labels.get(box.state, box.state),
                    "ip_address": box.ip_address or False,
                    "agent_version": box.agent_version or False,
                    "last_seen": (
                        fields.Datetime.to_string(box.last_seen)
                        if box.last_seen
                        else False
                    ),
                    "device_count": box.device_count,
                    "token_configured": bool(box.token),
                    "health_state": health_state,
                    "health_label": health_label,
                    "enabled_configured_count": len(enabled),
                    "reachable_count": len(reachable),
                    "devices": device_cards,
                }
            )

        jobs = []
        for job in recent_jobs:
            jobs.append(
                {
                    "id": job.id,
                    "name": job.name or f"Job #{job.id}",
                    "box": job.box_id.name or "-",
                    "device": job.device_id.name or job.device_key or "-",
                    "job_type": job_type_labels.get(job.job_type, job.job_type),
                    "state": job.state,
                    "state_label": job_state_labels.get(job.state, job.state),
                    "attempt_count": job.attempt_count,
                    "created_at": (
                        fields.Datetime.to_string(job.create_date)
                        if job.create_date
                        else False
                    ),
                }
            )

        alerts = []
        attention_boxes = self.search(
            box_domain + [("state", "in", ("offline", "error"))],
            order="write_date desc, id desc",
            limit=3,
        )
        for box in attention_boxes:
            alerts.append(
                {
                    "key": f"box-{box.id}",
                    "level": "danger" if box.state == "error" else "warning",
                    "icon": "fa-exclamation-triangle",
                    "title": box_state_labels.get(box.state, box.state),
                    "message": (
                        f"{box.name}: last contact "
                        f"{fields.Datetime.to_string(box.last_seen)}"
                        if box.last_seen
                        else f"{box.name}: no communication has been recorded yet."
                    ),
                }
            )

        if len(alerts) < 4:
            failed_jobs = Job.search(
                job_domain + [("state", "=", "error")],
                order="write_date desc, id desc",
                limit=4 - len(alerts),
            )
            for job in failed_jobs:
                alerts.append(
                    {
                        "key": f"job-{job.id}",
                        "level": "danger",
                        "icon": "fa-times-circle",
                        "title": "Job with error",
                        "message": (
                            f"{job.name or f'Job #{job.id}'} · "
                            f"{job.box_id.name or 'No box'}"
                        ),
                    }
                )

        return {
            "metrics": {
                "boxes_total": boxes_total,
                "boxes_online": boxes_online,
                "devices_total": devices_total,
                "enabled_configured": enabled_configured_total,
                "reachable_devices": reachable_total,
                "jobs_pending": jobs_pending,
                "attention": boxes_attention + jobs_error,
            },
            "boxes": box_cards,
            "jobs": jobs,
            "alerts": alerts,
        }

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if not vals.get("token"):
                vals["token"] = self._new_token()
        return super().create(vals_list)

    def _new_token(self):
        Box = self.sudo().with_context(active_test=False)
        while True:
            token = secrets.token_urlsafe(32)
            domain = [("token", "=", token)]
            if self.ids:
                domain.append(("id", "not in", self.ids))
            if not Box.search(domain, limit=1):
                return token

    def action_generate_token(self):
        for box in self:
            box.token = box._new_token()
        return {
            "type": "ir.actions.client",
            "tag": "display_notification",
            "params": {
                "title": "IoT Token",
                "message": "Token generated successfully.",
                "type": "success",
                "sticky": False,
            },
        }

    def _increment_config_version(self):
        for box in self.sudo():
            box.write({"config_version": (box.config_version or 0) + 1})

    def action_open_devices(self):
        self.ensure_one()
        action = self.env.ref("community_iot_box.action_community_iot_devices").read()[0]
        action["domain"] = [("box_id", "=", self.id)]
        action["context"] = {"default_box_id": self.id}
        action["view_mode"] = "tree,form"
        return action

    def action_open_jobs(self):
        self.ensure_one()
        action = self.env.ref("community_iot_box.action_community_iot_jobs").read()[0]
        action["domain"] = [("box_id", "=", self.id)]
        action["context"] = {"default_box_id": self.id}
        action["view_mode"] = "tree,form"
        return action

    def action_test_connection(self):
        self.ensure_one()
        self.sudo()._expire_stale_heartbeats([("id", "=", self.id)])
        if not self.active:
            return self._notification(
                title="Test connection",
                message="The IoT Box is inactive. Activate it before running tests.",
                level="warning",
            )

        if not self.token:
            return self._notification(
                title="Test connection",
                message="The IoT Box has no token. Generate one before testing the connection.",
                level="warning",
            )

        active_devices = self.device_ids.filtered("active")
        if not self.last_seen:
            return self._notification(
                title="Test connection",
                message=(
                    "No heartbeat has been recorded yet. Start the agent and wait a few seconds. "
                    "No print job was created."
                ),
                level="warning",
            )

        now_dt = fields.Datetime.to_datetime(fields.Datetime.now())
        last_seen_dt = fields.Datetime.to_datetime(self.last_seen)
        elapsed_seconds = 0
        if now_dt and last_seen_dt:
            elapsed_seconds = max(0, int((now_dt - last_seen_dt).total_seconds()))
        elapsed_display = self._format_elapsed_seconds(elapsed_seconds)

        last_seen_local = fields.Datetime.context_timestamp(self, self.last_seen)
        last_seen_display = (
            last_seen_local.strftime("%Y-%m-%d %H:%M:%S")
            if last_seen_local
            else str(self.last_seen)
        )

        if self.state == "online":
            return self._notification(
                title="Test connection",
                message=(
                    f"Connection OK. Status: {self.state}. Latest heartbeat was {elapsed_display} ago "
                    f"({last_seen_display}). Active devices: {len(active_devices)}. "
                    "No print was sent."
                ),
                level="success",
            )

        return self._notification(
            title="Test connection",
            message=(
                f"Connection not verified: status={self.state}, latest heartbeat was {elapsed_display} ago "
                f"({last_seen_display}). Active devices: {len(active_devices)}. "
                "No print was sent."
            ),
            level="warning",
        )

    def _notification(self, title, message, level="info"):
        return {
            "type": "ir.actions.client",
            "tag": "display_notification",
            "params": {
                "title": title,
                "message": message,
                "type": level,
                "sticky": False,
            },
        }

    def _test_job_type_for_device(self, device_type):
        mapping = {
            "ticket_printer": "test_ticket",
            "standard_printer": "test_ticket",
            "label_printer": "test_label",
            "drawer": "test_drawer",
        }
        return mapping.get(device_type, "test_ticket")

    def _build_generic_test_ticket(self, device):
        self.ensure_one()
        company_name = (self.company_id.name or "COMPANY").upper()
        box_name = self.name or "IoT Box"
        device_name = device.name or device.device_key or "Device"
        now_local = fields.Datetime.context_timestamp(self, fields.Datetime.now())
        dt_str = now_local.strftime("%Y-%m-%d %H:%M:%S")

        width = device._get_ticket_text_width() if hasattr(device, "_get_ticket_text_width") else 42

        separator = "-" * width
        heavy_separator = "=" * width
        type_label = dict(device._fields["type"].selection).get(device.type, device.type or "-")
        backend_label = dict(device._fields["backend"].selection).get(
            device.backend,
            device.backend or "-",
        )
        interface_label = dict(device._fields["interface"].selection).get(
            device.interface,
            device.interface or "-",
        )

        lines = [
            heavy_separator,
            self._center_text(company_name, width),
            self._center_text("IoT BOX COMMUNITY", width),
            self._center_text("TEST TICKET", width),
            heavy_separator,
            self._label_value_line("Date/Time", dt_str, width),
            self._label_value_line("IoT Box", box_name, width),
            self._label_value_line("Hostname", self.hostname or "-", width),
            self._label_value_line("Agent UID", self.box_uid or "-", width),
            self._label_value_line("IP", self.ip_address or "-", width),
            self._label_value_line("Device", device_name, width),
            self._label_value_line("Device Key", device.device_key or "-", width),
            self._label_value_line("Type", type_label, width),
            self._label_value_line("Backend", backend_label, width),
            self._label_value_line("Interface", interface_label, width),
            separator,
            self._format_ticket_item("1", "DEMO PRODUCT", 10.00, width),
            self._format_ticket_item("1", "ANOTHER DEMO PRODUCT", 25.00, width),
            separator,
            self._format_ticket_amount("TOTAL", 35.00, width),
            separator,
            self._center_text("If you can read this, printing is OK.", width),
            self._center_text("Thank you for using IoT Box Community", width),
            heavy_separator,
        ]

        if width >= 80:
            lines = ["", "", *lines, "", ""]

        return lines

    def _format_ticket_item(self, qty, description, amount, width):
        left = f"{qty} x {description}"
        right = f"{amount:,.2f}"
        max_left = max(1, width - len(right) - 1)
        left = self._truncate_text(left, max_left)
        space_count = max(1, width - len(left) - len(right))
        return f"{left}{' ' * space_count}{right}"

    def _format_ticket_amount(self, label, amount, width):
        left = str(label)
        right = f"{amount:,.2f}"
        max_left = max(1, width - len(right) - 1)
        left = self._truncate_text(left, max_left)
        space_count = max(1, width - len(left) - len(right))
        return f"{left}{' ' * space_count}{right}"

    def _label_value_line(self, label, value, width):
        prefix = f"{label}: "
        available = max(5, width - len(prefix))
        safe_value = self._truncate_text(value or "-", available)
        return f"{prefix}{safe_value}"

    def _center_text(self, text, width):
        return self._truncate_text(text or "", width).center(width)

    def _truncate_text(self, text, max_length):
        raw = str(text or "")
        if len(raw) <= max_length:
            return raw
        if max_length <= 3:
            return raw[:max_length]
        return f"{raw[: max_length - 3]}..."

    def _format_elapsed_seconds(self, seconds):
        total = max(0, int(seconds or 0))
        if total < 60:
            return f"{total}s"
        minutes, rem = divmod(total, 60)
        if minutes < 60:
            return f"{minutes}m {rem}s"
        hours, rem_min = divmod(minutes, 60)
        return f"{hours}h {rem_min}m"
