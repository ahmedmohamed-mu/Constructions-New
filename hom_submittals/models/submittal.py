from datetime import timedelta

from odoo import _, api, fields, models
from odoo.exceptions import UserError


class HomSubmittal(models.Model):
    _name = "hom.submittal"
    _description = "Submittal to Consultant"
    _inherit = ["mail.thread", "mail.activity.mixin"]
    _order = "due_date, id desc"
    _check_company_auto = True

    name = fields.Char(string="Reference", default=lambda self: _("New"), readonly=True, copy=False)
    title = fields.Char(required=True, tracking=True, help="e.g. Chilled water pipe insulation sample")
    submittal_type = fields.Selection([
        ("shop_drawing", "Shop drawing"),
        ("material", "Material submittal"),
        ("sample", "Sample"),
        ("method", "Method statement"),
        ("as_built", "As-built drawing"),
    ], string="Type", required=True, default="material", tracking=True)
    company_id = fields.Many2one("res.company", required=True, default=lambda self: self.env.company)
    project_id = fields.Many2one("project.project", required=True, tracking=True, check_company=True)
    sale_order_id = fields.Many2one(related="project_id.sale_order_id", string="Contract")
    consultant_id = fields.Many2one("res.partner", string="Consultant", tracking=True,
                                    compute="_compute_consultant_id", store=True, readonly=False)
    trade_account_id = fields.Many2one("account.analytic.account", string="Trade",
                                       help="Trade analytic account (HVAC, FIRE, PLUMB...).")
    product_id = fields.Many2one("product.product", string="Material")
    purchase_order_id = fields.Many2one("purchase.order", string="Purchase Order", check_company=True,
                                        help="Purchase waiting for this approval; the buyer is notified when it is approved.")
    revision = fields.Integer(string="Rev.", default=0, readonly=True, copy=False)
    previous_id = fields.Many2one("hom.submittal", string="Previous Revision", readonly=True, copy=False)
    responsible_id = fields.Many2one("res.users", string="Responsible", default=lambda self: self.env.user, tracking=True)
    submitted_date = fields.Date(readonly=True, copy=False, tracking=True)
    review_days = fields.Integer(string="Review Period (days)", default=14)
    due_date = fields.Date(compute="_compute_due_date", store=True)
    returned_date = fields.Date(readonly=True, copy=False)
    review_code = fields.Selection([
        ("A", "A - Approved"),
        ("B", "B - Approved as noted"),
        ("C", "C - Revise and resubmit"),
        ("D", "D - Rejected"),
    ], readonly=True, copy=False, tracking=True)
    state = fields.Selection([
        ("draft", "Draft"),
        ("submitted", "With Consultant"),
        ("approved", "Approved"),
        ("revise", "To Revise"),
        ("rejected", "Rejected"),
        ("superseded", "Superseded"),
    ], default="draft", required=True, tracking=True, copy=False)
    days_overdue = fields.Integer(compute="_compute_days_overdue")
    is_overdue = fields.Boolean(compute="_compute_days_overdue", search="_search_is_overdue")
    consultant_comments = fields.Html()
    note = fields.Html()

    @api.depends("project_id.sale_order_id.hom_consultant_id")
    def _compute_consultant_id(self):
        for sub in self:
            consultant = sub.project_id.sale_order_id.hom_consultant_id
            sub.consultant_id = consultant or sub.consultant_id

    @api.depends("submitted_date", "review_days")
    def _compute_due_date(self):
        for sub in self:
            sub.due_date = sub.submitted_date and sub.submitted_date + timedelta(days=sub.review_days or 0)

    def _compute_days_overdue(self):
        today = fields.Date.context_today(self)
        for sub in self:
            late = (today - sub.due_date).days if sub.state == "submitted" and sub.due_date else 0
            sub.days_overdue = max(late, 0)
            sub.is_overdue = late > 0

    def _search_is_overdue(self, operator, value):
        today = fields.Date.context_today(self)
        domain = [("state", "=", "submitted"), ("due_date", "<", today)]
        if operator in ("in", "not in"):
            positive = any(value) if operator == "in" else not any(value)
        else:
            positive = (operator == "=" and bool(value)) or (operator == "!=" and not value)
        return domain if positive else ["!", "&"] + domain

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if vals.get("name", _("New")) == _("New"):
                vals["name"] = self.env["ir.sequence"].next_by_code("hom.submittal") or _("New")
        return super().create(vals_list)

    def _display_ref(self):
        self.ensure_one()
        return "%s Rev.%s" % (self.name, self.revision)

    # ------------------------------------------------------------------ workflow
    def action_submit(self):
        for sub in self:
            if sub.state != "draft":
                continue
            sub.write({"state": "submitted", "submitted_date": fields.Date.context_today(sub)})
            sub.message_post(body=_("%(ref)s submitted to %(c)s; reply expected by %(d)s.",
                                    ref=sub._display_ref(), c=sub.consultant_id.name or _("the consultant"), d=sub.due_date))

    def _set_code(self, code):
        state = {"A": "approved", "B": "approved", "C": "revise", "D": "rejected"}[code]
        for sub in self:
            if sub.state != "submitted":
                raise UserError(_("Only a submittal with the consultant can receive a review code."))
            sub.write({"review_code": code, "state": state, "returned_date": fields.Date.context_today(sub)})
            sub.activity_ids.filtered(lambda a: a.summary and sub.name in a.summary).action_feedback(
                feedback=_("Consultant replied: %s", dict(self._fields["review_code"].selection)[code]))
            if state == "approved" and sub.purchase_order_id:
                buyer = sub.purchase_order_id.user_id or sub.responsible_id
                # the technical office may not have write access on purchases: notify the buyer as superuser
                sub.purchase_order_id.sudo().activity_schedule(
                    "mail.mail_activity_data_todo", user_id=buyer.id,
                    summary=_("Material approved: %s", sub.title),
                    note=_("%(ref)s came back with code %(code)s - the order can be released to the supplier.",
                           ref=sub._display_ref(), code=code))
            if state == "revise":
                sub.activity_schedule("mail.mail_activity_data_todo", user_id=sub.responsible_id.id,
                                      summary=_("Revise and resubmit %s", sub.name),
                                      note=_("Consultant code C: update the submittal and resubmit a new revision."))

    def action_code_a(self):
        self._set_code("A")

    def action_code_b(self):
        self._set_code("B")

    def action_code_c(self):
        self._set_code("C")

    def action_code_d(self):
        self._set_code("D")

    def action_resubmit(self):
        self.ensure_one()
        if self.state not in ("revise", "rejected"):
            raise UserError(_("Only a submittal to revise can be resubmitted."))
        new = self.copy({"name": self.name, "revision": self.revision + 1, "previous_id": self.id})
        self.activity_ids.action_feedback(feedback=_("Resubmitted as revision %s.", new.revision))
        self.state = "superseded"
        self.message_post(body=_("Superseded by revision %s.", new.revision))
        return {"type": "ir.actions.act_window", "res_model": "hom.submittal", "res_id": new.id, "view_mode": "form"}

    def action_reset_draft(self):
        self.filtered(lambda s: s.state == "submitted").write({"state": "draft", "submitted_date": False})

    @api.model
    def _cron_overdue(self):
        for sub in self.search([("is_overdue", "=", True)]):
            summary = _("%s overdue at the consultant", sub.name)
            if sub.activity_ids.filtered(lambda a: a.summary == summary):
                continue
            users = sub.responsible_id | sub.project_id.user_id
            for user in users:
                sub.activity_schedule("mail.mail_activity_data_todo", user_id=user.id, summary=summary,
                                      note=_("%(t)s - %(n)s days late. Follow up with %(c)s: work on site may be waiting for it.",
                                             t=sub.title, n=sub.days_overdue, c=sub.consultant_id.name or "-"))


class ProjectProject(models.Model):
    _inherit = "project.project"

    hom_submittal_ids = fields.One2many("hom.submittal", "project_id")
    hom_submittal_open_count = fields.Integer(compute="_compute_hom_submittal_open_count")

    def _compute_hom_submittal_open_count(self):
        for project in self:
            project.hom_submittal_open_count = len(project.hom_submittal_ids.filtered(
                lambda s: s.state in ("draft", "submitted", "revise")))

    def action_hom_submittals(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window", "name": _("Submittals"), "res_model": "hom.submittal",
            "view_mode": "list,kanban,form", "domain": [("project_id", "=", self.id)],
            "context": {"default_project_id": self.id},
        }
