"""Legacy PDF dispatch protocol.

This module deliberately contains the v1 policy rather than sharing the v2
dispatch decisions with the job model.
"""


class PdfDispatchV1:
    grouped = False
    requires_result_id = False
    recovery_filter = ""

    @staticmethod
    def recover_expired(model, box):
        return

    @staticmethod
    def expire_deadlines(model, box):
        return

    @staticmethod
    def accepts_terminal_result(job, result, final_state):
        return final_state in ("done", "error")

    @staticmethod
    def accepts_uncertain_result(job, result, final_state):
        return False

    @staticmethod
    def finish_pending(job, values):
        job.release_for_retry()