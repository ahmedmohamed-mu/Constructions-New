from datetime import timedelta

from odoo import _, models

from odoo.addons.hom_control_tower.models.control_tower import _act


class HomControlTower(models.AbstractModel):
    _inherit = "hom.control.tower"

    def _alerts(self, company, today):
        out = super()._alerts(company, today)
        env = self.sudo().env
        since = today - timedelta(days=30)
        incidents = env["zc.daily.report"].search([("company_id", "=", company.id), ("date", ">=", since),
                                                   ("incident", "in", ("medical", "lost_time"))])
        if incidents:
            out.append({"level": "danger", "count": len(incidents), "amount": 0.0,
                        "text": _("Safety incidents with injury in the last 30 days"),
                        "action": _act(_("Safety incidents"), "zc.daily.report", [("id", "in", incidents.ids)])})
        subs = env["zc.claim"].search([("company_id", "=", company.id), ("claimant", "=", "sub"),
                                       ("state", "in", ("notified", "submitted")), ("owner_claim_id", "=", False)])
        if subs:
            out.append({"level": "warning", "count": len(subs), "amount": sum(subs.mapped("amount_claimed")),
                        "text": _("Subcontractor claims not answered or not passed to the owner"),
                        "action": _act(_("Subcontractor claims"), "zc.claim", [("id", "in", subs.ids)])})
        order = {"danger": 0, "warning": 1, "info": 2}
        out.sort(key=lambda a: order[a["level"]])
        return out
