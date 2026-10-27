from odoo import _, models

from odoo.addons.hom_control_tower.models.control_tower import _act


class HomControlTower(models.AbstractModel):
    _inherit = "hom.control.tower"

    def _alerts(self, company, today):
        out = super()._alerts(company, today)
        env = self.sudo().env
        rows = [r for r in env["zc.evm.report"]._zc_compute(day=today) if r["company_id"] == company.id]
        action = "zc_project_controls.action_zc_evm_server"
        late = [r for r in rows if r["pv"] and r["spi"] < 0.9]
        over = [r for r in rows if r["ac"] and r["ev"] and r["cpi"] < 0.9]
        if late:
            out.append({"level": "danger", "count": len(late), "amount": sum(r["pv"] - r["ev"] for r in late),
                        "text": _("Projects behind the approved baseline (SPI below 0.9)"), "action": action})
        if over:
            out.append({"level": "warning", "count": len(over), "amount": sum(r["ac"] - r["ev"] for r in over),
                        "text": _("Projects spending more than the work earned (CPI below 0.9)"), "action": action})
        risk = env["hom.variation.order"].search([("zc_provisional", "=", True), ("company_id", "=", company.id)])
        if risk:
            out.append({"level": "warning", "count": len(risk), "amount": sum(risk.mapped("amount_total")),
                        "text": _("Variation work executed before the rate is agreed"),
                        "action": _act(_("Variations at risk"), "hom.variation.order", [("id", "in", risk.ids)])})
        sheets = env["zc.labour.sheet"].search([("state", "=", "submitted"), ("company_id", "=", company.id)])
        if sheets:
            out.append({"level": "info", "count": len(sheets), "amount": sum(sheets.mapped("amount_total")),
                        "text": _("Daily labour sheets waiting for the project manager"),
                        "action": _act(_("Labour sheets"), "zc.labour.sheet", [("id", "in", sheets.ids)])})
        order = {"danger": 0, "warning": 1, "info": 2}
        out.sort(key=lambda a: order[a["level"]])
        return out
