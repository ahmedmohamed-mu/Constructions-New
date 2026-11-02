from datetime import timedelta

from odoo import _, api, fields, models
from odoo.tools.safe_eval import safe_eval


class ZcMyWork(models.TransientModel):
    _name = "zc.my.work"
    _description = "My Work"

    user_id = fields.Many2one("res.users", string="Work of", default=lambda self: self.env.user, required=True,
                              help="Managers can look at the list of another user (site engineer, QS, PM, accounts).")
    date = fields.Date(default=fields.Date.context_today, readonly=True)
    line_ids = fields.One2many("zc.my.work.line", "work_id", readonly=True)
    total = fields.Integer(compute="_compute_total")

    def _compute_total(self):
        for rec in self:
            rec.total = sum(rec.line_ids.mapped("count"))

    @api.model
    def action_open(self):
        rec = self.create({})
        rec._refresh()
        return {"type": "ir.actions.act_window", "name": _("My Work"), "res_model": self._name, "res_id": rec.id,
                "view_mode": "form", "target": "current",
                "views": [(self.env.ref("zc_my_work.view_zc_my_work_form").id, "form")]}

    @api.onchange("user_id")
    def _onchange_user_id(self):
        if self.user_id:
            rows = self._zc_items(self.user_id, self.date or fields.Date.context_today(self))
            self.line_ids = [(5, 0, 0)] + [(0, 0, row) for row in rows if row["count"]]

    def action_refresh(self):
        self._refresh()
        return True

    def _refresh(self):
        for rec in self:
            rec.line_ids.unlink()
            rows = rec._zc_items(rec.user_id, rec.date)
            rec.line_ids = [(0, 0, row) for row in rows if row["count"]]

    # ------------------------------------------------------------------
    def _zc_items(self, user, today):
        """Everything waiting for this user, by the role they play on each document (no new security model:
        the project manager of the project, the technical office of the company, the author of the document)."""
        env = self.env
        rows = []
        day = fields.Date.to_string(today)
        in14 = fields.Date.to_string(today + timedelta(days=14))
        my_projects = env["project.project"].search([("user_id", "=", user.id)])
        my_orders = my_projects.mapped("sale_order_id") | my_projects.mapped("reinvoiced_sale_order_id")

        def add(sequence, role, label, model, domain, level="info"):
            if model not in env:
                return
            count = env[model].search_count(domain)
            rows.append({"sequence": sequence, "role": role, "name": label, "model": model, "count": count,
                         "domain": repr(domain), "level": level})

        # site engineer
        add(10, "site", _("Measured work without an inspection request (WIR)"), "hom.site.measurement",
            [("user_id", "=", user.id), ("zc_wir_id", "=", False), ("state", "=", "open")])
        add(11, "site", _("My inspections rejected: correct and re-inspect"), "zc.inspection.request",
            [("user_id", "=", user.id), ("state", "=", "rejected")], "warning")
        add(12, "site", _("Subcontractor certificates to prepare (draft)"), "hom.subcontract.certificate",
            [("user_id", "=", user.id), ("state", "=", "draft")])
        add(13, "site", _("Snags to close"), "zc.handover.snag",
            [("state", "=", "open"), ("handover_id.sale_order_id", "in", my_orders.ids)], "warning")
        if "zc.daily.report" in env:
            add(14, "site", _("My daily reports still in draft"), "zc.daily.report",
                [("user_id", "=", user.id), ("state", "=", "draft")])
        if "zc.ncr" in env:
            add(15, "site", _("Non-conformances to correct"), "zc.ncr",
                [("state", "=", "open"), "|", ("user_id", "=", user.id), ("sale_order_id", "in", my_orders.ids)],
                "warning")
        if "zc.rfi" in env:
            add(16, "site", _("My RFIs late at the consultant"), "zc.rfi",
                [("user_id", "=", user.id), ("state", "=", "sent"), ("due_date", "<", day)], "warning")
        # technical office (QS)
        if user == env.company.zc_qs_user_id:
            add(20, "qs", _("Subcontractor certificates to check (technical office)"), "hom.subcontract.certificate",
                [("state", "=", "submitted"), ("zc_review_stage", "=", "qs")], "warning")
        add(21, "qs", _("Owner certificates in draft"), "hom.progress.certificate",
            [("state", "=", "draft"), ("sale_order_id", "in", my_orders.ids)])
        add(22, "qs", _("Quantity claims open (consultant cuts)"), "zc.quantity.dispute",
            [("state", "in", ("open", "resubmitted")), ("sale_order_id", "in", my_orders.ids)])
        # project manager
        add(30, "pm", _("Subcontractor certificates to approve"), "hom.subcontract.certificate",
            [("state", "=", "submitted"), ("zc_review_stage", "=", "pm"), ("project_id", "in", my_projects.ids)],
            "warning")
        add(31, "pm", _("Purchase exceptions to decide (above budget)"), "purchase.order",
            [("zc_exception_state", "=", "requested"), ("order_line.zc_boq_line_id.order_id", "in", my_orders.ids)],
            "danger")
        if "zc.material.request" in env:
            add(32, "pm", _("Material requests to approve"), "zc.material.request",
                [("state", "=", "submitted"), ("sale_order_id", "in", my_orders.ids)], "warning")
        if "zc.daily.report" in env:
            add(33, "pm", _("Daily reports to approve"), "zc.daily.report",
                [("state", "=", "submitted"), ("sale_order_id", "in", my_orders.ids)])
        add(34, "pm", _("Claim deadlines within 14 days"), "zc.claim",
            [("next_deadline", "<=", in14), ("next_deadline", "!=", False),
             ("state", "not in", ("agreed", "rejected", "withdrawn")), "|", ("user_id", "=", user.id),
             ("sale_order_id", "in", my_orders.ids)], "danger")
        add(35, "pm", _("Variation orders waiting for the owner's agreement"), "hom.variation.order",
            [("sale_order_id", "in", my_orders.ids), ("zc_stage", "in", ("submitted", "negotiation"))])
        # accounts
        if user.has_group("account.group_account_invoice"):
            add(40, "accounts", _("Approved subcontractor certificates to bill"), "hom.subcontract.certificate",
                [("state", "=", "approved")], "warning")
            add(41, "accounts", _("Approved owner certificates to invoice"), "hom.progress.certificate",
                [("state", "=", "approved")], "warning")
            add(42, "accounts", _("Overdue owner invoices"), "account.move",
                [("move_type", "=", "out_invoice"), ("state", "=", "posted"), ("invoice_date_due", "<", day),
                 ("payment_state", "in", ("not_paid", "partial"))], "danger")
            add(43, "accounts", _("Draft retention releases to post"), "account.move",
                [("state", "=", "draft"), ("move_type", "=", "out_invoice"),
                 ("invoice_line_ids.product_id", "=", env.company.hom_retention_owner_product_id.id)])
        # everyone
        add(90, "all", _("My activities overdue"), "mail.activity",
            [("user_id", "=", user.id), ("date_deadline", "<", day)], "danger")
        add(91, "all", _("My activities due today"), "mail.activity",
            [("user_id", "=", user.id), ("date_deadline", "=", day)])
        return rows


class ZcMyWorkLine(models.TransientModel):
    _name = "zc.my.work.line"
    _description = "My Work Line"
    _order = "sequence, id"

    work_id = fields.Many2one("zc.my.work", required=True, ondelete="cascade")
    sequence = fields.Integer()
    role = fields.Selection([("site", "Site"), ("qs", "Technical office"), ("pm", "Project manager"),
                             ("accounts", "Accounts"), ("all", "Everyone")])
    name = fields.Char(string="Waiting for me")
    count = fields.Integer()
    level = fields.Selection([("info", "Info"), ("warning", "Warning"), ("danger", "Urgent")])
    model = fields.Char()
    domain = fields.Char()

    def action_open(self):
        self.ensure_one()
        return {"type": "ir.actions.act_window", "name": self.name, "res_model": self.model,
                "view_mode": "list,form", "domain": safe_eval(self.domain), "target": "current"}
