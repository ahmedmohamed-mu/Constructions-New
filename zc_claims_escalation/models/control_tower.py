from datetime import timedelta

from odoo import _, models

from odoo.addons.hom_control_tower.models.control_tower import _act


class HomControlTower(models.AbstractModel):
    _inherit = "hom.control.tower"

    def _alerts(self, company, today):
        out = super()._alerts(company, today)
        env = self.sudo().env
        Claim = env["zc.claim"]
        notice = Claim.search([("company_id", "=", company.id), ("state", "=", "event"),
                               ("notice_deadline", "<=", today + timedelta(days=14))])
        if notice:
            out.append({"level": "danger", "count": len(notice), "amount": sum(notice.mapped("amount_claimed")),
                        "text": _("Claim notices due within 14 days (risk of time-bar)"),
                        "action": _act(_("Claim notices"), "zc.claim", [("id", "in", notice.ids)])})
        late = Claim.search([("company_id", "=", company.id), ("state", "=", "submitted"),
                             ("answer_deadline", "<", today)])
        if late:
            out.append({"level": "warning", "count": len(late), "amount": sum(late.mapped("amount_claimed")),
                        "text": _("Claims without the engineer's answer in the contractual period"),
                        "action": _act(_("Claims waiting"), "zc.claim", [("id", "in", late.ids)])})
        nod = Claim.search([("company_id", "=", company.id), ("state", "in", ("partial", "rejected")),
                            ("nod_deadline", "<=", today + timedelta(days=7))])
        if nod:
            out.append({"level": "danger", "count": len(nod), "amount": 0.0,
                        "text": _("Determinations becoming final: notice of dissatisfaction due"),
                        "action": _act(_("Notice of dissatisfaction"), "zc.claim", [("id", "in", nod.ids)])})
        esc = env["zc.escalation"].search([("company_id", "=", company.id), ("state", "in", ("submitted", "approved"))])
        if esc:
            out.append({"level": "info", "count": len(esc), "amount": sum(esc.mapped("amount")),
                        "text": _("Price escalations waiting for approval / invoicing"),
                        "action": _act(_("Price escalations"), "zc.escalation", [("id", "in", esc.ids)])})
        order = {"danger": 0, "warning": 1, "info": 2}
        out.sort(key=lambda a: order[a["level"]])
        return out
