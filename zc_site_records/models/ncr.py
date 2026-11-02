from datetime import timedelta

from odoo import _, api, fields, models
from odoo.exceptions import UserError


class ZcNcr(models.Model):
    _name = "zc.ncr"
    _description = "Non-Conformance Report"
    _inherit = ["mail.thread", "mail.activity.mixin"]
    _order = "date desc, id desc"

    name = fields.Char(default=lambda self: _("New"), readonly=True, copy=False)
    title = fields.Char(required=True, tracking=True)
    sale_order_id = fields.Many2one("sale.order", string="Contract", required=True, check_company=True,
                                    domain="[('state', '=', 'sale'), ('hom_project_type', '=', 'contracting')]")
    project_id = fields.Many2one("project.project", compute="_compute_project_id", store=True)
    company_id = fields.Many2one(related="sale_order_id.company_id", store=True)
    currency_id = fields.Many2one(related="sale_order_id.currency_id")
    sale_line_id = fields.Many2one("sale.order.line", string="BOQ item",
                                   domain="[('order_id', '=', sale_order_id), ('display_type', '=', False)]")
    location = fields.Char(string="Zone / floor")
    wir_id = fields.Many2one("zc.inspection.request", string="From inspection")
    source = fields.Selection([("internal", "Our quality control"), ("consultant", "Consultant"),
                               ("owner", "Owner")], default="internal")
    date = fields.Date(default=fields.Date.context_today, required=True)
    user_id = fields.Many2one("res.users", string="Raised by", default=lambda self: self.env.user)
    description = fields.Html(required=True)
    responsible = fields.Selection([("own", "Our team"), ("subcontractor", "Subcontractor"),
                                    ("supplier", "Supplier")], default="own", required=True)
    partner_id = fields.Many2one("res.partner", string="Responsible party")
    subcontract_id = fields.Many2one("purchase.order", string="Subcontract",
                                     domain="[('hom_is_subcontract', '=', True), ('partner_id', '=', partner_id)]")
    root_cause = fields.Text()
    corrective_action = fields.Text()
    due_date = fields.Date(string="Correct by", compute="_compute_due", store=True, readonly=False)
    state = fields.Selection([("open", "Open"), ("action", "Corrective action done"), ("closed", "Verified & closed"),
                              ("cancel", "Cancelled")], default="open", tracking=True, copy=False)
    verified_by_id = fields.Many2one("res.users", readonly=True, copy=False)
    backcharge_amount = fields.Monetary(string="Back-charge to responsible party", tracking=True)
    deduction_id = fields.Many2one("zc.certificate.deduction", string="Charged in", readonly=True, copy=False)

    @api.depends("sale_order_id")
    def _compute_project_id(self):
        for rec in self:
            rec.project_id = rec.sale_order_id.project_ids[:1]

    @api.depends("date", "company_id.zc_ncr_close_days")
    def _compute_due(self):
        for rec in self:
            rec.due_date = rec.date and rec.date + timedelta(days=rec.company_id.zc_ncr_close_days or 14)

    @api.onchange("subcontract_id")
    def _onchange_subcontract(self):
        if self.subcontract_id:
            self.partner_id = self.subcontract_id.partner_id
            self.responsible = "subcontractor"

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if vals.get("name", _("New")) == _("New"):
                vals["name"] = self.env["ir.sequence"].next_by_code("zc.ncr") or _("New")
        return super().create(vals_list)

    def action_done(self):
        for rec in self:
            if not rec.corrective_action:
                raise UserError(_("Describe the corrective action taken."))
            rec.state = "action"
            rec.activity_schedule("mail.mail_activity_data_todo", user_id=rec.user_id.id,
                                  summary=_("Verify the correction of %s", rec.name))

    def action_verify(self):
        for rec in self:
            if rec.state != "action":
                raise UserError(_("The corrective action must be done before verification."))
            rec.write({"state": "closed", "verified_by_id": self.env.uid})
            rec.activity_feedback(["mail.mail_activity_data_todo"])
            if rec.backcharge_amount and rec.subcontract_id:
                rec._charge_subcontractor()

    def action_cancel(self):
        self.write({"state": "cancel"})

    def action_reopen(self):
        self.write({"state": "open"})

    def _charge_subcontractor(self):
        """The cost of the correction goes on the subcontractor's next certificate as a back-charge."""
        self.ensure_one()
        if self.deduction_id:
            return
        dtype = self.env["zc.deduction.type"].search([("company_id", "=", self.company_id.id), ("code", "=", "BCK")],
                                                     limit=1)
        cert = self.env["hom.subcontract.certificate"].search([("purchase_order_id", "=", self.subcontract_id.id),
                                                               ("state", "=", "draft")], limit=1)
        if not dtype or not cert:
            self.message_post(body=_("Back-charge of %(a)s to be deducted on the next certificate of %(s)s.",
                                     a=self.backcharge_amount, s=self.subcontract_id.name))
            return
        self.deduction_id = self.env["zc.certificate.deduction"].create({
            "sub_cert_id": cert.id, "type_id": dtype.id, "amount": self.backcharge_amount,
            "name": self.title, "reference": self.name})
        self.message_post(body=_("Back-charge added to %s.", cert.name))


class ZcInspectionRequest(models.Model):
    _inherit = "zc.inspection.request"

    def action_zc_raise_ncr(self):
        self.ensure_one()
        line = self.measurement_ids[:1].sale_line_id
        ncr = self.env["zc.ncr"].create({
            "title": _("Rejected inspection %s", self.name), "sale_order_id": self.sale_order_id.id,
            "wir_id": self.id, "source": "consultant", "sale_line_id": line.id if line else False,
            "location": self.location,
            "description": self.consultant_comments or _("Work rejected at inspection %s.", self.name)})
        return {"type": "ir.actions.act_window", "res_model": "zc.ncr", "res_id": ncr.id, "view_mode": "form"}
