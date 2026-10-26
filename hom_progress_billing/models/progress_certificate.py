from odoo import Command, _, api, fields, models
from odoo.exceptions import UserError, ValidationError
from odoo.tools import float_compare, float_is_zero, float_round


class HomProgressCertificate(models.Model):
    _name = "hom.progress.certificate"
    _description = "Owner Progress Certificate"
    _inherit = ["mail.thread", "mail.activity.mixin"]
    _order = "sale_order_id, number desc, id desc"
    _check_company_auto = True

    name = fields.Char(default=lambda self: _("New"), readonly=True, copy=False, tracking=True)
    number = fields.Integer(string="Certificate No.", readonly=True, copy=False)
    state = fields.Selection([
        ("draft", "Draft"),
        ("submitted", "Submitted to Consultant"),
        ("approved", "Approved"),
        ("invoiced", "Invoiced"),
        ("cancel", "Cancelled"),
    ], default="draft", required=True, tracking=True, copy=False)
    sale_order_id = fields.Many2one(
        "sale.order", string="Contract", required=True, tracking=True, check_company=True,
        domain="[('state', '=', 'sale'), ('hom_project_type', '=', 'contracting')]")
    company_id = fields.Many2one(related="sale_order_id.company_id", store=True)
    currency_id = fields.Many2one(related="sale_order_id.currency_id", store=True)
    partner_id = fields.Many2one(related="sale_order_id.partner_id", store=True, string="Owner")
    consultant_id = fields.Many2one(related="sale_order_id.hom_consultant_id", store=True)
    project_id = fields.Many2one("project.project", compute="_compute_project_id", store=True)
    user_id = fields.Many2one("res.users", string="Prepared by", default=lambda self: self.env.user, tracking=True)
    date = fields.Date(default=fields.Date.context_today, required=True, tracking=True)
    period_end = fields.Date(string="Work measured until")
    line_ids = fields.One2many("hom.progress.certificate.line", "certificate_id", copy=True)
    invoice_id = fields.Many2one("account.move", readonly=True, copy=False, tracking=True)
    invoice_payment_state = fields.Selection(related="invoice_id.payment_state")
    # Contract terms are copied on the certificate (not linked): changing the % on the contract later
    # must never rewrite certificates that were already invoiced and posted in accounting.
    advance_pct = fields.Float(string="Advance %", compute="_compute_contract_terms", store=True, readonly=False,
                               precompute=True)
    retention_pct = fields.Float(string="Retention %", compute="_compute_contract_terms", store=True, readonly=False,
                                 precompute=True)
    amount_current = fields.Monetary(string="Work this Certificate", compute="_compute_amounts", store=True)
    amount_cumulative = fields.Monetary(string="Cumulative Work", compute="_compute_amounts", store=True)
    amount_advance_recovery = fields.Monetary(string="Advance Recovery", compute="_compute_amounts", store=True)
    amount_retention = fields.Monetary(string="Retention", compute="_compute_amounts", store=True)
    amount_net = fields.Monetary(string="Net (before VAT)", compute="_compute_amounts", store=True)
    progress_pct = fields.Float(string="Contract Progress %", compute="_compute_amounts", store=True, aggregator="avg")
    note = fields.Html()
    measurement_ids = fields.One2many("hom.site.measurement", "certificate_id", string="Site Measurements")
    measurement_count = fields.Integer(compute="_compute_measurement_count")

    def _compute_measurement_count(self):
        for cert in self:
            cert.measurement_count = len(cert.measurement_ids)

    @api.depends("sale_order_id")
    def _compute_project_id(self):
        for cert in self:
            cert.project_id = cert.sale_order_id.project_ids[:1]

    @api.depends("sale_order_id")
    def _compute_contract_terms(self):
        for cert in self:
            cert.advance_pct = cert.sale_order_id.hom_advance_pct
            cert.retention_pct = cert.sale_order_id.hom_retention_pct

    def _hom_sync_contract_terms(self):
        """Draft certificates follow the current contract terms; later ones keep what was certified."""
        for cert in self.filtered(lambda c: c.state == "draft"):
            order = cert.sale_order_id
            if (cert.advance_pct, cert.retention_pct) != (order.hom_advance_pct, order.hom_retention_pct):
                cert.write({"advance_pct": order.hom_advance_pct, "retention_pct": order.hom_retention_pct})

    @api.depends("line_ids.amount_current", "line_ids.amount_cumulative", "advance_pct", "retention_pct")
    def _compute_amounts(self):
        for cert in self:
            current = sum(cert.line_ids.mapped("amount_current"))
            cert.amount_current = current
            cert.amount_cumulative = sum(cert.line_ids.mapped("amount_cumulative"))
            cert.amount_advance_recovery = -sum(cert.currency_id.round(qty * dp.price_unit)
                                                for dp, qty in cert._recovery_plan(current)) if cert.currency_id else 0.0
            cert.amount_retention = -cert.currency_id.round(current * cert.retention_pct / 100.0) if cert.currency_id else 0.0
            cert.amount_net = current + cert.amount_advance_recovery + cert.amount_retention
            contract = cert.sale_order_id.amount_untaxed
            cert.progress_pct = (cert.amount_cumulative / contract * 100.0) if contract else 0.0

    # ------------------------------------------------------------------
    # Advance (down payment) helpers
    # ------------------------------------------------------------------
    def _downpayment_lines(self):
        self.ensure_one()
        return self.sale_order_id.order_line.filtered(lambda l: l.is_downpayment and not l.display_type)

    def _dp_remaining_qty(self, dp_line, exclude_move=None):
        """Signed invoiced quantity still to be recovered on a down-payment SO line."""
        qty = 0.0
        for aml in dp_line.invoice_lines:
            move = aml.move_id
            if move == exclude_move or move.state == "cancel":
                continue
            sign = -1 if move.move_type == "out_refund" else 1
            if move.state == "posted" or aml.quantity < 0:
                qty += sign * aml.quantity
        return max(qty, 0.0)

    def _recovery_plan(self, amount_current):
        """[(down-payment line, quantity)] recovered by this certificate.

        Quantities are rounded to the unit precision up front, so the certificate and its invoice show the
        very same recovery amount.
        """
        self.ensure_one()
        to_recover = min(amount_current * self.advance_pct / 100.0, self._remaining_advance())
        digits = self.env["decimal.precision"].precision_get("Product Unit")
        plan = []
        for dp in self._downpayment_lines():
            if to_recover <= 0 or dp.price_unit <= 0:
                continue
            qty = float_round(min(to_recover / dp.price_unit, self._dp_remaining_qty(dp, self.invoice_id or None)),
                              precision_digits=digits)
            if qty <= 0:
                continue
            plan.append((dp, qty))
            to_recover -= qty * dp.price_unit
        return plan

    def _remaining_advance(self):
        self.ensure_one()
        exclude = self.invoice_id or None
        return sum(self._dp_remaining_qty(l, exclude) * l.price_unit for l in self._downpayment_lines())

    # ------------------------------------------------------------------
    # Actions
    # ------------------------------------------------------------------
    def action_load_boq(self):
        self._hom_sync_contract_terms()
        for cert in self:
            if cert.state != "draft":
                raise UserError(_("BOQ lines can only be loaded on a draft certificate."))
            existing = cert.line_ids.sale_line_id
            lines = cert.sale_order_id.order_line.filtered(
                lambda l: not l.display_type and not l.is_downpayment and l.product_id and l not in existing)
            Line = self.env["hom.progress.certificate.line"]
            cert.line_ids = [Command.create({
                "sale_line_id": l.id,
                "cumulative_qty": Line._previous_for(l, Line),
            }) for l in lines]
            cert._apply_measurements()

    def _apply_measurements(self):
        """Pull open site measurements into the certificate: cumulative = previous + measured."""
        Measurement = self.env["hom.site.measurement"].sudo()
        for cert in self.filtered(lambda c: c.state == "draft"):
            domain = [("sale_line_id", "in", cert.line_ids.sale_line_id.ids),
                      "|", ("certificate_id", "=", False), ("certificate_id.state", "=", "cancel")]
            if cert.period_end:
                domain.append(("date", "<=", cert.period_end))
            Measurement.search(domain).write({"certificate_id": cert.id})
            capped = []
            for line in cert.line_ids:
                measured = sum(cert.measurement_ids.filtered(lambda m: m.sale_line_id == line.sale_line_id).mapped("quantity"))
                if not measured:
                    continue
                qty = line.previous_qty + measured
                if line.contract_qty and qty > line.contract_qty:
                    capped.append(line.product_id.display_name)
                    qty = line.contract_qty
                line.cumulative_qty = qty
            if capped:
                cert.message_post(body=_("Measured quantities exceed the contract for: %s. Capped to the contract "
                                         "quantity; issue a variation order for the extra work.", ", ".join(capped)))

    def action_load_measurements(self):
        self._apply_measurements()

    def action_view_measurements(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window", "name": _("Site Measurements"),
            "res_model": "hom.site.measurement", "view_mode": "list,form",
            "domain": [("certificate_id", "=", self.id)],
        }

    def action_submit(self):
        self._hom_sync_contract_terms()
        for cert in self:
            if cert.state != "draft":
                continue
            if not cert.line_ids.filtered(lambda l: not float_is_zero(l.current_qty, precision_digits=6)):
                raise UserError(_("Nothing measured in this certificate."))
            if not cert.number:
                last = self.search([("sale_order_id", "=", cert.sale_order_id.id), ("number", ">", 0)],
                                   order="number desc", limit=1)
                cert.number = (last.number or 0) + 1
                cert.name = _("%(so)s - Certificate %(n)s", so=cert.sale_order_id.name, n=cert.number)
            cert.line_ids._freeze_previous()
            cert.state = "submitted"
            if cert.consultant_id:
                cert.message_post(body=_("Submitted to the consultant %s for review.", cert.consultant_id.name))

    def _check_can_approve(self):
        user = self.env.user
        if not (self.env.is_superuser() or user.has_group("sales_team.group_sale_manager")
                or user.has_group("project.group_project_manager")):
            raise UserError(_("Only a project manager or a sales manager can approve a progress certificate."))

    def action_approve(self):
        self._check_can_approve()
        for cert in self.filtered(lambda c: c.state == "submitted"):
            for line in cert.line_ids.sudo():
                sol = line.sale_line_id
                if sol.qty_delivered_method == "manual":
                    sol.qty_delivered = line.cumulative_qty
            cert.state = "approved"
            cert.message_post(body=_("Approved by %s; delivered quantities updated on the contract.", self.env.user.name))
            accountant = cert.company_id.hom_certificate_accountant_id
            if cert.company_id.hom_auto_draft_invoice:
                # zero-touch: the draft invoice is ready, the accountant only reviews and posts it
                cert.sudo().action_create_invoice()
                cert.message_post(body=_("Draft invoice %s prepared automatically.", cert.invoice_id.display_name))
                if accountant:
                    cert.invoice_id.sudo().activity_schedule(
                        "mail.mail_activity_data_todo", user_id=accountant.id,
                        summary=_("Review and post certificate invoice %s", cert.name),
                        note=_("Prepared automatically from the approved certificate (works, advance recovery, "
                               "retention, VAT). Check and post."))
            elif accountant:
                cert.activity_schedule(
                    "mail.mail_activity_data_todo", user_id=accountant.id,
                    summary=_("Invoice certificate %s", cert.name),
                    note=_("Certificate approved by the consultant: create and post the customer invoice."))

    def action_reset_draft(self):
        for cert in self:
            if cert.invoice_id and cert.invoice_id.state != "cancel":
                raise UserError(_("Cancel the invoice before resetting the certificate."))
            cert.state = "draft"

    def action_cancel(self):
        for cert in self:
            if cert.invoice_id and cert.invoice_id.state == "posted":
                raise UserError(_("The invoice of this certificate is posted."))
            cert.state = "cancel"
            cert.measurement_ids.sudo().write({"certificate_id": False})

    def _prepare_invoice_lines(self):
        self.ensure_one()
        cmds = []
        seq = 1
        for line in self.line_ids.filtered(lambda l: not float_is_zero(l.current_qty, precision_digits=6)):
            vals = line.sale_line_id._prepare_invoice_line(quantity=line.current_qty, sequence=seq)
            vals.update({"hom_previous_qty": line.previous_qty, "hom_cumulative_qty": line.cumulative_qty})
            cmds.append(Command.create(vals))
            seq += 1
        for dp, qty in self._recovery_plan(self.amount_current):
            cmds.append(Command.create(dp._prepare_invoice_line(quantity=-qty, sequence=900)))
        retention = self.company_id.hom_retention_owner_product_id
        if self.retention_pct and self.amount_current:
            if not retention:
                raise UserError(_("Set the owner retention product in Accounting settings (Contracting)."))
            cmds.append(Command.create({
                "product_id": retention.id,
                "name": _("Retention %(pct)s%% - %(cert)s", pct=self.retention_pct, cert=self.name),
                "quantity": 1,
                "price_unit": -self.currency_id.round(self.amount_current * self.retention_pct / 100.0),
                "tax_ids": [Command.clear()],
                "sequence": 950,
            }))
        return cmds

    def action_create_invoice(self):
        moves = self.env["account.move"]
        for cert in self:
            if cert.state != "approved":
                raise UserError(_("Only approved certificates can be invoiced."))
            # Accountants may not have Sales rights: prepare values as superuser, create as the user.
            vals = cert.sudo().sale_order_id._prepare_invoice()
            vals.update({
                "invoice_date": cert.date,
                "ref": cert.name,
                "hom_certificate_id": cert.id,
                "invoice_line_ids": cert.sudo()._prepare_invoice_lines(),
            })
            move = self.env["account.move"].with_context(default_move_type="out_invoice").create(vals)
            cert.write({"invoice_id": move.id, "state": "invoiced"})
            cert.activity_feedback(["mail.mail_activity_data_todo"])
            moves |= move
        return moves._get_records_action(name=_("Certificate Invoice")) if moves else True

    @api.model
    def _cron_monthly_drafts(self):
        """Create the monthly draft certificate of every running contract (on the configured day)."""
        today = fields.Date.context_today(self)
        orders = self.env["sale.order"].search([("state", "=", "sale"), ("hom_project_type", "=", "contracting")])
        for order in orders:
            if today.day != (order.company_id.hom_certificate_day or 25):
                continue
            if self.search_count([("sale_order_id", "=", order.id), ("state", "in", ("draft", "submitted"))]):
                continue
            cert = self.create({"sale_order_id": order.id, "date": today, "period_end": today})
            cert.action_load_boq()
            project = cert.project_id
            responsible = project.user_id or order.user_id
            if responsible:
                cert.activity_schedule(
                    "mail.mail_activity_data_todo", user_id=responsible.id,
                    summary=_("Monthly progress certificate - %s", order.name),
                    note=_("Draft prepared automatically from the site measurements. Review the quantities "
                           "with the consultant, then submit."))

    def action_view_invoice(self):
        self.ensure_one()
        return self.invoice_id._get_records_action()


