from odoo import _, api, fields, models

OPEN_DISPUTES = ("open", "resubmitted")


class SaleOrderLine(models.Model):
    _inherit = "sale.order.line"

    # ---------------- sold & budget
    zc_ic_sold = fields.Monetary(string="Sold", compute="_compute_zc_item_card")
    zc_ic_budget_cost = fields.Monetary(string="Budget Cost", compute="_compute_zc_item_card")
    zc_ic_budget_margin = fields.Monetary(string="Budget Margin", compute="_compute_zc_item_card")
    zc_ic_budget_margin_pct = fields.Float(string="Budget Margin %", compute="_compute_zc_item_card")
    # ---------------- purchasing & subcontract
    zc_ic_committed = fields.Monetary(string="Committed (POs & subcontracts)", compute="_compute_zc_item_card")
    zc_ic_over_budget_lines = fields.Integer(string="Orders above budget", compute="_compute_zc_item_card")
    zc_ic_billed_cost = fields.Monetary(string="Billed by suppliers & subcontractors",
                                        compute="_compute_zc_item_card")
    zc_ic_paid_cost = fields.Monetary(string="Paid", compute="_compute_zc_item_card")
    zc_ic_sub_partner_ids = fields.Many2many("res.partner", string="Executed by", compute="_compute_zc_item_card")
    zc_ic_sub_certified_qty = fields.Float(string="Certified to subcontractors", digits="Product Unit",
                                           compute="_compute_zc_item_card")
    zc_ic_sub_certified_amount = fields.Monetary(string="Subcontract work certified",
                                                 compute="_compute_zc_item_card")
    # ---------------- site
    zc_ic_measured_qty = fields.Float(string="Executed (site measurement)", digits="Product Unit",
                                      compute="_compute_zc_item_card")
    zc_ic_accepted_qty = fields.Float(string="Accepted (WIR passed)", digits="Product Unit",
                                      compute="_compute_zc_item_card")
    # ---------------- owner
    zc_ic_submitted_qty = fields.Float(string="Submitted to consultant", digits="Product Unit",
                                       compute="_compute_zc_item_card")
    zc_ic_certified_amount = fields.Monetary(string="Certified value", compute="_compute_zc_item_card")
    zc_ic_cut_qty = fields.Float(string="Cut by consultant (open claims)", digits="Product Unit",
                                 compute="_compute_zc_item_card")
    zc_ic_cut_amount = fields.Monetary(string="Open quantity claims", compute="_compute_zc_item_card")
    zc_ic_invoiced_amount = fields.Monetary(string="Invoiced to owner", compute="_compute_zc_item_card")
    zc_ic_collected_amount = fields.Monetary(string="Collected", compute="_compute_zc_item_card")
    zc_ic_overdue_amount = fields.Monetary(string="Overdue", compute="_compute_zc_item_card")
    zc_ic_progress_pct = fields.Float(string="Certified %", compute="_compute_zc_item_card")
    # ---------------- forecast
    zc_ic_forecast_cost = fields.Monetary(string="Forecast Final Cost", compute="_compute_zc_item_card")
    zc_ic_forecast_margin = fields.Monetary(string="Forecast Margin", compute="_compute_zc_item_card")
    zc_ic_forecast_margin_pct = fields.Float(string="Forecast Margin %", compute="_compute_zc_item_card")
    zc_ic_margin_drift = fields.Monetary(string="Change vs Budget", compute="_compute_zc_item_card")
    zc_ic_status = fields.Selection([("ok", "On budget"), ("warn", "Margin eroding"), ("loss", "Loss")],
                                    string="Item Health", compute="_compute_zc_item_card")
    # ---------------- counters for the buttons
    zc_ic_po_count = fields.Integer(compute="_compute_zc_item_card")
    zc_ic_measure_count = fields.Integer(compute="_compute_zc_item_card")
    zc_ic_wir_count = fields.Integer(compute="_compute_zc_item_card")
    zc_ic_cert_count = fields.Integer(compute="_compute_zc_item_card")
    zc_ic_sub_cert_count = fields.Integer(compute="_compute_zc_item_card")
    zc_ic_invoice_count = fields.Integer(compute="_compute_zc_item_card")
    zc_ic_bill_count = fields.Integer(compute="_compute_zc_item_card")
    zc_ic_dispute_count = fields.Integer(compute="_compute_zc_item_card")
    zc_ic_vo_count = fields.Integer(compute="_compute_zc_item_card")

    # ------------------------------------------------------------------ sources
    def _zc_ic_purchase_lines(self):
        self.ensure_one()
        POL = self.env["purchase.order.line"].sudo()
        domain = [("order_id.state", "in", ("purchase", "done"))]
        links = [("zc_boq_line_id", "=", self.id)]
        if "hom_boq_line_id" in POL._fields:
            links = ["|"] + links + [("hom_boq_line_id", "=", self.id)]
        return POL.search(domain + links)

    def _zc_ic_measurements(self):
        self.ensure_one()
        meas = self.env["hom.site.measurement"].sudo().search([("sale_line_id", "=", self.id)])
        return meas.filtered(lambda m: not m.certificate_id or m.certificate_id.state != "cancel")

    def _zc_ic_cert_lines(self):
        self.ensure_one()
        return self.env["hom.progress.certificate.line"].sudo().search([
            ("sale_line_id", "=", self.id), ("certificate_id.state", "!=", "cancel")])

    def _zc_ic_sub_cert_lines(self, pols):
        if not pols:
            return self.env["hom.subcontract.certificate.line"]
        return self.env["hom.subcontract.certificate.line"].sudo().search([
            ("purchase_line_id", "in", pols.ids), ("certificate_id.state", "!=", "cancel")])

    def _zc_ic_disputes(self):
        self.ensure_one()
        return self.env["zc.quantity.dispute"].sudo().search([("sale_line_id", "=", self.id)])

    def _zc_ic_variation_lines(self):
        self.ensure_one()
        VOL = self.env["hom.variation.order.line"].sudo()
        lines = VOL.search([("sale_line_id", "=", self.id)])
        if self.zc_variation_line_id:
            lines |= self.zc_variation_line_id.sudo()
        return lines

    def _zc_ic_customer_lines(self):
        self.ensure_one()
        return self.env["account.move.line"].sudo().search([
            ("sale_line_ids", "in", self.ids), ("parent_state", "=", "posted"),
            ("move_id.move_type", "in", ("out_invoice", "out_refund"))])

    def _zc_ic_vendor_lines(self, pols):
        if not pols:
            return self.env["account.move.line"]
        return self.env["account.move.line"].sudo().search([
            ("purchase_line_id", "in", pols.ids), ("parent_state", "=", "posted"),
            ("move_id.move_type", "in", ("in_invoice", "in_refund"))])

    @staticmethod
    def _zc_ic_paid_ratio(move):
        """Share of the invoice already paid. A payment registered on a bank journal without an outstanding
        account has no journal entry until the bank statement is matched (the invoice shows 'In payment' and its
        residual does not move): such payments are counted from the payments matched to the invoice."""
        total = abs(move.amount_total_signed)
        if not total:
            return 0.0
        paid = total - abs(move.amount_residual_signed)
        if paid < total and "matched_payment_ids" in move._fields:
            pending = move.matched_payment_ids.filtered(
                lambda p: not p.move_id and p.state not in ("draft", "canceled", "rejected"))
            paid += sum(abs(p.amount_company_currency_signed) for p in pending)
        return max(0.0, min(1.0, paid / total))

    # ------------------------------------------------------------------ compute
    @api.depends_context("uid")
    def _compute_zc_item_card(self):
        today = fields.Date.context_today(self)
        for line in self:
            vals = dict.fromkeys([
                "zc_ic_sold", "zc_ic_budget_cost", "zc_ic_budget_margin", "zc_ic_budget_margin_pct",
                "zc_ic_committed", "zc_ic_billed_cost", "zc_ic_paid_cost", "zc_ic_sub_certified_qty",
                "zc_ic_sub_certified_amount", "zc_ic_measured_qty", "zc_ic_accepted_qty", "zc_ic_submitted_qty",
                "zc_ic_certified_amount", "zc_ic_cut_qty", "zc_ic_cut_amount", "zc_ic_invoiced_amount",
                "zc_ic_collected_amount", "zc_ic_overdue_amount", "zc_ic_progress_pct", "zc_ic_forecast_cost",
                "zc_ic_forecast_margin", "zc_ic_forecast_margin_pct", "zc_ic_margin_drift"], 0.0)
            vals.update(dict.fromkeys([
                "zc_ic_over_budget_lines", "zc_ic_po_count", "zc_ic_measure_count", "zc_ic_wir_count",
                "zc_ic_cert_count", "zc_ic_sub_cert_count", "zc_ic_invoice_count", "zc_ic_bill_count",
                "zc_ic_dispute_count", "zc_ic_vo_count"], 0))
            vals["zc_ic_sub_partner_ids"] = [(6, 0, [])]
            vals["zc_ic_status"] = False
            if not line.id or line.display_type or line.is_downpayment:
                line.update(vals)
                continue

            sold = line.price_subtotal
            costs = line.sudo().zc_cost_line_ids
            budget = sum(costs.mapped("budget_amount"))
            pols = line._zc_ic_purchase_lines()
            linked = pols.filtered(lambda p: p.zc_cost_line_id in costs)
            unlinked = pols - linked
            committed = sum(pols.mapped("price_subtotal"))
            # forecast = what is committed + what the budget still has to buy, per budget line
            forecast = sum(unlinked.mapped("price_subtotal"))
            for cost in costs:
                cost_pols = linked.filtered(lambda p, c=cost: p.zc_cost_line_id == c)
                ordered = sum(cost_pols.mapped("product_qty"))
                still = max(cost.budget_qty - ordered, 0.0) * cost.unit_cost
                forecast += sum(cost_pols.mapped("price_subtotal")) + still
            if not costs and not pols:
                forecast = 0.0

            subs = pols.filtered(lambda p: p.order_id.hom_is_subcontract)
            sub_lines = line._zc_ic_sub_cert_lines(subs)
            sub_qty = sub_amount = 0.0
            for pol in subs:
                mine = sub_lines.filtered(lambda s, p=pol: s.purchase_line_id == p)
                if mine:
                    sub_qty += max(mine.mapped("cumulative_qty"))
                    sub_amount += max(mine.mapped("amount_cumulative"))

            bills = line._zc_ic_vendor_lines(pols)
            billed = sum(bills.mapped(lambda l: abs(l.balance) * (-1 if l.move_id.move_type == "in_refund" else 1)))
            paid = sum(abs(l.balance) * line._zc_ic_paid_ratio(l.move_id) for l in bills)

            meas = line._zc_ic_measurements()
            cert_lines = line._zc_ic_cert_lines()
            submitted = max(cert_lines.mapped("zc_submitted_qty") or [0.0])
            disputes = line._zc_ic_disputes()
            open_disp = disputes.filtered(lambda d: d.state in OPEN_DISPUTES)

            inv_lines = line._zc_ic_customer_lines()
            invoiced = collected = overdue = 0.0
            for aml in inv_lines:
                sign = -1 if aml.move_id.move_type == "out_refund" else 1
                amount = abs(aml.balance) * sign
                invoiced += amount
                ratio = line._zc_ic_paid_ratio(aml.move_id)
                collected += amount * ratio
                due = aml.move_id.invoice_date_due
                if due and due < today and ratio < 1.0:
                    overdue += amount * (1.0 - ratio)

            budget_margin = sold - budget if costs else 0.0
            forecast_margin = sold - forecast if forecast else 0.0
            status = False
            if forecast:
                if forecast_margin < 0:
                    status = "loss"
                elif costs and forecast_margin < budget_margin - 0.005:
                    status = "warn"
                else:
                    status = "ok"

            vals.update({
                "zc_ic_sold": sold,
                "zc_ic_budget_cost": budget,
                "zc_ic_budget_margin": budget_margin,
                "zc_ic_budget_margin_pct": (budget_margin / sold) if sold and costs else 0.0,
                "zc_ic_committed": committed,
                "zc_ic_over_budget_lines": len(pols.filtered(
                    lambda p: p.zc_check in ("over_qty", "over_price", "over_both"))),
                "zc_ic_billed_cost": billed,
                "zc_ic_paid_cost": paid,
                "zc_ic_sub_partner_ids": [(6, 0, subs.order_id.partner_id.ids)],
                "zc_ic_sub_certified_qty": sub_qty,
                "zc_ic_sub_certified_amount": sub_amount,
                "zc_ic_measured_qty": sum(meas.mapped("quantity")),
                "zc_ic_accepted_qty": sum(meas.filtered("zc_inspection_ok").mapped("quantity")),
                "zc_ic_submitted_qty": submitted or line.qty_delivered,
                "zc_ic_certified_amount": line.qty_delivered * line.price_unit,
                "zc_ic_cut_qty": sum(open_disp.mapped("qty")),
                "zc_ic_cut_amount": sum(open_disp.mapped("amount")),
                "zc_ic_invoiced_amount": invoiced,
                "zc_ic_collected_amount": collected,
                "zc_ic_overdue_amount": overdue,
                "zc_ic_progress_pct": (line.qty_delivered / line.product_uom_qty * 100.0)
                if line.product_uom_qty else 0.0,
                "zc_ic_forecast_cost": forecast,
                "zc_ic_forecast_margin": forecast_margin,
                "zc_ic_forecast_margin_pct": (forecast_margin / sold) if sold and forecast else 0.0,
                "zc_ic_margin_drift": (forecast_margin - budget_margin) if forecast and costs else 0.0,
                "zc_ic_status": status,
                "zc_ic_po_count": len(pols.order_id),
                "zc_ic_measure_count": len(meas),
                "zc_ic_wir_count": len(meas.zc_wir_id),
                "zc_ic_cert_count": len(cert_lines.certificate_id),
                "zc_ic_sub_cert_count": len(sub_lines.certificate_id),
                "zc_ic_invoice_count": len(inv_lines.move_id),
                "zc_ic_bill_count": len(bills.move_id),
                "zc_ic_dispute_count": len(disputes),
                "zc_ic_vo_count": len(line._zc_ic_variation_lines().variation_id),
            })
            line.update(vals)

    # ------------------------------------------------------------------ actions
    def action_zc_item_card(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": _("Item Card - %s", self.product_id.display_name or self.name),
            "res_model": "sale.order.line",
            "res_id": self.id,
            "view_mode": "form",
            "views": [(self.env.ref("zc_boq_item_card.view_zc_item_card_form").id, "form")],
            "target": "current",
        }

    def _zc_ic_open(self, records, name):
        action = {"type": "ir.actions.act_window", "name": name, "res_model": records._name,
                  "domain": [("id", "in", records.ids)], "context": {"create": False}}
        if len(records) == 1:
            action.update({"view_mode": "form", "res_id": records.id})
        else:
            action["view_mode"] = "list,form"
        return action

    def action_zc_ic_purchases(self):
        return self._zc_ic_open(self._zc_ic_purchase_lines().order_id, _("Purchases & Subcontracts"))

    def action_zc_ic_measurements(self):
        return self._zc_ic_open(self._zc_ic_measurements(), _("Site Measurements"))

    def action_zc_ic_wirs(self):
        return self._zc_ic_open(self._zc_ic_measurements().zc_wir_id, _("Inspection Requests"))

    def action_zc_ic_certificates(self):
        return self._zc_ic_open(self._zc_ic_cert_lines().certificate_id, _("Owner Certificates"))

    def action_zc_ic_sub_certificates(self):
        pols = self._zc_ic_purchase_lines()
        return self._zc_ic_open(self._zc_ic_sub_cert_lines(pols).certificate_id, _("Subcontractor Certificates"))

    def action_zc_ic_invoices(self):
        return self._zc_ic_open(self._zc_ic_customer_lines().move_id, _("Owner Invoices"))

    def action_zc_ic_bills(self):
        return self._zc_ic_open(self._zc_ic_vendor_lines(self._zc_ic_purchase_lines()).move_id,
                                _("Supplier & Subcontractor Bills"))

    def action_zc_ic_disputes(self):
        return self._zc_ic_open(self._zc_ic_disputes(), _("Quantity Claims"))

    def action_zc_ic_variations(self):
        return self._zc_ic_open(self._zc_ic_variation_lines().variation_id, _("Variation Orders"))


class SaleOrder(models.Model):
    _inherit = "sale.order"

    zc_ic_item_count = fields.Integer(compute="_compute_zc_ic_item_count")

    def _compute_zc_ic_item_count(self):
        for order in self:
            order.zc_ic_item_count = len(order.order_line.filtered(
                lambda l: not l.display_type and not l.is_downpayment))

    def action_zc_item_cards(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": _("Item Cards - %s", self.name),
            "res_model": "sale.order.line",
            "view_mode": "list,form",
            "views": [(self.env.ref("zc_boq_item_card.view_zc_item_card_list").id, "list"),
                      (self.env.ref("zc_boq_item_card.view_zc_item_card_form").id, "form")],
            "domain": [("order_id", "=", self.id), ("display_type", "=", False), ("is_downpayment", "=", False)],
            "context": {"create": False, "delete": False},
        }
