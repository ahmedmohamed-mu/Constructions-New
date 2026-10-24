from odoo import Command, _, api, fields, models
from odoo.exceptions import UserError, ValidationError
from odoo.tools import float_compare, float_is_zero


class HomSubcontractCertificate(models.Model):
    _name = "hom.subcontract.certificate"
    _description = "Subcontractor Progress Certificate"
    _inherit = ["mail.thread", "mail.activity.mixin"]
    _order = "purchase_order_id, number desc, id desc"
    _check_company_auto = True

    name = fields.Char(default=lambda self: _("New"), readonly=True, copy=False, tracking=True)
    number = fields.Integer(string="Certificate No.", readonly=True, copy=False)
    state = fields.Selection([
        ("draft", "Draft"),
        ("submitted", "Submitted"),
        ("approved", "Approved"),
        ("billed", "Billed"),
        ("cancel", "Cancelled"),
    ], default="draft", required=True, tracking=True, copy=False)
    purchase_order_id = fields.Many2one(
        "purchase.order", string="Subcontract", required=True, tracking=True, check_company=True,
        domain="[('hom_is_subcontract', '=', True), ('state', '=', 'purchase')]")
    company_id = fields.Many2one(related="purchase_order_id.company_id", store=True)
    currency_id = fields.Many2one(related="purchase_order_id.currency_id", store=True)
    partner_id = fields.Many2one(related="purchase_order_id.partner_id", store=True, string="Subcontractor")
    user_id = fields.Many2one("res.users", string="Site Engineer", default=lambda self: self.env.user, tracking=True)
    date = fields.Date(default=fields.Date.context_today, required=True)
    line_ids = fields.One2many("hom.subcontract.certificate.line", "certificate_id", copy=True)
    bill_id = fields.Many2one("account.move", readonly=True, copy=False, tracking=True)
    bill_payment_state = fields.Selection(related="bill_id.payment_state")
    recovery_pct = fields.Float(related="purchase_order_id.hom_advance_recovery_pct")
    retention_pct = fields.Float(related="purchase_order_id.hom_retention_pct")
    amount_current = fields.Monetary(string="Work this Certificate", compute="_compute_amounts", store=True)
    amount_cumulative = fields.Monetary(string="Cumulative Work", compute="_compute_amounts", store=True)
    amount_advance_recovery = fields.Monetary(string="Advance Recovery", compute="_compute_amounts", store=True)
    amount_retention = fields.Monetary(string="Retention", compute="_compute_amounts", store=True)
    amount_net = fields.Monetary(string="Net (before VAT)", compute="_compute_amounts", store=True)

    @api.depends("line_ids.amount_current", "line_ids.amount_cumulative", "recovery_pct", "retention_pct")
    def _compute_amounts(self):
        for cert in self:
            current = sum(cert.line_ids.mapped("amount_current"))
            cert.amount_current = current
            cert.amount_cumulative = sum(cert.line_ids.mapped("amount_cumulative"))
            remaining = cert.purchase_order_id.hom_advance_remaining if cert.state != "billed" else float("inf")
            recovery = min(current * cert.recovery_pct / 100.0, max(remaining, 0.0))
            cert.amount_advance_recovery = -cert.currency_id.round(recovery) if cert.currency_id else 0.0
            cert.amount_retention = -cert.currency_id.round(current * cert.retention_pct / 100.0) if cert.currency_id else 0.0
            cert.amount_net = current + cert.amount_advance_recovery + cert.amount_retention

    def action_load_items(self):
        Line = self.env["hom.subcontract.certificate.line"]
        for cert in self:
            if cert.state != "draft":
                raise UserError(_("Items can only be loaded on a draft certificate."))
            existing = cert.line_ids.purchase_line_id
            lines = cert.purchase_order_id.order_line.filtered(
                lambda l: not l.display_type and l.product_id and l not in existing)
            cert.line_ids = [Command.create({
                "purchase_line_id": l.id, "cumulative_qty": Line._previous_for(l, Line)}) for l in lines]

    def action_submit(self):
        for cert in self.filtered(lambda c: c.state == "draft"):
            if not cert.line_ids.filtered(lambda l: not float_is_zero(l.current_qty, precision_digits=6)):
                raise UserError(_("Nothing measured in this certificate."))
            if not cert.number:
                last = self.search([("purchase_order_id", "=", cert.purchase_order_id.id), ("number", ">", 0)],
                                   order="number desc", limit=1)
                cert.number = (last.number or 0) + 1
                cert.name = _("%(po)s - Sub. Certificate %(n)s", po=cert.purchase_order_id.name, n=cert.number)
            cert.line_ids._compute_previous_qty()
            cert.state = "submitted"

    def action_approve(self):
        for cert in self.filtered(lambda c: c.state == "submitted"):
            for line in cert.line_ids:
                pol = line.purchase_line_id
                if pol.qty_received_method == "manual":
                    pol.qty_received = line.cumulative_qty
            cert.state = "approved"
            buyer = cert.purchase_order_id.user_id
            if buyer:
                cert.activity_schedule("mail.mail_activity_data_todo", user_id=buyer.id,
                                       summary=_("Bill subcontractor certificate %s", cert.name))

    def action_reset_draft(self):
        for cert in self:
            if cert.bill_id and cert.bill_id.state != "cancel":
                raise UserError(_("Cancel the bill before resetting the certificate."))
            cert.state = "draft"

    def action_cancel(self):
        for cert in self:
            if cert.bill_id and cert.bill_id.state == "posted":
                raise UserError(_("The bill of this certificate is posted."))
            cert.state = "cancel"

    def _prepare_bill_lines(self, move_type="in_invoice"):
        self.ensure_one()
        company = self.company_id
        cmds = []
        for line in self.line_ids.filtered(lambda l: not float_is_zero(l.current_qty, precision_digits=6)):
            pol = line.purchase_line_id
            vals = pol._prepare_account_move_line()
            vals.update({"quantity": line.current_qty, "hom_previous_qty": line.previous_qty,
                         "hom_cumulative_qty": line.cumulative_qty})
            if pol.analytic_distribution:
                vals["analytic_distribution"] = pol.analytic_distribution
            cmds.append(Command.create(vals))
        if self.amount_advance_recovery:
            product = company.hom_sub_advance_recovery_product_id
            if not product:
                raise UserError(_("Set the advance recovery product in Accounting settings (Contracting)."))
            cmds.append(Command.create({
                "product_id": product.id,
                "name": _("Advance recovery %(pct)s%% - %(po)s", pct=self.recovery_pct, po=self.purchase_order_id.name),
                "quantity": 1,
                "price_unit": self.amount_advance_recovery,
                "tax_ids": [Command.set(product.supplier_taxes_id.filtered(lambda t: t.company_id == company).ids)],
            }))
        if self.amount_retention:
            product = company.hom_retention_sub_product_id
            if not product:
                raise UserError(_("Set the subcontractor retention product in Accounting settings (Contracting)."))
            cmds.append(Command.create({
                "product_id": product.id,
                "name": _("Retention %(pct)s%% - %(po)s", pct=self.retention_pct, po=self.purchase_order_id.name),
                "quantity": 1,
                "price_unit": self.amount_retention,
                "tax_ids": [Command.clear()],
            }))
        return cmds

    def action_create_bill(self):
        moves = self.env["account.move"]
        for cert in self:
            if cert.state != "approved":
                raise UserError(_("Only approved certificates can be billed."))
            order = cert.purchase_order_id.with_context(default_move_type="in_invoice")
            vals = order._prepare_invoice()
            vals.update({
                "invoice_date": cert.date,
                "ref": cert.name,
                "hom_sub_certificate_id": cert.id,
                "invoice_line_ids": cert._prepare_bill_lines(),
            })
            move = self.env["account.move"].create(vals)
            cert.write({"bill_id": move.id, "state": "billed"})
            cert.activity_feedback(["mail.mail_activity_data_todo"])
            moves |= move
        return moves._get_records_action(name=_("Subcontractor Bill")) if moves else True

    def action_view_bill(self):
        self.ensure_one()
        return self.bill_id._get_records_action()


