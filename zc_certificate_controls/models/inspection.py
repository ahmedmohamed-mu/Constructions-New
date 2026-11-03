from datetime import timedelta

from odoo import Command, _, api, fields, models
from odoo.exceptions import UserError
from odoo.fields import Domain

PASSED = ("approved", "approved_comments")


class ZcInspectionRequest(models.Model):
    """Work Inspection Request: the consultant inspects executed work before it can be certified.

    A hold point stops the next activity until the consultant answers; a witness point lets the work go on.
    """
    _name = "zc.inspection.request"
    _description = "Work Inspection Request (WIR)"
    _inherit = ["mail.thread", "mail.activity.mixin"]
    _order = "date_requested desc, id desc"
    _check_company_auto = True

    name = fields.Char(default=lambda self: _("New"), readonly=True, copy=False)
    title = fields.Char(string="Work inspected", required=True, tracking=True)
    state = fields.Selection([
        ("draft", "Draft"),
        ("submitted", "Waiting consultant"),
        ("approved", "Approved"),
        ("approved_comments", "Approved with comments"),
        ("rejected", "Rejected - re-inspect"),
        ("cancel", "Cancelled"),
    ], default="draft", required=True, tracking=True, copy=False)
    inspection_type = fields.Selection([("hold", "Hold point"), ("witness", "Witness point")],
                                       default="hold", required=True,
                                       help="Hold point: no further work until the consultant approves.\n"
                                            "Witness point: work goes on while the consultant observes.")
    sale_order_id = fields.Many2one("sale.order", string="Contract", required=True,
                                    domain="[('hom_project_type', '=', 'contracting'), ('state', '=', 'sale')]")
    company_id = fields.Many2one(related="sale_order_id.company_id", store=True)
    project_id = fields.Many2one("project.project", compute="_compute_project_id", store=True)
    consultant_id = fields.Many2one(related="sale_order_id.hom_consultant_id", store=True)
    location = fields.Char(string="Location / Zone")
    user_id = fields.Many2one("res.users", string="Site Engineer", default=lambda self: self.env.user, tracking=True)
    date_requested = fields.Datetime(string="Requested on", copy=False, readonly=True)
    date_due = fields.Date(string="Answer due", compute="_compute_date_due", store=True)
    date_inspected = fields.Date(string="Inspected on", copy=False, tracking=True)
    inspector = fields.Char(string="Consultant inspector")
    consultant_comments = fields.Text()
    measurement_ids = fields.One2many("hom.site.measurement", "zc_wir_id", string="Executed work")
    measurement_count = fields.Integer(compute="_compute_totals")
    quantity_summary = fields.Char(string="Quantities", compute="_compute_totals")
    previous_id = fields.Many2one("zc.inspection.request", string="Re-inspection of", readonly=True, copy=False)
    is_overdue = fields.Boolean(compute="_compute_is_overdue", search="_search_is_overdue")
    attachment_note = fields.Char(string="Attached", help="Photos, test results, check-lists sent with the request.")

    @api.depends("sale_order_id")
    def _compute_project_id(self):
        for rec in self:
            rec.project_id = rec.sale_order_id.project_ids[:1]

    @api.depends("date_requested", "company_id.zc_wir_review_days")
    def _compute_date_due(self):
        for rec in self:
            rec.date_due = rec.date_requested and (
                rec.date_requested.date() + timedelta(days=rec.company_id.zc_wir_review_days or 2))

    def _compute_is_overdue(self):
        today = fields.Date.context_today(self)
        for rec in self:
            rec.is_overdue = rec.state == "submitted" and rec.date_due and rec.date_due < today

    def _search_is_overdue(self, operator, value):
        if operator not in ("=", "!=") or not isinstance(value, bool):
            raise UserError(_("Unsupported search."))
        domain = Domain([("state", "=", "submitted"), ("date_due", "<", fields.Date.context_today(self))])
        return domain if (operator == "=") == value else ~domain

    @api.depends("measurement_ids.quantity")
    def _compute_totals(self):
        for rec in self:
            rec.measurement_count = len(rec.measurement_ids)
            parts = {}
            for m in rec.measurement_ids:
                key = (m.product_id.default_code or m.product_id.name, m.product_uom_id.name)
                parts[key] = parts.get(key, 0.0) + m.quantity
            rec.quantity_summary = " | ".join("%s: %g %s" % (k[0], v, k[1] or "") for k, v in parts.items())

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if vals.get("name", _("New")) == _("New"):
                vals["name"] = self.env["ir.sequence"].next_by_code("zc.inspection.request") or _("New")
        return super().create(vals_list)

    # ------------------------------------------------------------------
    def action_submit(self):
        for rec in self.filtered(lambda r: r.state == "draft"):
            if not rec.measurement_ids:
                raise UserError(_("Link the executed work (site measurements) to inspect."))
            rec.write({"state": "submitted", "date_requested": fields.Datetime.now()})
            rec.message_post(body=_("Inspection requested from the consultant %s.",
                                    rec.consultant_id.name or _("(not set on the contract)")))

    def _answer(self, state):
        for rec in self.filtered(lambda r: r.state == "submitted"):
            rec.write({"state": state, "date_inspected": rec.date_inspected or fields.Date.context_today(self)})
            if state == "rejected" and rec.user_id:
                rec.activity_schedule("mail.mail_activity_data_todo", user_id=rec.user_id.id,
                                      summary=_("WIR rejected: correct and re-inspect %s", rec.name),
                                      note=rec.consultant_comments or "")

    def action_approve(self):
        self._answer("approved")

    def action_approve_comments(self):
        self._answer("approved_comments")

    def action_reject(self):
        self._answer("rejected")

    def action_cancel(self):
        if self.measurement_ids.filtered(lambda m: m.state == "certified"):
            raise UserError(_("Work of this request is already certified."))
        self.write({"state": "cancel"})

    def action_draft(self):
        self.filtered(lambda r: r.state == "cancel").write({"state": "draft"})

    def action_reinspect(self):
        """New request for the corrected work; the measurements move to it."""
        self.ensure_one()
        if self.state != "rejected":
            raise UserError(_("Only a rejected request is re-inspected."))
        new = self.copy({"previous_id": self.id, "title": _("%s (re-inspection)", self.title),
                         "consultant_comments": False, "date_inspected": False, "inspector": False})
        self.measurement_ids.write({"zc_wir_id": new.id})
        return new._get_records_action(name=_("Re-inspection"))

    def action_view_measurements(self):
        self.ensure_one()
        return {"type": "ir.actions.act_window", "name": _("Executed work"), "res_model": "hom.site.measurement",
                "view_mode": "list,form", "domain": [("zc_wir_id", "=", self.id)]}


