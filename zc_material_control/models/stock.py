from datetime import timedelta

from odoo import _, api, fields, models


class StockPicking(models.Model):
    _inherit = "stock.picking"

    zc_return_deadline = fields.Date(string="Return Deadline", compute="_compute_zc_return_deadline", store=True,
                                     help="Last day the supplier accepts surplus material back.")

    @api.depends("date_done", "state", "picking_type_code", "partner_id.zc_return_days")
    def _compute_zc_return_deadline(self):
        for picking in self:
            if picking.picking_type_code != "incoming" or picking.state != "done" or not picking.date_done:
                picking.zc_return_deadline = False
                continue
            days = picking.partner_id.commercial_partner_id.zc_return_days or picking.company_id.zc_return_days or 14
            picking.zc_return_deadline = picking.date_done.date() + timedelta(days=days)

    @api.model
    def _zc_cron_return_alerts(self):
        """Warn the stores before the return window of a receipt closes while surplus is still on site."""
        today = fields.Date.context_today(self)
        Recon = self.env["zc.material.recon"]
        rows = Recon._zc_compute_rows()
        for company in self.env["res.company"].search([]):
            limit = today + timedelta(days=company.zc_return_alert_days or 5)
            user = company.zc_stores_user_id or company.hom_management_user_id
            if not user:
                continue
            for row in rows:
                if row["company_id"] != company.id or row["surplus_qty"] <= 0 or not row["return_deadline"]:
                    continue
                if today <= row["return_deadline"] <= limit:
                    # write the alert in the stores keeper's language, not the cron user's
                    picking = self.with_context(lang=user.lang or self.env.lang).browse(row["picking_id"])
                    summary = picking.env._("Return surplus before %s", row["return_deadline"])
                    if picking.activity_ids.filtered(lambda a: a.summary == summary):
                        continue
                    picking.activity_schedule(
                        "mail.mail_activity_data_warning", user_id=user.id, date_deadline=row["return_deadline"],
                        summary=summary,
                        note=picking.env._("%(q)s %(u)s of %(p)s are still on site beyond what is installed (value %(v)s). "
                               "Return them to the supplier before the deadline or transfer them to another project.",
                               q=round(row["surplus_qty"], 2), u=row["uom"], p=row["product_name"],
                               v="{:,.0f}".format(row["surplus_value"])))
