from dateutil.relativedelta import relativedelta

from odoo import Command, _, api, fields, models


class SaleOrder(models.Model):
    _inherit = "sale.order"

    zc_dlp_months = fields.Integer(string="Defects liability (months)", default=12)
    zc_retention_prelim_pct = fields.Float(
        string="Retention released at preliminary handover %",
        help="Part of the retention paid back at the preliminary handover (e.g. 50%); the rest at the final handover.")
    zc_prelim_handover_id = fields.Many2one("zc.handover", string="Preliminary handover", readonly=True, copy=False)
    zc_final_handover_id = fields.Many2one("zc.handover", string="Final handover", readonly=True, copy=False)
    zc_prelim_date = fields.Date(related="zc_prelim_handover_id.date", string="Preliminary handover on")
    zc_dlp_end = fields.Date(related="zc_prelim_handover_id.dlp_end", string="Defects liability ends", store=True)
    zc_final_date = fields.Date(related="zc_final_handover_id.date", string="Final handover on")
    zc_closeout_state = fields.Selection([
        ("running", "Under execution"), ("dlp", "Defects liability period"), ("dlp_over", "Final handover due"),
        ("closed", "Closed (final handover)"),
    ], string="Close-out", compute="_compute_zc_closeout_state", search="_search_zc_closeout_state")
    zc_handover_ids = fields.One2many("zc.handover", "sale_order_id", string="Handovers")

    def _compute_zc_closeout_state(self):
        today = fields.Date.context_today(self)
        for order in self:
            if order.zc_final_handover_id:
                state = "closed"
            elif order.zc_prelim_handover_id:
                state = "dlp_over" if order.zc_dlp_end and order.zc_dlp_end <= today else "dlp"
            else:
                state = "running"
            order.zc_closeout_state = state

    def _search_zc_closeout_state(self, operator, value):
        today = fields.Date.context_today(self)
        domains = {
            "closed": [("zc_final_handover_id", "!=", False)],
            "dlp": [("zc_final_handover_id", "=", False), ("zc_prelim_handover_id", "!=", False), ("zc_dlp_end", ">", today)],
            "dlp_over": [("zc_final_handover_id", "=", False), ("zc_prelim_handover_id", "!=", False), ("zc_dlp_end", "<=", today)],
            "running": [("zc_prelim_handover_id", "=", False)],
        }
        values = [value] if isinstance(value, str) else list(value)
        ids = set()
        for v in values:
            ids |= set(self.search(domains.get(v, [("id", "=", 0)])).ids)
        return [("id", "in" if operator in ("=", "in") else "not in", sorted(ids))]

    def _zc_release_retention(self, amount, label):
        """Draft retention release invoice of ``amount`` (all or part of the retention balance)."""
        self.ensure_one()
        action = self.action_hom_release_retention()
        move = self.env["account.move"].browse(action["res_id"])
        line = move.invoice_line_ids[:1]
        if self.currency_id.compare_amounts(amount, line.price_unit) != 0:
            line.price_unit = amount
        move.ref = "%s - %s" % (move.ref, label)
        return move

    def _zc_subcontracts(self):
        """Subcontracts charged to this contract's project (analytic account)."""
        self.ensure_one()
        account = self.project_ids[:1].account_id
        if not account:
            return self.env["purchase.order"]
        orders = self.env["purchase.order"].search([("hom_is_subcontract", "=", True), ("state", "=", "purchase"),
                                                    ("company_id", "=", self.company_id.id)])
        return orders.filtered(lambda po: any(
            str(account.id) in str(key).split(",") for line in po.order_line for key in (line.analytic_distribution or {})))

    def action_zc_handover(self):
        self.ensure_one()
        kind = "final" if self.zc_prelim_handover_id else "preliminary"
        return {
            "type": "ir.actions.act_window", "name": _("Handover"), "res_model": "zc.handover",
            "view_mode": "form", "target": "current",
            "context": {"default_sale_order_id": self.id, "default_kind": kind,
                        "default_dlp_months": self.zc_dlp_months or 12},
        }

    def action_zc_handovers(self):
        self.ensure_one()
        return {"type": "ir.actions.act_window", "name": _("Handovers"), "res_model": "zc.handover",
                "view_mode": "list,form", "domain": [("sale_order_id", "=", self.id)],
                "context": {"default_sale_order_id": self.id}}

    def _zc_final_account(self):
        """Figures of the final account (الحساب الختامي)."""
        self.ensure_one()
        cur = self.currency_id
        lines = self.order_line.filtered(lambda l: not l.display_type and not l.is_downpayment and l.product_id
                                         and not getattr(l, "zc_provisional", False))
        vos = self.env["hom.variation.order"].search([("sale_order_id", "=", self.id), ("state", "=", "applied")])
        vo_amount = sum(vos.mapped("amount_total"))
        boq = []
        for l in lines:
            price = l.price_unit * (1 - (l.discount or 0.0) / 100.0)
            boq.append({"code": l.product_id.default_code or "", "name": l.product_id.with_context(lang="ar_001").name,
                        "uom": l.product_uom_id.name, "contract_qty": l.product_uom_qty, "final_qty": l.qty_delivered,
                        "price": price, "contract_amount": l.product_uom_qty * price,
                        "final_amount": l.qty_delivered * price,
                        "variance": (l.qty_delivered - l.product_uom_qty) * price})
        certs = self.hom_certificate_ids.filtered(lambda c: c.state == "invoiced")
        deductions = {}
        for d in certs.mapped("zc_deduction_ids").filtered("amount"):
            key = d.type_id.with_context(lang="ar_001").name
            deductions[key] = deductions.get(key, 0.0) + d.amount
        invoices = self.invoice_ids.filtered(lambda m: m.state == "posted") | \
            self.hom_retention_release_ids.filtered(lambda m: m.state == "posted")
        invoiced = sum(m.amount_total * (-1 if m.move_type == "out_refund" else 1) for m in invoices)
        residual = sum(m.amount_residual * (-1 if m.move_type == "out_refund" else 1) for m in invoices)
        claims = self.env["zc.quantity.dispute"].sudo().search([("sale_order_id", "=", self.id)])
        return {
            "original": sum(r["contract_amount"] for r in boq) - vo_amount, "variations": vo_amount,
            "vos": vos, "contract": sum(r["contract_amount"] for r in boq),
            "final_work": sum(r["final_amount"] for r in boq), "boq": boq,
            "certificates": certs, "advance": self.hom_advance_received,
            "advance_outstanding": self.hom_advance_outstanding,
            "retention_held": self.hom_retention_held, "retention_released": self.hom_retention_released,
            "retention_balance": self.hom_retention_balance, "deductions": deductions,
            "invoiced": invoiced, "collected": invoiced - residual, "balance": residual,
            "claims_waived": sum(claims.filtered(lambda c: c.state == "waived").mapped("amount")),
            "claims_recovered": sum(claims.mapped("recovered_amount")),
            "currency": cur,
        }

    @api.model
    def _zc_cron_dlp_reminder(self):
        today = fields.Date.context_today(self)
        for order in self.search([("zc_prelim_handover_id", "!=", False), ("zc_final_handover_id", "=", False),
                                  ("zc_dlp_end", "<=", today + relativedelta(days=30))]):
            responsible = order.project_ids[:1].user_id or order.user_id
            if responsible and not order.activity_ids.filtered(lambda a: a.summary and order.name in a.summary
                                                               and a.user_id == responsible):
                order.activity_schedule("mail.mail_activity_data_todo", user_id=responsible.id,
                                        date_deadline=order.zc_dlp_end,
                                        summary=_("Final handover due - %s", order.name),
                                        note=_("Defects liability period ends on %s.", order.zc_dlp_end))