class HomSiteMeasurement(models.Model):
    _inherit = "hom.site.measurement"

    zc_wir_id = fields.Many2one("zc.inspection.request", string="Inspection (WIR)", copy=False,
                                index="btree_not_null", ondelete="set null")
    zc_wir_state = fields.Selection(related="zc_wir_id.state", store=True, string="Inspection")
    zc_inspection_ok = fields.Boolean(compute="_compute_zc_inspection_ok", store=True, string="Inspected")

    @api.depends("zc_wir_id.state")
    def _compute_zc_inspection_ok(self):
        for rec in self:
            rec.zc_inspection_ok = rec.zc_wir_id.state in PASSED

    @api.model
    def _search(self, domain, *args, **kwargs):
        # Certificate gate: only inspected work is pulled into a certificate (set by the certificates).
        if self.env.context.get("zc_wir_gate"):
            domain = Domain(domain) & Domain("zc_inspection_ok", "=", True)
        return super()._search(domain, *args, **kwargs)

    def action_zc_request_inspection(self):
        """One WIR for the selected work (same contract), ready to send to the consultant."""
        todo = self.filtered(lambda m: not m.zc_wir_id or m.zc_wir_id.state in ("rejected", "cancel"))
        todo = todo.filtered(lambda m: m.state != "certified")
        if not todo:
            raise UserError(_("The selected work is already under inspection."))
        if len(todo.sale_order_id) > 1:
            raise UserError(_("Select work of one contract at a time."))
        items = ", ".join(dict.fromkeys(todo.mapped(lambda m: m.product_id.default_code or m.product_id.name)))
        zones = ", ".join(dict.fromkeys(z for z in todo.mapped("location") if z))
        wir = self.env["zc.inspection.request"].create({
            "sale_order_id": todo.sale_order_id.id,
            "title": items,
            "location": zones,
            "measurement_ids": [Command.set(todo.ids)],
        })
        return wir._get_records_action(name=_("Work Inspection Request"))
