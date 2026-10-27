from odoo import _, models

from odoo.addons.hom_control_tower.models.control_tower import _act


class HomControlTower(models.AbstractModel):
    _inherit = "hom.control.tower"

    def _alerts(self, company, today):
        out = super()._alerts(company, today)
        env = self.sudo().env
        held = env["hom.progress.certificate"].search([("company_id", "=", company.id), ("state", "=", "submitted"),
                                                       ("zc_consultant_due", "<", today)])
        if held:
            out.append({"level": "danger", "count": len(held), "amount": sum(held.mapped("amount_net")),
                        "text": _("Certificates held at the consultant beyond the contractual period"),
                        "action": _act(_("Certificates at the consultant"), "hom.progress.certificate",
                                       [("id", "in", held.ids)])})
        claims = env["zc.quantity.dispute"].search([("company_id", "=", company.id), ("state", "=", "open")])
        if claims:
            out.append({"level": "warning", "count": len(claims), "amount": sum(claims.mapped("amount")),
                        "text": _("Quantities cut by the consultant - to claim"),
                        "action": _act(_("Quantity claims"), "zc.quantity.dispute", [("id", "in", claims.ids)])})
        wirs = env["zc.inspection.request"].search([("company_id", "=", company.id), ("is_overdue", "=", True)])
        if wirs:
            out.append({"level": "warning", "count": len(wirs), "amount": 0.0,
                        "text": _("Inspection requests (WIR) not answered by the consultant"),
                        "action": _act(_("Late inspections"), "zc.inspection.request", [("id", "in", wirs.ids)])})
        if company.zc_wir_required:
            waiting = env["hom.site.measurement"].search([
                ("company_id", "=", company.id), ("zc_inspection_ok", "=", False), ("state", "=", "open")])
            if waiting:
                value = sum(m.quantity * m.sale_line_id.price_unit for m in waiting)
                out.append({"level": "info", "count": len(waiting), "amount": value,
                            "text": _("Executed work not certifiable yet (no approved inspection)"),
                            "action": _act(_("Work waiting inspection"), "hom.site.measurement",
                                           [("id", "in", waiting.ids)])})
        subs = env["hom.subcontract.certificate"].search([("company_id", "=", company.id), ("state", "=", "submitted"),
                                                          ("zc_review_stage", "in", ("qs", "pm"))])
        if subs:
            out.append({"level": "info", "count": len(subs), "amount": sum(subs.mapped("amount_net")),
                        "text": _("Subcontractor certificates in the approval chain"),
                        "action": _act(_("Subcontractor certificates"), "hom.subcontract.certificate",
                                       [("id", "in", subs.ids)])})
        order = {"danger": 0, "warning": 1, "info": 2}
        out.sort(key=lambda a: order[a["level"]])
        return out
