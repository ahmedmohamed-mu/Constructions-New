from odoo import _, models

from odoo.addons.hom_control_tower.models.control_tower import _act


class HomControlTower(models.AbstractModel):
    _inherit = "hom.control.tower"

    def _alerts(self, company, today):
        out = super()._alerts(company, today)
        env = self.sudo().env
        due = env["sale.order"].search([("company_id", "=", company.id), ("zc_closeout_state", "=", "dlp_over")])
        if due:
            out.append({"level": "danger", "count": len(due), "amount": sum(due.mapped("hom_retention_balance")),
                        "text": _("Defects liability ended: final handover and retention release due"),
                        "action": _act(_("Final handover due"), "sale.order", [("id", "in", due.ids)])})
        dlp = env["sale.order"].search([("company_id", "=", company.id), ("zc_closeout_state", "=", "dlp")])
        short = env["hom.bank.guarantee"].search([("sale_order_id", "in", dlp.ids), ("direction", "=", "issued"),
                                                  ("guarantee_type", "in", ("performance", "retention")),
                                                  ("state", "in", ("active", "extended"))]).filtered(
            lambda g: g.expiry_date and g.sale_order_id.zc_dlp_end and g.expiry_date < g.sale_order_id.zc_dlp_end)
        if short:
            out.append({"level": "warning", "count": len(short), "amount": sum(short.mapped("amount_current")),
                        "text": _("Guarantees expiring before the end of the defects liability period"),
                        "action": _act(_("Guarantees to extend"), "hom.bank.guarantee", [("id", "in", short.ids)])})
        snags = env["zc.handover.snag"].search([("handover_id.company_id", "=", company.id), ("state", "=", "open"),
                                                ("due_date", "<", today)])
        if snags:
            out.append({"level": "warning", "count": len(snags), "amount": 0.0,
                        "text": _("Handover snags past their due date"),
                        "action": _act(_("Open snags"), "zc.handover.snag", [("id", "in", snags.ids)])})
        order = {"danger": 0, "warning": 1, "info": 2}
        out.sort(key=lambda a: order[a["level"]])
        return out
