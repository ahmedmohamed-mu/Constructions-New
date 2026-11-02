from datetime import timedelta

from odoo import _, models

from odoo.addons.hom_control_tower.models.control_tower import _act


class HomControlTower(models.AbstractModel):
    _inherit = "hom.control.tower"

    def _alerts(self, company, today):
        out = super()._alerts(company, today)
        env = self.sudo().env
        rfis = env["zc.rfi"].search([("company_id", "=", company.id), ("state", "=", "sent"), ("due_date", "<", today)])
        if rfis:
            out.append({"level": "warning", "count": len(rfis), "amount": 0.0,
                        "text": _("RFIs unanswered by the consultant past their due date"),
                        "action": _act(_("Late RFIs"), "zc.rfi", [("id", "in", rfis.ids)])})
        ncrs = env["zc.ncr"].search([("company_id", "=", company.id), ("state", "=", "open"), ("due_date", "<", today)])
        if ncrs:
            out.append({"level": "danger", "count": len(ncrs), "amount": sum(ncrs.mapped("backcharge_amount")),
                        "text": _("Non-conformances not corrected on time"),
                        "action": _act(_("Late NCRs"), "zc.ncr", [("id", "in", ncrs.ids)])})
        # contracts in execution with no site daily report for the last working day
        yesterday = today - timedelta(days=2 if today.weekday() == 5 else 1)
        active = env["sale.order"].search([("company_id", "=", company.id), ("state", "=", "sale"),
                                           ("hom_project_type", "=", "contracting")])
        if "zc_closeout_state" in env["sale.order"]._fields:
            active = active.filtered(lambda o: o.zc_closeout_state in (False, "running"))
        reported = env["zc.daily.report"].search([("sale_order_id", "in", active.ids), ("date", ">=", yesterday)])
        started = active.filtered(lambda o: env["zc.daily.report"].search_count([("sale_order_id", "=", o.id)]))
        missing = started - reported.sale_order_id
        if missing:
            out.append({"level": "info", "count": len(missing), "amount": 0.0,
                        "text": _("Projects without a site daily report since %s", yesterday),
                        "action": _act(_("No daily report"), "sale.order", [("id", "in", missing.ids)])})
        order = {"danger": 0, "warning": 1, "info": 2}
        out.sort(key=lambda a: order[a["level"]])
        return out
