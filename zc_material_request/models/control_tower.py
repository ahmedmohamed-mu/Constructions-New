from datetime import timedelta

from odoo import _, models

from odoo.addons.hom_control_tower.models.control_tower import _act


class HomControlTower(models.AbstractModel):
    _inherit = "hom.control.tower"

    def _alerts(self, company, today):
        out = super()._alerts(company, today)
        env = self.sudo().env
        late = env["zc.material.request"].search([("company_id", "=", company.id),
                                                  ("state", "in", ("submitted", "approved")),
                                                  ("date_needed", "<", today)])
        if late:
            out.append({"level": "danger", "count": len(late), "amount": 0.0,
                        "text": _("Material requests not served by the date needed on site"),
                        "action": _act(_("Late material requests"), "zc.material.request", [("id", "in", late.ids)])})
        qc = env["stock.picking"].search([("company_id", "=", company.id), ("zc_qc_state", "=", "pending"),
                                          ("date_done", "<", today - timedelta(days=2))])
        if qc:
            out.append({"level": "warning", "count": len(qc), "amount": 0.0,
                        "text": _("Receipts waiting for technical acceptance more than 2 days"),
                        "action": _act(_("Receipts to inspect"), "stock.picking", [("id", "in", qc.ids)])})
        order = {"danger": 0, "warning": 1, "info": 2}
        out.sort(key=lambda a: order[a["level"]])
        return out


class HomControlTowerConsumption(models.AbstractModel):
    _inherit = "hom.control.tower"

    def _alerts(self, company, today):
        out = super()._alerts(company, today)
        env = self.sudo().env
        meas = env["hom.site.measurement"].search([("company_id", "=", company.id), ("zc_issue_move_ids", "=", False),
                                                   ("project_id.zc_warehouse_id", "!=", False)])
        todo = env["hom.site.measurement"]
        value = 0.0
        for m in meas:
            needs = m._zc_material_needs()
            if needs:
                todo |= m
                value += sum(p.standard_price * q for p, q in needs)
        if todo:
            out.append({"level": "warning", "count": len(todo), "amount": value,
                        "text": _("Work installed on site without a material issue from the store"),
                        "action": _act(_("Materials to issue"), "hom.site.measurement", [("id", "in", todo.ids)])})
            order = {"danger": 0, "warning": 1, "info": 2}
            out.sort(key=lambda a: order[a["level"]])
        return out
