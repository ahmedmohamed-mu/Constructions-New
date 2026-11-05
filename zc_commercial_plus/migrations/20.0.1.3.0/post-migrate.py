import logging

from odoo import SUPERUSER_ID, api

_logger = logging.getLogger(__name__)


def migrate(cr, version):
    """Existing contract projects get the standard plan (dates spread over their duration, status from the site).
    A project that fails is skipped: the plan can be loaded later from its form, the upgrade goes on."""
    env = api.Environment(cr, SUPERUSER_ID, {})
    template = env["zc.project.plan.template"].search([("is_default", "=", True)], limit=1)
    if not template:
        return
    projects = env["project.project"].search([("sale_order_id", "!=", False), ("date_start", "!=", False),
                                              ("date", "!=", False)])
    for project in projects.filtered(lambda p: not p.task_ids):
        try:
            with cr.savepoint():
                project._zc_build_plan(template)
        except Exception:
            _logger.exception("Project plan not loaded on %s", project.display_name)
