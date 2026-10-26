from odoo import _, fields, models

from odoo.addons.hom_control_tower.models.control_tower import _act


class HomControlTower(models.AbstractModel):
    _inherit = "hom.control.tower"

    def _alerts(self, company, today):
        out = super()._alerts(company, today)
        env = self.sudo().env
        rows = env["zc.material.recon"]._zc_compute_rows()
        rows = [r for r in rows if r["company_id"] == company.id]
        ret = [r for r in rows if r["recommendation"] == "return"]
        idle = [r for r in rows if r["recommendation"] in ("transfer", "hold")]
        action = "zc_material_control.action_zc_material_recon_server"
        if ret:
            out.insert(0, {"level": "danger", "count": len(ret), "amount": sum(r["surplus_value"] for r in ret),
                           "text": _("Surplus material to return before the supplier deadline"), "action": action})
        if idle:
            out.insert(1 if ret else 0, {"level": "warning", "count": len(idle),
                                         "amount": sum(r["surplus_value"] for r in idle),
                                         "text": _("Idle material on site past the return window"), "action": action})
        P = env["purchase.order"].search([("zc_exception_state", "=", "requested")])
        if P:
            out.insert(0, {"level": "warning", "count": len(P), "amount": sum(P.mapped("amount_untaxed")),
                           "text": _("Purchases above shop drawing / item budget waiting for the PM"),
                           "action": _act(_("Purchase exceptions"), "purchase.order", [("id", "in", P.ids)])})
        return out
