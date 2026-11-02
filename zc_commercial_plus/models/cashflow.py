from collections import defaultdict
from datetime import timedelta

from odoo import _, api, fields, models

from odoo.addons.zc_project_controls.models.tools import month_end, month_start, months_between, next_month

KINDS = [
    ("in_open", "Owner invoices to collect"),
    ("in_future", "Future certificates (remaining contract)"),
    ("in_retention", "Retention release"),
    ("out_open", "Supplier & subcontractor bills to pay"),
    ("out_committed", "Orders committed, not billed yet"),
    ("out_budget", "Remaining budget not ordered yet"),
]


class ZcCashflowLine(models.Model):
    _name = "zc.cashflow.line"
    _description = "Contract Cash-Flow Forecast"
    _order = "sale_order_id, month, kind"

    company_id = fields.Many2one("res.company", readonly=True)
    currency_id = fields.Many2one(related="company_id.currency_id")
    sale_order_id = fields.Many2one("sale.order", string="Contract", readonly=True, index=True)
    project_id = fields.Many2one("project.project", readonly=True)
    month = fields.Date(readonly=True)
    month_label = fields.Char(string="Period", readonly=True)
    kind = fields.Selection(KINDS, readonly=True)
    direction = fields.Selection([("in", "Cash in"), ("out", "Cash out")], readonly=True)
    amount = fields.Monetary(string="Net cash", readonly=True, help="Positive = collected, negative = paid.")
    cumulative = fields.Monetary(string="Cumulative (contract)", readonly=True)

    # ------------------------------------------------------------------
    @api.model
    def _zc_forecast(self, order, today):
        """{(month, kind): amount} for one contract. Collections: open owner invoices on their due date, the rest of
        the contract certified along the approved baseline (or evenly to the end date) and collected after the
        consultant's review + the payment term, the retention at the end of the defects period. Payments: open
        bills on their due date, orders committed and not billed one month after delivery, the budget not ordered
        yet spread like the work."""
        env = self.env
        company = order.company_id
        out = defaultdict(float)
        start = month_start(today)

        def put(day, kind, amount):
            if amount:
                out[(max(month_start(day), start), kind)] += amount

        project = order.project_ids[:1]
        # open owner invoices
        invoices = env["account.move"].search([("move_type", "in", ("out_invoice", "out_refund")), ("state", "=", "posted"),
                                               ("payment_state", "in", ("not_paid", "partial")),
                                               ("line_ids.sale_line_ids.order_id", "=", order.id)])
        invoices |= order.invoice_ids.filtered(lambda m: m.state == "posted" and m.payment_state in ("not_paid", "partial"))
        for inv in invoices:
            put(inv.invoice_date_due or today, "in_open", abs(inv.amount_residual_signed))
        # remaining contract value, net of retention and advance recovery
        lines = order.order_line.filtered(lambda l: not l.display_type and not l.is_downpayment)
        remaining = sum(max(l.product_uom_qty - l.qty_delivered, 0.0) * l.price_unit for l in lines)
        net_pct = 1.0 - (order.hom_retention_pct or 0.0) / 100.0
        if order.hom_advance_outstanding and remaining:
            net_pct -= min(order.hom_advance_outstanding / remaining, 1.0)
        lag = (company.zc_consultant_review_days or 14) + 30
        months = self._zc_work_months(order, project, today)
        for month, share in months:
            put(month_end(month) + timedelta(days=lag), "in_future", remaining * share * net_pct)
        # retention back at the end of the defects period
        end = project.date or today
        dlp_end = order.zc_dlp_end or (end + timedelta(days=30 * (order.zc_dlp_months or 12)))
        retention_future = remaining * (order.hom_retention_pct or 0.0) / 100.0
        put(dlp_end, "in_retention", order.hom_retention_balance + retention_future)
        # payments: open bills of the project
        account = project.account_id
        if account:
            bills = env["account.move"].search([("move_type", "in", ("in_invoice", "in_refund")), ("state", "=", "posted"),
                                                ("payment_state", "in", ("not_paid", "partial")),
                                                ("invoice_line_ids.analytic_distribution", "in", [account.id])])
            for bill in bills:
                put(bill.invoice_date_due or today, "out_open", -abs(bill.amount_residual_signed))
        # committed and not billed
        pols = env["purchase.order.line"].search([("order_id.state", "in", ("purchase", "done")),
                                                  ("zc_boq_line_id.order_id", "=", order.id)])
        committed = 0.0
        for pol in pols:
            open_qty = max(pol.product_qty - pol.qty_invoiced, 0.0)
            committed += pol.price_subtotal
            put((pol.date_planned.date() if pol.date_planned else today) + timedelta(days=30), "out_committed",
                -open_qty * pol.price_unit)
        # the rest of the budget, along the work
        budget_left = max(sum(order.zc_cost_line_ids.mapped("budget_amount")) - committed, 0.0) \
            if "zc_cost_line_ids" in order._fields else 0.0
        progress = sum(l.qty_delivered * l.price_unit for l in lines) / (order.amount_untaxed or 1.0)
        budget_left *= max(1.0 - progress, 0.0)
        for month, share in months:
            put(month_end(month) + timedelta(days=30), "out_budget", -budget_left * share)
        return out

    @api.model
    def _zc_work_months(self, order, project, today):
        """[(month, share of the remaining work)] from the approved baseline, else evenly to the end date."""
        baseline = self.env["zc.baseline"].search([("sale_order_id", "=", order.id), ("state", "=", "approved")],
                                                  order="version desc", limit=1)
        first = month_start(today)
        if baseline:
            rows = defaultdict(float)
            for line in baseline.line_ids.filtered(lambda l: l.month >= first):
                rows[line.month] += line.planned_value
            total = sum(rows.values())
            if total:
                return sorted((m, v / total) for m, v in rows.items())
        end = project.date if project and project.date and project.date > today else today + timedelta(days=90)
        months = months_between(today, end)
        return [(m, 1.0 / len(months)) for m in months]

    @api.model
    def action_refresh(self):
        today = fields.Date.context_today(self)
        self.sudo().search([("company_id", "in", self.env.companies.ids)]).unlink()
        orders = self.env["sale.order"].search([("state", "=", "sale"), ("hom_project_type", "=", "contracting"),
                                                ("company_id", "in", self.env.companies.ids)])
        if "zc_closeout_state" in orders._fields:
            orders = orders.filtered(lambda o: o.zc_closeout_state != "closed")
        vals = []
        for order in orders:
            data = self._zc_forecast(order, today)
            running = 0.0
            for (month, kind) in sorted(data, key=lambda k: (k[0], k[1])):
                amount = order.currency_id.round(data[(month, kind)])
                running += amount
                vals.append({"company_id": order.company_id.id, "sale_order_id": order.id,
                             "project_id": order.project_ids[:1].id, "month": month,
                             "month_label": month.strftime("%Y-%m"), "kind": kind,
                             "direction": "in" if kind.startswith("in") else "out", "amount": amount,
                             "cumulative": running})
        self.sudo().create(vals)
        action = self.env["ir.actions.act_window"]._for_xml_id("zc_commercial_plus.action_zc_cashflow")
        return action

    @api.model
    def _zc_peak_need(self, order):
        """Lowest cumulative cash position of the contract over the forecast (negative = money to find)."""
        rows = self.search([("sale_order_id", "=", order.id)], order="month, kind")
        return min(rows.mapped("cumulative") or [0.0])
