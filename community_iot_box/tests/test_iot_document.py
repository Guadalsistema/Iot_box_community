import hashlib
import json

from odoo import fields
from odoo.exceptions import UserError, ValidationError
from odoo.tests.common import TransactionCase, tagged


PDF_BYTES = b"%PDF-1.4\n% Community IoT Odoo test\n%%EOF\n"
JPEG_BYTES = b"\xff\xd8\xff\xe0native-jpeg\xff\xd9"
WEBP_BYTES = b"RIFF\x12\x00\x00\x00WEBPVP8L\x05\x00\x00\x00\x2f\x00\x00\x00\x00\x00"


@tagged("post_install", "-at_install")
class TestCommunityIotDocument(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.box = cls.env["community_iot_box.iot_box"].create(
            {
                "name": "PDF Box",
                "state": "online",
                "agent_capabilities": json.dumps(["pdf_print_v2"]),
            }
        )
        cls.device = cls.env["community_iot_box.iot_device"].create(
            {
                "name": "Office A4",
                "box_id": cls.box.id,
                "device_key": "office_a4",
                "type": "standard_printer",
                "backend": "standard",
                "interface": "cups",
                "cups_printer_name": "Office_A4",
                "printer_capabilities": json.dumps(
                    {"document_formats": ["application/pdf", "image/jpeg", "image/webp"]}
                ),
            }
        )
        cls.Job = cls.env["community_iot_box.iot_job"]

    def _create_jobs(self, copies=2):
        return self.Job._create_pdf_jobs(
            device=self.device,
            pdf_content=PDF_BYTES,
            filename="quotation.pdf",
            copies=copies,
            name="Quotation",
            payload={"source": "test"},
            origin_model="res.partner",
        )

    def _make_attachment_old(self, attachment):
        self.env.cr.execute(
            "UPDATE ir_attachment SET create_date = NOW() - INTERVAL '13 hours' WHERE id = %s",
            [attachment.id],
        )
        attachment.invalidate_recordset(["create_date"])

    def test_pdf_jobs_share_bounded_attachment_and_metadata(self):
        jobs = self._create_jobs(copies=2)
        self.assertEqual(len(jobs), 2)
        self.assertEqual(len(jobs.mapped("document_attachment_id")), 1)
        self.assertEqual(set(jobs.mapped("document_size")), {len(PDF_BYTES)})
        self.assertEqual(set(jobs.mapped("document_mimetype")), {"application/pdf"})
        self.assertTrue(all(len(value) == 64 for value in jobs.mapped("document_sha256")))
        self.assertTrue(all("%PDF" not in payload for payload in jobs.mapped("payload")))

    def test_native_formats_share_document_creation_interface(self):
        for mimetype, content, filename in (
            ("application/pdf", PDF_BYTES, "quote.bin"),
            ("image/jpeg", JPEG_BYTES, "photo.pdf"),
            ("image/webp", WEBP_BYTES, "preview.jpeg"),
        ):
            job = self.Job._create_document_jobs(
                device=self.device,
                content=content,
                mimetype=mimetype,
                filename=filename,
            )
            extension = {"application/pdf": ".pdf", "image/jpeg": ".jpg", "image/webp": ".webp"}[mimetype]
            self.assertEqual(job.document_mimetype, mimetype)
            self.assertEqual(job.document_filename, filename.rsplit(".", 1)[0] + extension)
            self.assertEqual(job.document_size, len(content))
            self.assertEqual(job.document_sha256, hashlib.sha256(content).hexdigest())
            self.assertEqual(job.document_attachment_id.raw, content)

    def test_document_creation_rejects_mismatch_and_unsupported_device(self):
        documents = (
            ("application/pdf", PDF_BYTES),
            ("image/jpeg", JPEG_BYTES),
            ("image/webp", WEBP_BYTES),
        )
        for mimetype, _content in documents:
            with self.assertRaises(ValidationError):
                self.Job._create_document_jobs(
                    device=self.device,
                    content=b"not-the-declared-format",
                    mimetype=mimetype,
                    filename="bad.bin",
                )

        with self.assertRaises(ValidationError):
            self.Job._create_document_jobs(
                device=self.device,
                content=b"\x89PNG\r\n\x1a\n",
                mimetype="image/png",
                filename="unsupported.png",
            )

        all_formats = {mimetype for mimetype, _content in documents}
        for mimetype, content in documents:
            self.device.printer_capabilities = json.dumps(
                {"document_formats": sorted(all_formats - {mimetype})}
            )
            with self.assertRaises(ValidationError):
                self.Job._create_document_jobs(
                    device=self.device,
                    content=content,
                    mimetype=mimetype,
                    filename="unsupported.bin",
                )

    def test_non_pdf_and_excessive_copies_are_rejected(self):
        with self.assertRaises(ValidationError):
            self.Job._create_pdf_jobs(
                device=self.device,
                pdf_content=b"not-pdf",
                filename="bad.pdf",
            )
        with self.assertRaises(ValidationError):
            self._create_jobs(copies=11)

    def test_pdf_group_does_not_exceed_requested_limit(self):
        self._create_jobs(copies=2)
        self.assertFalse(self.Job.claim_for_box(
            self.box, limit=1, supported_job_types=["document_print"]
        ))

    def test_pdf_v1_claims_copies_individually_and_retries_expired_leases(self):
        jobs = self._create_jobs(copies=2)
        first_claim = self.Job.claim_for_box(
            self.box,
            limit=1,
            supported_job_types=["document_print"],
            document_dispatch_version="v1",
        )

        self.assertEqual(first_claim, jobs[:1])
        self.assertEqual(jobs[1].state, "pending")
        first_claim.lease_expires_at = fields.Datetime.subtract(
            fields.Datetime.now(), seconds=1
        )

        retried = self.Job.claim_for_box(
            self.box,
            limit=1,
            supported_job_types=["document_print"],
            document_dispatch_version="v1",
        )

        self.assertEqual(retried, first_claim)
        self.assertEqual(first_claim.state, "processing")
        self.assertEqual(first_claim.attempt_count, 2)

    def test_legacy_claim_does_not_partially_claim_following_pdf_group(self):
        ticket = self.Job.create(
            {
                "name": "Legacy ticket",
                "box_id": self.box.id,
                "job_type": "ticket_print",
                "payload": "{}",
            }
        )
        documents = self._create_jobs(copies=2)

        claimed = self.Job.claim_for_box(
            self.box,
            limit=2,
            supported_job_types=["ticket_print", "document_print"],
        )

        self.assertEqual(claimed, ticket)
        self.assertEqual(documents.mapped("state"), ["pending", "pending"])

    def test_capability_filter_keeps_pdf_from_legacy_agent(self):
        jobs = self._create_jobs(copies=1)
        claimed = self.Job.claim_for_box(
            self.box,
            limit=5,
            supported_job_types=["ticket_print"],
        )
        self.assertFalse(claimed)
        self.assertEqual(jobs.state, "pending")

    def test_pdf_only_claim_filter_never_leases_native_image(self):
        image = self.Job._create_document_jobs(
            device=self.device,
            content=JPEG_BYTES,
            mimetype="image/jpeg",
            filename="photo.jpg",
        )
        self.assertFalse(
            self.Job.claim_for_box_v2(
                self.box,
                supported_job_types=["document_print"],
                supported_document_mimetypes=["application/pdf"],
            )
        )
        self.assertEqual(image.state, "pending")

        claimed = self.Job.claim_for_box_v2(
            self.box,
            supported_job_types=["document_print"],
            supported_document_mimetypes=["application/pdf", "image/jpeg", "image/webp"],
        )
        self.assertEqual(claimed, image)

    def test_tampered_document_cannot_report_success(self):
        job = self.Job._create_document_jobs(
            device=self.device,
            content=JPEG_BYTES,
            mimetype="image/jpeg",
            filename="photo.jpg",
        )
        claimed = self.Job.claim_for_box_v2(
            self.box,
            supported_job_types=["document_print"],
            supported_document_mimetypes=["image/jpeg"],
        )
        job.document_attachment_id.raw = b"\xff\xd8tampered\xff\xd9"
        result = self.Job.apply_results_for_box_v2(
            self.box,
            [{
                "job_id": job.id,
                "lock_token": claimed.lock_token,
                "state": "done",
                "result_id": "tampered-result",
            }],
        )
        self.assertEqual(result["accepted"], 0)
        self.assertEqual(result["rejected"][0]["reason"], "document_invalid")
        self.assertEqual(job.state, "processing")

    def test_successful_copies_remove_pdf_only_after_last_result(self):
        jobs = self._create_jobs(copies=2)
        attachment = jobs.document_attachment_id
        claimed = self.Job.claim_for_box(
            self.box,
            limit=2,
            supported_job_types=["document_print"],
        )
        first, second = claimed.sorted("id")
        self.Job.apply_results_for_box(
            self.box,
            [{"job_id": first.id, "lock_token": first.lock_token, "state": "done", "result_id": "copy-1"}],
        )
        self.assertTrue(attachment.exists())
        self.Job.apply_results_for_box(
            self.box,
            [{"job_id": second.id, "lock_token": second.lock_token, "state": "done", "result_id": "copy-2"}],
        )
        self.assertFalse(attachment.exists())
        self.assertFalse(jobs.document_attachment_id)

    def test_failed_pdf_can_retry_and_old_attachment_is_cleaned(self):
        job = self._create_jobs(copies=1)
        claimed = self.Job.claim_for_box(
            self.box,
            limit=1,
            supported_job_types=["document_print"],
        )
        self.Job.apply_results_for_box(
            self.box,
            [{"job_id": job.id, "lock_token": claimed.lock_token, "state": "error", "result_id": "failed-1"}],
        )
        self.assertEqual(job.state, "error")
        job.action_retry_document()
        self.assertEqual(job.state, "pending")

        job.write({"state": "error"})
        attachment = job.document_attachment_id
        self._make_attachment_old(attachment)
        self.Job._cron_cleanup_expired_documents()
        self.assertFalse(attachment.exists())
        with self.assertRaises(UserError):
            job.action_retry_document()

    def test_document_retry_result_is_uncertain_not_requeued(self):
        job = self._create_jobs(copies=1)
        claimed = self.Job.claim_for_box(self.box, limit=1, supported_job_types=["document_print"])
        report = {
            "job_id": job.id,
            "lock_token": claimed.lock_token,
            "state": "retry",
            "result_id": "ambiguous-1",
        }
        result = self.Job.apply_results_for_box(
            self.box,
            [report],
        )
        self.assertEqual(result["accepted"], 1)
        self.assertEqual(job.state, "uncertain")

        replay = self.Job.apply_results_for_box(self.box, [report])

        self.assertEqual(replay["accepted"], 1)
        self.assertEqual(
            replay["accepted_pairs"],
            [{"job_id": job.id, "result_id": "ambiguous-1"}],
        )

    def test_grouped_document_copies_cannot_be_retried_individually(self):
        jobs = self._create_jobs(copies=2)
        jobs[0].write({"state": "error"})
        with self.assertRaises(UserError):
            jobs[0].action_retry_document()

    def test_group_claim_requires_all_siblings_pending(self):
        jobs = self._create_jobs(copies=2)
        jobs[1].write({"state": "error"})
        self.assertFalse(self.Job.claim_for_box(
            self.box, limit=2, supported_job_types=["document_print"]
        ))
        self.assertEqual(jobs[0].state, "pending")

    def test_expired_document_lease_is_retained_then_old_attachment_is_cleaned(self):
        job = self._create_jobs(copies=1)
        attachment = job.document_attachment_id
        self.Job.claim_for_box(self.box, limit=1, supported_job_types=["document_print"])
        job.flush_recordset(["state", "lease_expires_at"])
        self.env.cr.execute(
            """UPDATE community_iot_box_iot_job
                  SET lease_expires_at = NOW() - INTERVAL '1 second'
                WHERE id = %s""",
            [job.id],
        )
        job.invalidate_recordset(["lease_expires_at"])

        self.assertFalse(self.Job.claim_for_box(
            self.box, limit=1, supported_job_types=["document_print"]
        ))
        self.assertEqual(job.state, "uncertain")
        self.assertTrue(attachment.exists())

        self._make_attachment_old(attachment)
        self.Job._cron_cleanup_expired_documents()
        self.assertFalse(attachment.exists())
        self.assertFalse(job.document_attachment_id)
        self.assertEqual(job.document_sha256, hashlib.sha256(PDF_BYTES).hexdigest())

    def test_expired_claim_deadline_marks_document_uncertain(self):
        job = self._create_jobs(copies=1)
        job.write({"claim_deadline": fields.Datetime.subtract(fields.Datetime.now(), days=1)})
        job.flush_recordset(["claim_deadline"])

        self.assertFalse(self.Job.claim_for_box(
            self.box, limit=1, supported_job_types=["document_print"]
        ))
        self.assertEqual(job.state, "uncertain")
        self.assertEqual(job.error_code, "expired_claim_deadline")

    def test_shared_old_document_is_removed_after_all_jobs_are_terminal(self):
        jobs = self._create_jobs(copies=2)
        attachment = jobs.document_attachment_id
        claimed = self.Job.claim_for_box(
            self.box, limit=2, supported_job_types=["document_print"]
        )
        for job in claimed:
            self.Job.apply_results_for_box(
                self.box,
                [{"job_id": job.id, "lock_token": job.lock_token,
                  "state": "error", "result_id": f"failed-{job.id}"}],
            )
        self._make_attachment_old(attachment)
        self.Job._cron_cleanup_expired_documents()
        self.assertFalse(attachment.exists())
