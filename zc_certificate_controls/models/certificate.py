from datetime import timedelta

from odoo import Command, _, api, fields, models
from odoo.exceptions import UserError
from odoo.tools import float_compare, float_is_zero


class SaleOrder(models.Model):
    _inherit = "sale.order"

    zc_deduction_type_ids = fields.Many2many(
        "zc.deduction.type", "zc_sale_order_deduction_rel", string="Owner deductions",
        domain="[('scope', '=', 'owner'), ('company_id', '=', company_id)]",
        help="Applied on every certificate of this contract (e.g. withholding tax 1%, social insurance 5% for "
             "government owners).")


class HomProgressCertificate(models.Model):
    _inherit = ["hom.progress.certificate", "zc.deduction.mixin"]
    _name = "hom.progress.certificate"

    zc_deduction_ids = fields.One2many("zc.certificate.deduction", "owner_cert_id", string="Deductions", copy=False)
    zc_amount_deduct_invoice = fields.Monetary(string="Other deductions", compute="_compute_zc_amounts", store=True)
    zc_amount_withholding = fields.Monetary(string="Withheld by the owner", compute="_compute_zc_amounts", store=True)
    zc_amount_payable = fields.Monetary(string="Net certificate (before VAT)", compute="_compute_zc_amounts",
                                        store=True)
    zc_expected_cash = fields.Monetary(string="Expected cash", compute="_compute_zc_cash",
                                       help="Invoice total (with VAT) less what the owner withholds at payment.")
    zc_withholding_open = fields.Boolean(compute="_compute_zc_cash")
    # consultant review
    zc_submitted_date = fields.Date(string="Submitted on", readonly=True, copy=False, tracking=True)
    zc_consultant_due = fields.Date(string="Consultant due", compute="_compute_zc_consultant_due", store=True)
    zc_approved_date = fields.Date(string="Certified on", readonly=True, copy=False)
    zc_days_at_consultant = fields.Integer(string="Days at consultant", compute="_compute_zc_days")
    zc_consultant_ref = fields.Char(string="Consultant approval ref.", tracking=True)
    zc_amount_submitted = fields.Monetary(string="Submitted", compute="_compute_zc_review", store=True)
    zc_amount_cut = fields.Monetary(string="Cut by consultant", compute="_compute_zc_review", store=True)
    zc_dispute_ids = fields.One2many("zc.quantity.dispute", "certificate_id", string="Quantity claims")
    zc_wir_blocked = fields.Integer(string="Work waiting inspection", compute="_compute_zc_wir_blocked")

    @api.depends("zc_deduction_ids.amount", "zc_deduction_ids.timing", "amount_net")
    def _compute_zc_amounts(self):
        for cert in self:
            inv = sum(cert.zc_deduction_ids.filtered(lambda d: d.timing == "invoice").mapped("amount"))
            wh = sum(cert.zc_deduction_ids.filtered(lambda d: d.timing == "payment").mapped("amount"))
            cert.zc_amount_deduct_invoice = -inv
            cert.zc_amount_withholding = -wh
            cert.zc_amount_payable = cert.amount_net - inv

    def _compute_zc_cash(self):
        for cert in self:
            inv = cert.invoice_id
            cert.zc_expected_cash = (inv.amount_total + cert.zc_amount_withholding) if inv else 0.0
            cert.zc_withholding_open = bool(inv and inv.state == "posted" and cert.zc_deduction_ids.filtered(
                lambda d: d.timing == "payment" and d.amount and not d.settled_move_id))

    @api.depends("zc_submitted_date", "company_id.zc_consultant_review_days")
    def _compute_zc_consultant_due(self):
        for cert in self:
            cert.zc_consultant_due = cert.zc_submitted_date and (
                cert.zc_submitted_date + timedelta(days=cert.company_id.zc_consultant_review_days or 14))

    def _compute_zc_days(self):
        today = fields.Date.context_today(self)
        for cert in self:
            end = cert.zc_approved_date or today
            cert.zc_days_at_consultant = (end - cert.zc_submitted_date).days if cert.zc_submitted_date else 0

    @api.depends("line_ids.zc_submitted_qty", "line_ids.zc_cut_amount", "state")
    def _compute_zc_review(self):
        for cert in self:
            cert.zc_amount_submitted = sum(cert.line_ids.mapped("zc_submitted_amount"))
            cert.zc_amount_cut = sum(cert.line_ids.mapped("zc_cut_amount"))

    def _compute_zc_wir_blocked(self):
        Measurement = self.env["hom.site.measurement"].sudo()
        for cert in self:
            if not cert.company_id.zc_wir_required or cert.state != "draft":
                cert.zc_wir_blocked = 0
                continue
            cert.zc_wir_blocked = Measurement.search_count([
                ("sale_order_id", "=", cert.sale_order_id.id), ("zc_inspection_ok", "=", False),
                "|", ("certificate_id", "=", False), ("certificate_id.state", "=", "cancel")])

    # ------------------------------------------------------------------
    def _zc_gate(self):
        return self.company_id.zc_wir_required

    def _apply_measurements(self):
        self._zc_unclaim_disputes()
        gated = self.filtered(lambda c: c.state == "draft" and c._zc_gate())
        res = super(HomProgressCertificate, self - gated)._apply_measurements()
        if gated:
            super(HomProgressCertificate, gated.with_context(zc_wir_gate=True))._apply_measurements()
            for cert in gated:
                if cert.zc_wir_blocked:
                    cert.message_post(body=_(
                        "%s site measurement(s) are not certified yet: their work inspection request (WIR) is "
                        "not approved by the consultant.", cert.zc_wir_blocked))
        self._zc_resubmit_disputes()
        return res

    def _zc_unclaim_disputes(self):
        """Pulling again starts from the measurements: claimed quantities are added back afterwards."""
        Dispute = self.env["zc.quantity.dispute"].sudo()
        for cert in self.filtered(lambda c: c.state == "draft"):
            for line in cert.line_ids.filtered("zc_claimed_qty"):
                line.cumulative_qty = max(line.cumulative_qty - line.zc_claimed_qty, line.previous_qty)
                line.zc_claimed_qty = 0.0
            Dispute.search([("resubmit_cert_id", "=", cert.id), ("state", "=", "resubmitted")]).write(
                {"state": "open", "resubmit_cert_id": False})

    def _zc_resubmit_disputes(self):
        """Quantities the consultant cut before come back in the next certificate."""
        Dispute = self.env["zc.quantity.dispute"].sudo()
        for cert in self.filtered(lambda c: c.state == "draft"):
            staged = cert.sale_order_id.zc_stage_valuation
            resubmitted = []
            for line in cert.line_ids:
                disputes = Dispute.search([("sale_line_id", "=", line.sale_line_id.id), ("state", "=", "open"),
                                           ("certificate_id", "!=", cert.id)])
                if not disputes:
                    continue
                if not staged:
                    # stage-valued contracts re-measure everything to date, the cut quantity is already back
                    qty = line.cumulative_qty + sum(disputes.mapped("qty"))
                    if line.contract_qty:
                        qty = min(qty, line.contract_qty)
                    old = line.cumulative_qty
                    line.cumulative_qty = max(qty, line.previous_qty)
                    line.zc_claimed_qty += line.cumulative_qty - old
                disputes.write({"state": "resubmitted", "resubmit_cert_id": cert.id})
                resubmitted.append("%s: %g" % (line.product_id.default_code or line.product_id.name,
                                               sum(disputes.mapped("qty"))))
            if resubmitted:
                cert.message_post(body=_("Quantities cut by the consultant before are claimed again: %s",
                                         ", ".join(resubmitted)))

    def action_load_boq(self):
        res = super().action_load_boq()
        for cert in self:
            cert._zc_add_contract_deductions(cert.sale_order_id.zc_deduction_type_ids)
        return res

    def action_submit(self):
        drafts = self.filtered(lambda c: c.state == "draft")
        res = super().action_submit()
        for cert in drafts.filtered(lambda c: c.state == "submitted"):
            for line in cert.line_ids:
                line.zc_submitted_qty = line.cumulative_qty
            cert.zc_submitted_date = fields.Date.context_today(self)
        return res

    def action_approve(self):
        to_do = self.filtered(lambda c: c.state == "submitted")
        res = super().action_approve()
        for cert in to_do.filtered(lambda c: c.state in ("approved", "invoiced")):
            cert.zc_approved_date = fields.Date.context_today(self)
            cert._zc_record_cuts()
        return res

    def _zc_record_cuts(self):
        self.ensure_one()
        Dispute = self.env["zc.quantity.dispute"].sudo()
        cuts = []
        for line in self.line_ids:
            cut = line.zc_cut_qty
            old = Dispute.search([("resubmit_cert_id", "=", self.id), ("sale_line_id", "=", line.sale_line_id.id),
                                  ("state", "=", "resubmitted")], order="id")
            remaining_cut = max(cut, 0.0)
            for d in reversed(old):  # the latest claimed quantities are the first to be cut again
                not_recovered = min(d.qty, remaining_cut)
                remaining_cut -= not_recovered
                d.write({"state": "closed", "recovered_qty": d.qty - not_recovered})
            if float_compare(cut, 0.0, precision_digits=6) > 0:
                cuts.append({"certificate_id": self.id, "sale_line_id": line.sale_line_id.id, "qty": cut,
                             "price_unit": line.zc_net_price, "reason": line.zc_cut_reason})
        if cuts:
            claims = Dispute.create(cuts)
            self.message_post(body=_("The consultant certified %(c)s less than submitted; %(n)s quantity claim(s) "
                                     "opened and carried to the next certificate.",
                                     c=self.currency_id.format(self.zc_amount_cut), n=len(claims)))

    def action_reset_draft(self):
        res = super().action_reset_draft()
        for cert in self.filtered(lambda c: c.state == "draft"):
            cert.zc_dispute_ids.filtered(lambda d: d.state == "open").unlink()
            self.env["zc.quantity.dispute"].sudo().search([("resubmit_cert_id", "=", cert.id),
                                                           ("state", "=", "closed")]).write(
                {"state": "resubmitted", "recovered_qty": 0.0})
            cert.zc_approved_date = False
            cert.line_ids.write({"zc_submitted_qty": 0.0})
        return res

    def action_cancel(self):
        res = super().action_cancel()
        for cert in self:
            self.env["zc.quantity.dispute"].sudo().search([("resubmit_cert_id", "=", cert.id)]).write(
                {"state": "open", "resubmit_cert_id": False, "recovered_qty": 0.0})
            cert.zc_dispute_ids.unlink()
        return res

    def _prepare_invoice_lines(self):
        cmds = super()._prepare_invoice_lines()
        analytic = self.project_id.account_id
        for seq, d in enumerate(self._zc_deduction_lines("invoice"), start=960):
            vals = {"name": "%s - %s" % (d._label(), self.name), "account_id": d.type_id.account_id.id,
                    "quantity": 1, "price_unit": -d.amount, "tax_ids": [Command.clear()], "sequence": seq}
            if d.type_id.analytic and analytic:
                vals["analytic_distribution"] = {str(analytic.id): 100}
            cmds.append(Command.create(vals))
        return cmds

    def action_zc_settle_withholdings(self):
        self.ensure_one()
        return self._zc_settle(self.invoice_id, self.partner_id.commercial_partner_id, 1)._get_records_action()


