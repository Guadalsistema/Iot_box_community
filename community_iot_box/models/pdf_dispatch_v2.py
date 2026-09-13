"""Grouped PDF dispatch protocol (v2)."""


class PdfDispatchV2:
    grouped = True
    requires_result_id = True
    recovery_filter = "AND job_type != 'document_print'"

    @staticmethod
    def recover_expired(model, box):
        model.env.cr.execute(
            """UPDATE community_iot_box_iot_job SET state = 'uncertain',
                    claimed_at = NULL, lease_expires_at = NULL, lock_token = NULL,
                    error_code = 'expired_processing', error_message = 'Lease expired while processing'
                WHERE box_id = %s AND job_type = 'document_print' AND state = 'processing'
                  AND lease_expires_at IS NOT NULL AND lease_expires_at < NOW()
                RETURNING id""",
            [box.id],
        )
        ids = [row[0] for row in model.env.cr.fetchall()]
        if ids:
            model.browse(ids).invalidate_recordset(
                ["state", "claimed_at", "lease_expires_at", "lock_token", "error_code", "error_message"],
                flush=False,
            )

    @staticmethod
    def expire_deadlines(model, box):
        model.env.cr.execute(
            """UPDATE community_iot_box_iot_job
                   SET state = 'uncertain', error_code = 'expired_claim_deadline',
                       error_message = 'Dispatch deadline expired before claim'
                WHERE box_id = %s AND job_type = 'document_print' AND state = 'pending'
                  AND claim_deadline IS NOT NULL AND claim_deadline < NOW()
                RETURNING id""",
            [box.id],
        )
        ids = [row[0] for row in model.env.cr.fetchall()]
        if ids:
            model.browse(ids).invalidate_recordset(
                ["state", "error_code", "error_message"], flush=False
            )

    @staticmethod
    def accepts_terminal_result(job, result, final_state):
        return final_state in ("done", "error") and result.get("result_id") == job.result_id

    @staticmethod
    def accepts_uncertain_result(job, result, final_state):
        return final_state == "pending" and result.get("result_id") == job.result_id

    @staticmethod
    def finish_pending(job, values):
        job.finish_from_agent(values)