class HomProgressCertificateLine(models.Model):
    _name = "hom.progress.certificate.line"
    _description = "Owner Progress Certificate Line"
    _order = "sequence, id"

    certificate_id = fields.Many2one("hom.progress.certificate", required=True, ondelete="cascade", index=True)
    state = fields.Selection(related="certificate_id.state")
    currency_id = fields.Many2one(related="certificate_id.currency_id")
    sale_line_id = fields.Many2one("sale.order.line", string="BOQ Item", required=True,
                                   domain="[('order_id', '=', parent.sale_order_id), ('display_type', '=', False), ('is_downpayment', '=', False)]")
    sequence = fields.Integer(related="sale_line_id.sequence", store=True)
    product_id = fields.Many2one(related="sale_line_id.product_id")
    name = fields.Text(related="sale_line_id.name")
    product_uom_id = fields.Many2one(related="sale_line_id.product_uom_id")
    contract_qty = fields.Float(related="sale_line_id.product_uom_qty", string="Contract Qty")
    price_unit = fields.Float(related="sale_line_id.price_unit")
    previous_qty = fields.Float(string="Previous Qty", compute="_compute_previous_qty", store=True, readonly=False,
                                digits="Product Unit")
    cumulative_qty = fields.Float(string="Cumulative Qty", digits="Product Unit",
                                  help="Total quantity executed to date (as measured with the consultant).")
    current_qty = fields.Float(string="This Certificate", compute="_compute_current", store=True, digits="Product Unit")
    amount_current = fields.Monetary(compute="_compute_current", store=True)
    amount_cumulative = fields.Monetary(compute="_compute_current", store=True)
    progress_pct = fields.Float(string="Progress %", compute="_compute_current", store=True)

    @api.depends("sale_line_id", "certificate_id.state")
    def _compute_previous_qty(self):
        for line in self:
            if line.certificate_id.state not in (False, "draft"):
                continue
            line.previous_qty = self._previous_for(line.sale_line_id, line._origin)

    @api.model
    def _previous_for(self, sale_line, exclude):
        others = self.search([
            ("sale_line_id", "=", sale_line.id),
            ("certificate_id.state", "in", ("approved", "invoiced")),
            ("id", "not in", exclude.ids),
        ])
        return max(others.mapped("cumulative_qty") or [0.0])

    def _freeze_previous(self):
        self._compute_previous_qty()

    @api.depends("previous_qty", "cumulative_qty", "price_unit", "contract_qty", "sale_line_id.discount")
    def _compute_current(self):
        for line in self:
            line.current_qty = line.cumulative_qty - line.previous_qty
            price = line.price_unit * (1 - (line.sale_line_id.discount or 0.0) / 100.0)
            line.amount_current = line.current_qty * price
            line.amount_cumulative = line.cumulative_qty * price
            line.progress_pct = (line.cumulative_qty / line.contract_qty * 100.0) if line.contract_qty else 0.0

    @api.constrains("cumulative_qty", "previous_qty")
    def _check_quantities(self):
        for line in self:
            if float_compare(line.cumulative_qty, line.previous_qty, precision_digits=6) < 0:
                raise ValidationError(_("Cumulative quantity of %s cannot be lower than the previous certificate.",
                                        line.product_id.display_name))
            if line.contract_qty and float_compare(line.cumulative_qty, line.contract_qty, precision_digits=6) > 0:
                raise ValidationError(_("Cumulative quantity of %s exceeds the contract quantity; "
                                        "issue a variation order first.", line.product_id.display_name))
