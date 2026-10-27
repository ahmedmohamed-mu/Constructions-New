from odoo import Command, _, api, fields, models
from odoo.exceptions import UserError


class PurchaseOrder(models.Model):
    _inherit = "purchase.order"

    zc_deduction_type_ids = fields.Many2many(
        "zc.deduction.type", "zc_purchase_order_deduction_rel", string="Subcontractor deductions",
        domain="[('scope', '=', 'subcontract'), ('company_id', '=', company_id)]",
        help="Applied on every certificate of this subcontract (e.g. withholding tax).")


class HomSubcontractCertificate(models.Model):
    _inherit = ["hom.subcontract.certificate", "zc.deduction.mixin"]
    _name = "hom.subcontract.certificate"

    zc_deduction_ids = fields.One2many("zc.certificate.deduction", "sub_cert_id", string="Deductions", copy=False)
    zc_amount_deduct_invoice = fields.Monetary(string="Back-charges & other deductions",
                                               compute="_compute_zc_amounts", store=True)
    zc_amount_withholding = fields.Monetary(string="Withheld at payment", compute="_compute_zc_amounts", store=True)
    zc_amount_payable = fields.Monetary(string="Net certificate (before VAT)", compute="_compute_zc_amounts",
                                        store=True)
    zc_withholding_open = fields.Boolean(compute="_compute_zc_withholding_open")
    zc_review_stage = fields.Selection([
        ("site", "Site engineer"),
        ("qs", "Technical office (QS)"),
        ("pm", "Project manager"),
        ("done", "Approved"),
    ], string="Approval step", default="site", copy=False, tracking=True)
    zc_qs_user_id = fields.Many2one("res.users", string="Checked by (QS)", readonly=True, copy=False)
    zc_qs_date = fields.Date(string="Checked on", readonly=True, copy=False)
    zc_pm_user_id = fields.Many2one("res.users", string="Approved by (PM)", readonly=True, copy=False)
    zc_multi_approval = fields.Boolean(related="company_id.zc_sub_multi_approval")

    @api.depends("zc_deduction_ids.amount", "zc_deduction_ids.timing", "amount_net")
    def _compute_zc_amounts(self):
        for cert in self:
            inv = sum(cert.zc_deduction_ids.filtered(lambda d: d.timing == "invoice").mapped("amount"))
            wh = sum(cert.zc_deduction_ids.filtered(lambda d: d.timing == "payment").mapped("amount"))
            cert.zc_amount_deduct_invoice = -inv
            cert.zc_amount_withholding = -wh
            cert.zc_amount_payable = cert.amount_net - inv

    def _compute_zc_withholding_open(self):
        for cert in self:
            bill = cert.bill_id
            cert.zc_withholding_open = bool(bill and bill.state == "posted" and cert.zc_deduction_ids.filtered(
                lambda d: d.timing == "payment" and d.amount and not d.settled_move_id))

    # ------------------------------------------------------------------
    def _hom_apply_site_progress(self):
        gated = self.filtered(lambda c: c.state == "draft" and c.company_id.zc_wir_required)
        res = super(HomSubcontractCertificate, self - gated)._hom_apply_site_progress()
        if gated:
            super(HomSubcontractCertificate, gated.with_context(zc_wir_gate=True))._hom_apply_site_progress()
        return res

    def action_load_items(self):
        res = super().action_load_items()
        for cert in self:
            cert._zc_add_contract_deductions(cert.purchase_order_id.zc_deduction_type_ids)
        return res

    def action_zc_load_materials(self):
        """Charge the materials issued from our stores to this subcontractor (not charged yet)."""
        for cert in self:
            if cert.state not in ("draft", "submitted"):
                raise UserError(_("Materials are charged on a draft or submitted certificate."))
            pickings = self.env["stock.picking"].search([
                ("zc_charge_partner_id", "=", cert.partner_id.id), ("state", "=", "done"),
                ("zc_charge_deduction_id", "=", False), ("company_id", "=", cert.company_id.id)])
            if not pickings:
                raise UserError(_("No material issued to %s is waiting to be charged.", cert.partner_id.name))
            dtype = self.env["zc.deduction.type"].search([
                ("scope", "=", "subcontract"), ("code", "=", "MAT"), ("company_id", "=", cert.company_id.id)], limit=1)
            if not dtype:
                raise UserError(_("Create a subcontractor deduction type with code MAT (materials issued)."))
            amount = sum(pickings.mapped("zc_charge_amount"))
            ded = self.env["zc.certificate.deduction"].create({
                "sub_cert_id": cert.id, "type_id": dtype.id, "amount": cert.currency_id.round(amount),
                "reference": ", ".join(pickings.mapped("name")), "sequence": dtype.sequence,
            })
            pickings.write({"zc_charge_deduction_id": ded.id})
            cert.message_post(body=_("Materials issued from our stores charged: %(a)s (%(p)s).",
                                     a=cert.currency_id.format(amount), p=ded.reference))

    def action_submit(self):
        drafts = self.filtered(lambda c: c.state == "draft")
        res = super().action_submit()
        for cert in drafts.filtered(lambda c: c.state == "submitted"):
            if not cert.zc_multi_approval:
                continue
            cert.zc_review_stage = "qs"
            qs = cert.company_id.zc_qs_user_id
            if qs:
                cert.activity_schedule("mail.mail_activity_data_todo", user_id=qs.id,
                                       summary=_("Check quantities and deductions: %s", cert.name),
                                       note=_("Technical office check of the subcontractor certificate "
                                              "(quantities against the site measurements, back-charges, "
                                              "materials issued)."))
        return res

    def action_zc_qs_check(self):
        for cert in self:
            if cert.state != "submitted" or cert.zc_review_stage != "qs":
                raise UserError(_("%s is not waiting for the technical office.", cert.name))
            cert.write({"zc_review_stage": "pm", "zc_qs_user_id": self.env.user.id,
                        "zc_qs_date": fields.Date.context_today(self)})
            cert.activity_feedback(["mail.mail_activity_data_todo"])
            pm = cert.project_id.user_id or cert.purchase_order_id.user_id
            if pm:
                cert.activity_schedule("mail.mail_activity_data_todo", user_id=pm.id,
                                       summary=_("Approve subcontractor certificate %s", cert.name),
                                       note=_("Checked by the technical office (%s).", self.env.user.name))

    def action_approve(self):
        for cert in self.filtered(lambda c: c.state == "submitted" and c.zc_multi_approval):
            if cert.zc_review_stage == "qs":
                raise UserError(_("%s must be checked by the technical office (QS) first.", cert.name))
        to_do = self.filtered(lambda c: c.state == "submitted")
        res = super().action_approve()
        for cert in to_do.filtered(lambda c: c.state in ("approved", "billed")):
            cert.write({"zc_review_stage": "done", "zc_pm_user_id": self.env.user.id})
        return res

    def action_reset_draft(self):
        res = super().action_reset_draft()
        self.filtered(lambda c: c.state == "draft").write({"zc_review_stage": "site"})
        return res

    def _prepare_bill_lines(self, move_type="in_invoice"):
        cmds = super()._prepare_bill_lines(move_type=move_type)
        analytic = self.project_id.account_id
        for seq, d in enumerate(self._zc_deduction_lines("invoice"), start=960):
            vals = {"name": "%s - %s%s" % (d._label(), self.name, (" (%s)" % d.reference) if d.reference else ""),
                    "account_id": d.type_id.account_id.id, "quantity": 1, "price_unit": -d.amount,
                    "tax_ids": [Command.clear()], "sequence": seq}
            if d.type_id.analytic and analytic:
                vals["analytic_distribution"] = {str(analytic.id): 100}
            cmds.append(Command.create(vals))
        return cmds

    def action_zc_settle_withholdings(self):
        self.ensure_one()
        return self._zc_settle(self.bill_id, self.partner_id.commercial_partner_id, -1)._get_records_action()
