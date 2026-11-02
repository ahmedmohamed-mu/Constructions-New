from datetime import timedelta

from odoo import _, api, fields, models
from odoo.exceptions import UserError


class ZcRfi(models.Model):
    _name = "zc.rfi"
    _description = "Request for Information"
    _inherit = ["mail.thread", "mail.activity.mixin"]
    _order = "date desc, id desc"

    name = fields.Char(default=lambda self: _("New"), readonly=True, copy=False)
    subject = fields.Char(required=True, tracking=True)
    sale_order_id = fields.Many2one("sale.order", string="Contract", required=True, check_company=True,
                                    domain="[('state', '=', 'sale'), ('hom_project_type', '=', 'contracting')]")
    project_id = fields.Many2one("project.project", compute="_compute_project_id", store=True)
    company_id = fields.Many2one(related="sale_order_id.company_id", store=True)
    consultant_id = fields.Many2one(related="sale_order_id.hom_consultant_id", string="Consultant")
    sale_line_id = fields.Many2one("sale.order.line", string="BOQ item",
                                   domain="[('order_id', '=', sale_order_id), ('display_type', '=', False)]")
    discipline = fields.Selection([("arch", "Architecture"), ("struct", "Structure"), ("mep", "MEP"),
                                   ("finish", "Finishes"), ("other", "Other")], default="arch")
    user_id = fields.Many2one("res.users", string="Raised by", default=lambda self: self.env.user)
    date = fields.Date(string="Sent on", default=fields.Date.context_today)
    due_date = fields.Date(string="Answer due", compute="_compute_due", store=True, readonly=False)
    question = fields.Html(required=True)
    proposal = fields.Html(string="Contractor's proposal")
    answer = fields.Html()
    answer_date = fields.Date(tracking=True)
    state = fields.Selection([("draft", "Draft"), ("sent", "Sent to consultant"), ("answered", "Answered"),
                              ("closed", "Closed")], default="draft", tracking=True, copy=False)
    overdue = fields.Boolean(compute="_compute_overdue", search="_search_overdue")
    days_late = fields.Integer(compute="_compute_overdue")
    impact = fields.Selection([("none", "No impact"), ("cost", "Cost"), ("time", "Time"), ("both", "Time & cost")],
                              default="none", tracking=True)
    variation_id = fields.Many2one("hom.variation.order", string="Variation", readonly=True, copy=False)
    claim_id = fields.Many2one("zc.claim", string="Claim", readonly=True, copy=False)

    @api.depends("sale_order_id")
    def _compute_project_id(self):
        for rec in self:
            rec.project_id = rec.sale_order_id.project_ids[:1]

    @api.depends("date", "company_id.zc_rfi_answer_days")
    def _compute_due(self):
        for rec in self:
            rec.due_date = rec.date and rec.date + timedelta(days=rec.company_id.zc_rfi_answer_days or 7)

    def _compute_overdue(self):
        today = fields.Date.context_today(self)
        for rec in self:
            late = rec.state == "sent" and rec.due_date and rec.due_date < today
            rec.overdue = bool(late)
            rec.days_late = (today - rec.due_date).days if late else 0

    def _search_overdue(self, operator, value):
        values = [value] if isinstance(value, bool) else list(value)
        today = fields.Date.context_today(self)
        ids = self.search([("state", "=", "sent"), ("due_date", "<", today)]).ids
        positive = (operator in ("=", "in") and True in values) or (operator in ("!=", "not in") and False in values)
        return [("id", "in" if positive else "not in", ids)]

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if vals.get("name", _("New")) == _("New"):
                vals["name"] = self.env["ir.sequence"].next_by_code("zc.rfi") or _("New")
        return super().create(vals_list)

    def action_send(self):
        for rec in self:
            rec.write({"state": "sent", "date": rec.date or fields.Date.context_today(self)})
            rec.activity_schedule("mail.mail_activity_data_todo", date_deadline=rec.due_date, user_id=rec.user_id.id,
                                  summary=_("Follow up the consultant's answer to %s", rec.name))

    def action_answer(self):
        for rec in self:
            if not rec.answer:
                raise UserError(_("Write the consultant's answer first."))
            rec.write({"state": "answered", "answer_date": rec.answer_date or fields.Date.context_today(self)})
            rec.activity_feedback(["mail.mail_activity_data_todo"])

    def action_close(self):
        self.write({"state": "closed"})

    def action_create_variation(self):
        """An answer that changes the scope becomes a variation order (owner side follows the VO cycle)."""
        self.ensure_one()
        if not self.variation_id:
            self.variation_id = self.env["hom.variation.order"].create({
                "sale_order_id": self.sale_order_id.id, "title": self.subject,
                "reference": self.name if "reference" in self.env["hom.variation.order"]._fields else False})
        return {"type": "ir.actions.act_window", "res_model": "hom.variation.order", "res_id": self.variation_id.id,
                "view_mode": "form"}

    def action_create_claim(self):
        """A late or scope-changing answer starts the claim clock from the answer (or today)."""
        self.ensure_one()
        if not self.claim_id:
            self.claim_id = self.env["zc.claim"].create({
                "title": _("%(r)s: %(s)s", r=self.name, s=self.subject), "sale_order_id": self.sale_order_id.id,
                "event_date": self.answer_date or fields.Date.context_today(self), "cause": "drawings",
                "claim_type": "both" if self.impact == "both" else ("cost" if self.impact == "cost" else "eot")})
        return {"type": "ir.actions.act_window", "res_model": "zc.claim", "res_id": self.claim_id.id,
                "view_mode": "form"}


class ResCompany(models.Model):
    _inherit = "res.company"

    zc_rfi_answer_days = fields.Integer(string="RFI answer within (days)", default=7)
    zc_ncr_close_days = fields.Integer(string="NCR corrective action within (days)", default=14)
