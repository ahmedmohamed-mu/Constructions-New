from datetime import timedelta

from odoo import _, models

from odoo.addons.hom_control_tower.models.control_tower import _act


class HomControlTower(models.AbstractModel):
    _inherit = "hom.control.tower"

    def _alerts(self, company, today):
        out = super()._alerts(company, today)
        env = self.sudo().env
        soon = env["crm.lead"].search([("company_id", "in", (company.id, False)), ("zc_is_tender", "=", True),
                                       ("active", "=", True), ("zc_submission_deadline", ">=", today),
                                       ("zc_submission_deadline", "<=", today + timedelta(days=7)),
                                       ("zc_decision", "!=", "no_bid")])
        soon = soon.filtered(lambda l: not l.order_ids.filtered(lambda o: o.state in ("sent", "sale")))
        if soon:
            out.append({"level": "warning", "count": len(soon), "amount": sum(soon.mapped("expected_revenue")),
                        "text": _("Tenders to submit within 7 days"),
                        "action": _act(_("Tenders due"), "crm.lead", [("id", "in", soon.ids)])})
        undecided = env["crm.lead"].search([("company_id", "in", (company.id, False)), ("zc_is_tender", "=", True),
                                            ("active", "=", True), ("zc_decision", "=", "open")])
        if undecided:
            out.append({"level": "info", "count": len(undecided), "amount": sum(undecided.mapped("expected_revenue")),
                        "text": _("Tenders waiting for the Bid / No-Bid decision"),
                        "action": _act(_("Bid / No-Bid"), "crm.lead", [("id", "in", undecided.ids)])})
        order = {"danger": 0, "warning": 1, "info": 2}
        out.sort(key=lambda a: order[a["level"]])
        return out
