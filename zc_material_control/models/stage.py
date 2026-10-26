from datetime import timedelta

from odoo import _, api, fields, models
from odoo.tools import float_compare


class HomSiteMeasurement(models.Model):
    _inherit = "hom.site.measurement"

    zc_stage = fields.Selection([("supplied", "Supplied / مورّد"), ("installed", "Installed / مركّب")],
                                string="Stage", default="installed", required=True,
                                help="Supplied: material delivered to the site, not installed yet.\n"
                                     "Installed: supplied and installed (record the installed quantity even if the "
                                     "same units were measured as supplied before).")


class HomProgressCertificate(models.Model):
    _inherit = "hom.progress.certificate"

    zc_stage_valuation = fields.Boolean(related="sale_order_id.zc_stage_valuation")

    def _apply_measurements(self):
        staged = self.filtered(lambda c: c.state == "draft" and c.sale_order_id.zc_stage_valuation)
        res = super(HomProgressCertificate, self - staged)._apply_measurements()
        Measurement = self.env["hom.site.measurement"].sudo()
        for cert in staged:
            domain = [("sale_line_id", "in", cert.line_ids.sale_line_id.ids),
                      "|", ("certificate_id", "=", False), ("certificate_id.state", "=", "cancel")]
            if cert.period_end:
                domain.append(("date", "<=", cert.period_end))
            Measurement.search(domain).write({"certificate_id": cert.id})
            date = cert.period_end or cert.date
            capped = []
            for line in cert.line_ids:
                installed, supplied = line.sale_line_id._zc_measured(date)
                qty = line.sale_line_id._zc_valued_qty(installed, supplied)
                if line.contract_qty and float_compare(qty, line.contract_qty, precision_digits=6) > 0:
                    capped.append(line.product_id.display_name)
                    qty = line.contract_qty
                if float_compare(qty, line.previous_qty, precision_digits=9) > 0:
                    line.cumulative_qty = qty
            if capped:
                cert.message_post(body=_("Valued quantities exceed the contract for: %s. Capped to the contract "
                                         "quantity; issue a variation order for the extra work.", ", ".join(capped)))
        return res

    @api.model
    def _zc_cron_interval_drafts(self):
        """Draft certificate every N days (e.g. every 20 days) from the last certified period."""
        today = fields.Date.context_today(self)
        orders = self.env["sale.order"].search([("state", "=", "sale"), ("hom_project_type", "=", "contracting"),
                                                ("hom_certificate_frequency", "=", "interval")])
        for order in orders:
            last = self.search([("sale_order_id", "=", order.id), ("state", "!=", "cancel")],
                               order="period_end desc, date desc, id desc", limit=1)
            start = last.period_end or last.date or order.date_order.date()
            if today >= start + timedelta(days=order.zc_cert_interval_days or 20):
                self._hom_prepare_draft(order, _("Certificate every %(d)s days - %(o)s",
                                                 d=order.zc_cert_interval_days or 20, o=order.name), period_end=today)


class HomProgressCertificateLine(models.Model):
    _inherit = "hom.progress.certificate.line"

    zc_installed_qty = fields.Float(string="Installed", compute="_compute_zc_stage_qty", digits="Product Unit")
    zc_supplied_only_qty = fields.Float(string="Supplied only", compute="_compute_zc_stage_qty", digits="Product Unit")

    def _compute_zc_stage_qty(self):
        for line in self:
            cert = line.certificate_id
            if not (line.sale_line_id and cert.sale_order_id.zc_stage_valuation):
                line.zc_installed_qty = line.zc_supplied_only_qty = 0.0
                continue
            installed, supplied = line.sale_line_id._zc_measured(cert.period_end or cert.date)
            line.zc_installed_qty = installed
            line.zc_supplied_only_qty = max(supplied - installed, 0.0)


class HomSubcontractCertificate(models.Model):
    _inherit = "hom.subcontract.certificate"

    def _hom_apply_site_progress(self):
        """On stage-valued contracts the owner side is valued (60/75%); the subcontractor is paid on the
        physical installed quantity."""
        res = super()._hom_apply_site_progress()
        for cert in self.filtered(lambda c: c.state == "draft"):
            for line in cert.line_ids:
                boq = line.purchase_line_id.hom_boq_line_id
                if not boq or not boq.order_id.zc_stage_valuation:
                    continue
                installed, _supplied = boq._zc_measured(cert.date)
                qty = min(installed, line.contract_qty) if line.contract_qty else installed
                line.cumulative_qty = max(qty, line.previous_qty)
        return res
