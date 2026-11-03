from odoo import _, api, fields, models
from odoo.exceptions import UserError


class ZcDailyReport(models.Model):
    _name = "zc.daily.report"
    _description = "Site Daily Report"
    _inherit = ["mail.thread", "mail.activity.mixin"]
    _order = "date desc, id desc"

    name = fields.Char(default=lambda self: _("New"), readonly=True, copy=False)
    sale_order_id = fields.Many2one("sale.order", string="Contract", required=True,
                                    domain="[('state', '=', 'sale'), ('hom_project_type', '=', 'contracting')]",
                                    tracking=True)
    project_id = fields.Many2one("project.project", compute="_compute_project_id", store=True)
    company_id = fields.Many2one(related="sale_order_id.company_id", store=True)
    date = fields.Date(required=True, default=fields.Date.context_today, tracking=True)
    user_id = fields.Many2one("res.users", string="Site engineer", default=lambda self: self.env.user, tracking=True)
    state = fields.Selection([("draft", "Draft"), ("submitted", "Submitted"), ("approved", "Approved by PM")],
                             default="draft", tracking=True, copy=False)
    weather = fields.Selection([("normal", "Normal"), ("hot", "Extreme heat"), ("rain", "Rain"),
                                ("wind", "Wind / sand storm")], default="normal")
    work_ids = fields.One2many("zc.daily.report.work", "report_id", string="Work done", copy=True)
    labour_ids = fields.One2many("zc.daily.report.labour", "report_id", string="Labour", copy=True)
    equipment_ids = fields.One2many("zc.daily.report.equipment", "report_id", string="Equipment", copy=True)
    labour_total = fields.Integer(string="Workers on site", compute="_compute_totals", store=True)
    instructions = fields.Text(string="Instructions received on site")
    delays = fields.Text(string="Obstacles & delays",
                         help="Facts written on the day: the evidence of any later claim.")
    delay_hours = fields.Float(string="Hours lost")
    delay_cause = fields.Selection(lambda self: self.env["zc.claim"]._fields["cause"].selection,
                                   string="Cause of delay")
    claim_id = fields.Many2one("zc.claim", string="Claim opened", readonly=True, copy=False)
    measurement_count = fields.Integer(compute="_compute_measurement_count")

    _unique_day = models.Constraint("unique(sale_order_id, date)", "One daily report per contract and day.")

    @api.depends("sale_order_id")
    def _compute_project_id(self):
        for rec in self:
            rec.project_id = rec.sale_order_id.project_ids[:1]

    @api.depends("labour_ids.count")
    def _compute_totals(self):
        for rec in self:
            rec.labour_total = sum(rec.labour_ids.mapped("count"))

    @api.depends("work_ids.measurement_id")
    def _compute_measurement_count(self):
        for rec in self:
            rec.measurement_count = len(rec.work_ids.measurement_id)

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if vals.get("name", _("New")) == _("New"):
                vals["name"] = self.env["ir.sequence"].next_by_code("zc.daily.report") or _("New")
        return super().create(vals_list)

    def action_submit(self):
        for rec in self:
            if not (rec.work_ids or rec.labour_ids or rec.delays):
                raise UserError(_("Record the work done, the labour or the obstacles of the day first."))
            rec._create_measurements()
            rec.state = "submitted"
            managers = rec.project_id.user_id
            if managers:
                rec.activity_schedule("mail.mail_activity_data_todo", user_id=managers.id,
                                      summary=_("Review the site daily report %s", rec.name))

    def action_approve(self):
        self.filtered(lambda r: r.state == "submitted").write({"state": "approved"})
        self.activity_feedback(["mail.mail_activity_data_todo"])

    def action_draft(self):
        for rec in self:
            if rec.work_ids.measurement_id.filtered(lambda m: m.state != "open"):
                raise UserError(_("Quantities of this report are already in a certificate."))
        self.write({"state": "draft"})

    def _create_measurements(self):
        """The quantities of the day become site measurements once: the certificate reads them from there."""
        Measurement = self.env["hom.site.measurement"]
        for line in self.work_ids.filtered(lambda w: w.sale_line_id and w.quantity and not w.measurement_id):
            vals = {"sale_line_id": line.sale_line_id.id, "quantity": line.quantity, "date": self.date,
                    "location": line.location or "", "note": _("Daily report %s", self.name)}
            if "zc_stage" in Measurement._fields and line.stage:
                vals["zc_stage"] = line.stage
            line.measurement_id = Measurement.create(vals)

    def action_open_claim(self):
        """Turn the obstacles of the day into a claim event: the notice clock starts from this date."""
        self.ensure_one()
        if not self.delays:
            raise UserError(_("Describe the obstacle first."))
        if not self.claim_id:
            self.claim_id = self.env["zc.claim"].create({
                "title": (self.delays or "").splitlines()[0][:120], "sale_order_id": self.sale_order_id.id,
                "event_date": self.date, "cause": self.delay_cause or "other", "claim_type": "eot",
                "description": _("From site daily report %(r)s: %(t)s (hours lost: %(h)s)",
                                 r=self.name, t=self.delays, h=self.delay_hours)})
        return {"type": "ir.actions.act_window", "res_model": "zc.claim", "res_id": self.claim_id.id,
                "view_mode": "form"}

    def action_view_measurements(self):
        return {"type": "ir.actions.act_window", "name": _("Site Measurements"), "res_model": "hom.site.measurement",
                "view_mode": "list,form", "domain": [("id", "in", self.work_ids.measurement_id.ids)]}


class ZcDailyReportWork(models.Model):
    _name = "zc.daily.report.work"
    _description = "Daily Report - Work Done"

    report_id = fields.Many2one("zc.daily.report", required=True, ondelete="cascade", index=True)
    sale_order_id = fields.Many2one(related="report_id.sale_order_id")
    sale_line_id = fields.Many2one("sale.order.line", string="BOQ item",
                                   domain="[('order_id', '=', sale_order_id), ('display_type', '=', False), "
                                          "('is_downpayment', '=', False)]")
    location = fields.Char(string="Zone / floor")
    quantity = fields.Float(digits="Product Unit")
    product_uom_id = fields.Many2one(related="sale_line_id.product_uom_id", string="Unit")
    stage = fields.Selection([("installed", "Installed"), ("supplied", "Supplied only")], default="installed")
    partner_id = fields.Many2one("res.partner", string="Done by (subcontractor)")
    note = fields.Char()
    measurement_id = fields.Many2one("hom.site.measurement", readonly=True, copy=False)


class ZcDailyReportLabour(models.Model):
    _name = "zc.daily.report.labour"
    _description = "Daily Report - Labour"

    report_id = fields.Many2one("zc.daily.report", required=True, ondelete="cascade", index=True)
    trade = fields.Char(required=True)
    partner_id = fields.Many2one("res.partner", string="Subcontractor (empty = own)")
    count = fields.Integer(string="Workers")
    hours = fields.Float(default=8.0)


class ZcDailyReportEquipment(models.Model):
    _name = "zc.daily.report.equipment"
    _description = "Daily Report - Equipment"

    report_id = fields.Many2one("zc.daily.report", required=True, ondelete="cascade", index=True)
    equipment_id = fields.Many2one("maintenance.equipment", string="Equipment")
    name = fields.Char(string="Description")
    hours_worked = fields.Float()
    hours_idle = fields.Float(string="Idle / breakdown hours")
