from odoo import Command, _, api, fields, models
from odoo.exceptions import UserError


class PurchaseOrder(models.Model):
    _inherit = "purchase.order"

    hom_sub_certificate_ids = fields.One2many("hom.subcontract.certificate", "purchase_order_id")
    hom_sub_certificate_count = fields.Integer(compute="_compute_hom_sub_certificate_count")
    hom_advance_bill_ids = fields.One2many("account.move", "hom_advance_order_id", string="Advance Bills")
    hom_advance_billed = fields.Monetary(compute="_compute_hom_advance", currency_field="currency_id")
    hom_advance_recovered = fields.Monetary(compute="_compute_hom_advance", currency_field="currency_id")
    hom_advance_remaining = fields.Monetary(compute="_compute_hom_advance", currency_field="currency_id")

    def _compute_hom_sub_certificate_count(self):
        for order in self:
            order.hom_sub_certificate_count = len(order.hom_sub_certificate_ids)

    def _compute_hom_advance(self):
        for order in self:
            billed = recovered = 0.0
            adv = order.company_id.hom_sub_advance_product_id
            rec = order.company_id.hom_sub_advance_recovery_product_id
            for move in order.hom_advance_bill_ids.filtered(lambda m: m.state == "posted"):
                billed += sum(move.invoice_line_ids.filtered(lambda l: l.product_id == adv).mapped("price_subtotal"))
            for cert in order.hom_sub_certificate_ids:
                bill = cert.bill_id
                if bill and bill.state == "posted":
                    recovered -= sum(bill.invoice_line_ids.filtered(lambda l: l.product_id == rec).mapped("price_subtotal"))
            order.hom_advance_billed = billed
            order.hom_advance_recovered = recovered
            order.hom_advance_remaining = billed - recovered

    def action_hom_advance_bill(self):
        self.ensure_one()
        product = self.company_id.hom_sub_advance_product_id
        if not product:
            raise UserError(_("Set the subcontractor advance product in Accounting settings (Contracting)."))
        if not self.hom_advance_amount:
            raise UserError(_("Set the advance amount on the subcontract first."))
        if self.hom_advance_bill_ids.filtered(lambda m: m.state != "cancel"):
            raise UserError(_("An advance bill already exists for this subcontract."))
        vals = self.with_context(default_move_type="in_invoice")._prepare_invoice()
        vals.update({
            "ref": self.partner_ref or self.name,
            "hom_advance_order_id": self.id,
            "invoice_line_ids": [Command.create({
                "product_id": product.id,
                "name": _("Advance payment - %s", self.name),
                "quantity": 1,
                "price_unit": self.hom_advance_amount,
                "tax_ids": [Command.set(product.supplier_taxes_id.filtered(lambda t: t.company_id == self.company_id).ids)],
            })],
        })
        move = self.env["account.move"].create(vals)
        return move._get_records_action()

    def action_hom_sub_certificates(self):
        self.ensure_one()
        action = self.env["ir.actions.act_window"]._for_xml_id("hom_subcontract_billing.action_hom_subcontract_certificate")
        action["domain"] = [("purchase_order_id", "=", self.id)]
        action["context"] = {"default_purchase_order_id": self.id}
        return action


class AccountMove(models.Model):
    _inherit = "account.move"

    hom_advance_order_id = fields.Many2one("purchase.order", string="Advance of Subcontract", readonly=True,
                                           copy=False, index="btree_not_null")
    hom_sub_certificate_id = fields.Many2one("hom.subcontract.certificate", string="Subcontractor Certificate",
                                             readonly=True, copy=False, index="btree_not_null")


class PurchaseOrderLine(models.Model):
    _inherit = "purchase.order.line"

    hom_boq_line_id = fields.Many2one(
        "sale.order.line", string="Owner BOQ Item", compute="_compute_hom_boq_line_id", store=True, readonly=False,
        help="Owner contract item executed by this subcontract line: its site measurements fill the "
             "subcontractor certificate automatically (matched on the item code, e.g. SC-HV-04 -> HV-04).")

    @api.depends("product_id", "order_id.hom_is_subcontract")
    def _compute_hom_boq_line_id(self):
        SOL = self.env["sale.order.line"].sudo()
        for line in self:
            code = line.product_id.default_code or ""
            if line.hom_boq_line_id or not line.order_id.hom_is_subcontract or not code:
                line.hom_boq_line_id = line.hom_boq_line_id
                continue
            base = code[3:] if code.upper().startswith("SC-") else code
            candidates = SOL.search([("product_id.default_code", "=", base), ("order_id.state", "=", "sale"),
                                     ("order_id.hom_project_type", "=", "contracting")])
            analytic_ids = set()
            for key in (line.analytic_distribution or {}):
                analytic_ids.update(int(x) for x in str(key).split(",") if x.isdigit())
            if analytic_ids:
                preferred = candidates.filtered(
                    lambda s: "project_ids" in s.order_id._fields and s.order_id.project_ids[:1].account_id.id in analytic_ids)
                candidates = preferred or candidates
            line.hom_boq_line_id = candidates[:1]