class HomProgressCertificateLine(models.Model):
    _inherit = "hom.progress.certificate.line"

    zc_submitted_qty = fields.Float(string="Submitted Qty", digits="Product Unit", copy=False,
                                    help="Cumulative quantity submitted to the consultant.")
    zc_net_price = fields.Float(compute="_compute_zc_net_price", digits="Product Price")
    zc_submitted_amount = fields.Monetary(string="Submitted", compute="_compute_zc_cut", store=True)
    zc_cut_qty = fields.Float(string="Cut Qty", compute="_compute_zc_cut", store=True, digits="Product Unit")
    zc_cut_amount = fields.Monetary(string="Cut", compute="_compute_zc_cut", store=True)
    zc_cut_reason = fields.Char(string="Consultant's reason")
    zc_claimed_qty = fields.Float(string="Claimed again", digits="Product Unit", copy=False,
                                  help="Quantity cut by the consultant in an earlier certificate, claimed again here.")

    @api.depends("price_unit", "sale_line_id.discount")
    def _compute_zc_net_price(self):
        for line in self:
            line.zc_net_price = line.price_unit * (1 - (line.sale_line_id.discount or 0.0) / 100.0)

    @api.depends("zc_submitted_qty", "cumulative_qty", "previous_qty", "price_unit", "sale_line_id.discount")
    def _compute_zc_cut(self):
        for line in self:
            price = line.price_unit * (1 - (line.sale_line_id.discount or 0.0) / 100.0)
            submitted = line.zc_submitted_qty or line.cumulative_qty
            line.zc_submitted_amount = (submitted - line.previous_qty) * price
            cut = submitted - line.cumulative_qty if line.zc_submitted_qty else 0.0
            line.zc_cut_qty = cut if not float_is_zero(cut, precision_digits=6) else 0.0
            line.zc_cut_amount = line.zc_cut_qty * price

    @api.constrains("cumulative_qty", "zc_submitted_qty")
    def _check_zc_review(self):
        for line in self:
            if line.zc_submitted_qty and float_compare(line.cumulative_qty, line.zc_submitted_qty,
                                                       precision_digits=6) > 0:
                raise UserError(_("The consultant cannot certify more than submitted for %s.",
                                  line.product_id.display_name))