class HomSubcontractCertificateLine(models.Model):
    _name = "hom.subcontract.certificate.line"
    _description = "Subcontractor Certificate Line"
    _order = "sequence, id"

    certificate_id = fields.Many2one("hom.subcontract.certificate", required=True, ondelete="cascade", index=True)
    currency_id = fields.Many2one(related="certificate_id.currency_id")
    purchase_line_id = fields.Many2one("purchase.order.line", string="Item", required=True)
    sequence = fields.Integer(related="purchase_line_id.sequence", store=True)
    product_id = fields.Many2one(related="purchase_line_id.product_id")
    product_uom_id = fields.Many2one(related="purchase_line_id.uom_id")
    contract_qty = fields.Float(related="purchase_line_id.product_qty", string="Contract Qty")
    price_unit = fields.Float(related="purchase_line_id.price_unit")
    previous_qty = fields.Float(compute="_compute_previous_qty", store=True, readonly=False, digits="Product Unit")
    cumulative_qty = fields.Float(digits="Product Unit")
    current_qty = fields.Float(compute="_compute_current", store=True, digits="Product Unit", string="This Certificate")
    amount_current = fields.Monetary(compute="_compute_current", store=True)
    amount_cumulative = fields.Monetary(compute="_compute_current", store=True)
    progress_pct = fields.Float(compute="_compute_current", store=True, string="Progress %")

    @api.depends("purchase_line_id", "certificate_id.state")
    def _compute_previous_qty(self):
        for line in self:
            if line.certificate_id.state not in (False, "draft"):
                continue
            line.previous_qty = self._previous_for(line.purchase_line_id, line._origin)

    @api.model
    def _previous_for(self, purchase_line, exclude):
        others = self.search([
            ("purchase_line_id", "=", purchase_line.id),
            ("certificate_id.state", "in", ("approved", "billed")),
            ("id", "not in", exclude.ids),
        ])
        return max(others.mapped("cumulative_qty") or [0.0])

    @api.depends("previous_qty", "cumulative_qty", "price_unit", "contract_qty")
    def _compute_current(self):
        for line in self:
            line.current_qty = line.cumulative_qty - line.previous_qty
            line.amount_current = line.current_qty * line.price_unit
            line.amount_cumulative = line.cumulative_qty * line.price_unit
            line.progress_pct = (line.cumulative_qty / line.contract_qty * 100.0) if line.contract_qty else 0.0

    @api.constrains("cumulative_qty", "previous_qty")
    def _check_quantities(self):
        for line in self:
            if float_compare(line.cumulative_qty, line.previous_qty, precision_digits=6) < 0:
                raise ValidationError(_("Cumulative quantity of %s cannot be lower than the previous certificate.",
                                        line.product_id.display_name))
            if line.contract_qty and float_compare(line.cumulative_qty, line.contract_qty, precision_digits=6) > 0:
                raise ValidationError(_("Cumulative quantity of %s exceeds the subcontract quantity.",
                                        line.product_id.display_name))
