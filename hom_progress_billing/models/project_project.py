from markupsafe import Markup

from odoo import _, api, fields, models


class ProjectProject(models.Model):
    _inherit = "project.project"

    hom_certificate_count = fields.Integer(compute="_compute_hom_certificate_count")
    hom_estimate_to_complete = fields.Monetary(
        string="Estimate to Complete (ETC)", currency_field="currency_id", tracking=True,
        help="Cost still expected to finish the project; used for the expected margin (EAC = actual cost + ETC).")

    def _compute_hom_certificate_count(self):
        data = self.env["hom.progress.certificate"]._read_group(
            [("project_id", "in", self.ids)], ["project_id"], ["__count"])
        counts = {project.id: count for project, count in data}
        for project in self:
            project.hom_certificate_count = counts.get(project.id, 0)

    def action_hom_certificates(self):
        self.ensure_one()
        action = self.env["ir.actions.act_window"]._for_xml_id("hom_progress_billing.action_hom_progress_certificate")
        action["domain"] = [("project_id", "=", self.id)]
        action["context"] = {"default_sale_order_id": self.reinvoiced_sale_order_id.id or self.sale_order_id.id}
        return action

    # ------------------------------------------------------------------
    # Weekly automatic status update
    # ------------------------------------------------------------------
    def _hom_contract_figures(self):
        self.ensure_one()
        order = self.reinvoiced_sale_order_id or self.sale_order_id
        certs = self.env["hom.progress.certificate"].search(
            [("project_id", "=", self.id), ("state", "in", ("approved", "invoiced"))], order="number desc", limit=1)
        account = self.account_id
        actual_cost = 0.0
        if account:
            plan_col = account.plan_id._column_name()
            lines = self.env["account.analytic.line"].sudo().search([(plan_col, "=", account.id), ("amount", "<", 0)])
            actual_cost = -sum(lines.mapped("amount"))
        contract = order.amount_untaxed if order else 0.0
        eac = actual_cost + (self.hom_estimate_to_complete or 0.0)
        margin = ((contract - eac) / contract * 100.0) if contract and self.hom_estimate_to_complete else None
        invoiced = 0.0
        if order:
            retention = order.company_id.hom_retention_owner_product_id
            for move in order.invoice_ids.filtered(lambda m: m.state == "posted"):
                sign = -1 if move.move_type == "out_refund" else 1
                work = move.invoice_line_ids.filtered(
                    lambda l: l.display_type == "product" and not l.is_downpayment and l.product_id != retention)
                invoiced += sign * sum(work.mapped("price_subtotal"))
        return {
            "order": order, "contract": contract, "certified": certs.amount_cumulative if certs else 0.0,
            "progress": certs.progress_pct if certs else 0.0, "invoiced": invoiced,
            "actual_cost": actual_cost, "etc": self.hom_estimate_to_complete or 0.0, "eac": eac, "margin": margin,
        }

    @api.model
    def _cron_weekly_update(self):
        projects = self.search([("account_id", "!=", False)]).filtered(
            lambda p: (p.reinvoiced_sale_order_id or p.sale_order_id).hom_project_type == "contracting")
        for project in projects:
            f = project._hom_contract_figures()
            margin = f["margin"]
            if margin is None:
                status = "on_track"
            elif margin < 5:
                status = "off_track"
            elif margin < 15:
                status = "at_risk"
            else:
                status = "on_track"
            fmt = lambda v: "{:,.0f}".format(v)
            rows = [
                (_("Contract value"), fmt(f["contract"])),
                (_("Certified to date"), fmt(f["certified"])),
                (_("Work invoiced"), fmt(f["invoiced"])),
                (_("Actual cost"), fmt(f["actual_cost"])),
                (_("Estimate to complete"), fmt(f["etc"])),
                (_("Expected margin"), ("%.1f %%" % margin) if margin is not None else "-"),
            ]
            body = Markup("<p>%s</p><table class='table table-sm'>%s</table>") % (
                _("Automatic weekly update (certificates, invoices and analytic costs)."),
                Markup("").join(Markup("<tr><td>%s</td><td class='text-end'>%s</td></tr>") % r for r in rows))
            self.env["project.update"].create({
                "project_id": project.id,
                "name": _("Weekly update %s", fields.Date.context_today(self)),
                "status": status,
                "progress": int(round(f["progress"])),
                "description": body,
                "user_id": project.user_id.id or self.env.user.id,
            })


class ProjectMilestone(models.Model):
    _inherit = "project.milestone"

    def write(self, vals):
        newly_reached = self.filtered(lambda m: not m.is_reached) if vals.get("is_reached") else self.browse()
        res = super().write(vals)
        Certificate = self.env["hom.progress.certificate"]
        for milestone in newly_reached:
            project = milestone.project_id
            order = project.reinvoiced_sale_order_id or project.sale_order_id
            if order and order.state == "sale" and order.hom_project_type == "contracting" \
                    and order.hom_certificate_frequency == "milestone":
                cert = Certificate._hom_prepare_draft(order, _("Milestone reached: %s - certificate", milestone.name))
                if cert:
                    cert.message_post(body=_("Prepared automatically: milestone «%s» reached.", milestone.name))
        return res
