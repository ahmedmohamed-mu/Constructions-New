from datetime import timedelta

from odoo import Command, _, api, fields, models
from odoo.exceptions import UserError


class ZcClaim(models.Model):
    """Contractor claim: extension of time and / or additional cost, with its contractual clock."""
    _name = "zc.claim"
    _description = "Contractor Claim (EOT / cost)"
    _inherit = ["mail.thread", "mail.activity.mixin"]
    _order = "event_date desc, id desc"
    _check_company_auto = True

    name = fields.Char(default=lambda self: _("New"), readonly=True, copy=False)
    title = fields.Char(string="Event", required=True, tracking=True)
    sale_order_id = fields.Many2one("sale.order", string="Contract", required=True,
                                    domain="[('hom_project_type', '=', 'contracting'), ('state', '=', 'sale')]")
    company_id = fields.Many2one(related="sale_order_id.company_id", store=True)
    currency_id = fields.Many2one(related="sale_order_id.currency_id")
    partner_id = fields.Many2one(related="sale_order_id.partner_id", store=True, string="Owner")
    consultant_id = fields.Many2one(related="sale_order_id.hom_consultant_id", string="Consultant")
    project_id = fields.Many2one("project.project", compute="_compute_project_id", store=True)
    user_id = fields.Many2one("res.users", string="Claims engineer", default=lambda self: self.env.user, tracking=True)
    claim_type = fields.Selection([("eot", "Extension of time"), ("cost", "Additional cost"),
                                   ("both", "Time and cost")], required=True, default="both", tracking=True)
    cause = fields.Selection([
        ("site", "Late site access / handover"), ("drawings", "Late drawings / approvals"),
        ("instruction", "Owner / consultant instruction"), ("variation", "Variation order"),
        ("suspension", "Suspension of work"), ("payment", "Late payment"),
        ("force_majeure", "Force majeure / exceptional event"), ("other", "Other"),
    ], required=True, default="drawings")
    variation_id = fields.Many2one("hom.variation.order", string="Related variation",
                                   domain="[('sale_order_id', '=', sale_order_id)]")
    state = fields.Selection([
        ("event", "Event recorded"), ("notified", "Notice sent"), ("submitted", "Detailed claim submitted"),
        ("agreed", "Agreed"), ("partial", "Partially agreed"), ("rejected", "Rejected"),
        ("dispute", "Dispute (NOD)"), ("withdrawn", "Withdrawn"),
    ], default="event", required=True, tracking=True, copy=False)
    description = fields.Html(string="Facts & entitlement")
    event_date = fields.Date(string="Event / aware on", required=True, default=fields.Date.context_today, tracking=True)
    notice_deadline = fields.Date(compute="_compute_deadlines", store=True)
    notice_date = fields.Date(string="Notice sent on", tracking=True, copy=False)
    notice_ref = fields.Char(string="Notice letter ref.", copy=False)
    detail_deadline = fields.Date(string="Detailed claim due", compute="_compute_deadlines", store=True)
    submission_date = fields.Date(string="Detailed claim on", tracking=True, copy=False)
    answer_deadline = fields.Date(string="Engineer answer due", compute="_compute_deadlines", store=True)
    determination_date = fields.Date(string="Determination on", tracking=True, copy=False)
    determination_ref = fields.Char(copy=False)
    nod_deadline = fields.Date(string="Dissatisfaction notice due", compute="_compute_deadlines", store=True)
    days_claimed = fields.Integer(tracking=True)
    days_granted = fields.Integer(tracking=True, copy=False)
    amount_claimed = fields.Monetary(tracking=True)
    amount_granted = fields.Monetary(tracking=True, copy=False)
    next_deadline = fields.Date(compute="_compute_next", store=True)
    next_step = fields.Char(compute="_compute_next", store=True)
    time_barred = fields.Boolean(compute="_compute_next", store=True,
                                 help="The contractual notice period passed without notice: entitlement may be lost.")
    invoice_id = fields.Many2one("account.move", readonly=True, copy=False)
    eot_applied = fields.Boolean(readonly=True, copy=False)

    @api.depends("sale_order_id")
    def _compute_project_id(self):
        for rec in self:
            rec.project_id = rec.sale_order_id.project_ids[:1]

    @api.depends("event_date", "submission_date", "determination_date", "company_id.zc_claim_notice_days",
                 "company_id.zc_claim_detail_days", "company_id.zc_claim_answer_days", "company_id.zc_claim_nod_days")
    def _compute_deadlines(self):
        for rec in self:
            c = rec.company_id or self.env.company
            ev = rec.event_date
            rec.notice_deadline = ev and ev + timedelta(days=c.zc_claim_notice_days or 28)
            rec.detail_deadline = ev and ev + timedelta(days=c.zc_claim_detail_days or 84)
            rec.answer_deadline = rec.submission_date and rec.submission_date + timedelta(days=c.zc_claim_answer_days or 42)
            rec.nod_deadline = rec.determination_date and rec.determination_date + timedelta(days=c.zc_claim_nod_days or 28)

    @api.depends("state", "notice_deadline", "detail_deadline", "answer_deadline", "nod_deadline", "notice_date")
    def _compute_next(self):
        for rec in self:
            step, deadline = False, False
            if rec.state == "event":
                step, deadline = _("Send the notice of claim"), rec.notice_deadline
            elif rec.state == "notified":
                step, deadline = _("Submit the detailed claim"), rec.detail_deadline
            elif rec.state == "submitted":
                step, deadline = _("Engineer's agreement / determination"), rec.answer_deadline
            elif rec.state in ("partial", "rejected"):
                step, deadline = _("Accept or send a notice of dissatisfaction"), rec.nod_deadline
            rec.next_step, rec.next_deadline = step, deadline
            rec.time_barred = bool(rec.notice_deadline and (
                (rec.notice_date and rec.notice_date > rec.notice_deadline)
                or (not rec.notice_date and rec.state == "event" and rec.notice_deadline < fields.Date.context_today(rec))))

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if vals.get("name", _("New")) == _("New"):
                vals["name"] = self.env["ir.sequence"].next_by_code("zc.claim") or _("New")
        return super().create(vals_list)

    # ------------------------------------------------------------------
    def action_notify(self):
        for rec in self.filtered(lambda r: r.state == "event"):
            rec.write({"state": "notified", "notice_date": rec.notice_date or fields.Date.context_today(self)})
            if rec.time_barred:
                rec.message_post(body=_("Notice sent after the contractual period (%s): the claim may be time-barred.",
                                        rec.notice_deadline))

    def action_submit(self):
        for rec in self.filtered(lambda r: r.state == "notified"):
            if rec.claim_type in ("eot", "both") and not rec.days_claimed:
                raise UserError(_("Enter the days claimed."))
            if rec.claim_type in ("cost", "both") and not rec.amount_claimed:
                raise UserError(_("Enter the amount claimed."))
            rec.write({"state": "submitted", "submission_date": rec.submission_date or fields.Date.context_today(self)})
            if rec.user_id:
                rec.activity_schedule("mail.mail_activity_data_todo", user_id=rec.user_id.id,
                                      date_deadline=rec.answer_deadline,
                                      summary=_("Follow up the engineer's answer on %s", rec.name))

    def _determine(self, state):
        if state in ("agreed", "partial"):
            for rec in self.filtered(lambda r: r.state == "submitted"):
                days = rec.days_granted or (rec.days_claimed if state == "agreed" else 0)
                if days and rec.claim_type in ("eot", "both") and rec.project_id and not rec.project_id.date:
                    raise UserError(_(
                        "Project %s has no planned completion date, so the %s days of extension cannot be added. "
                        "Set the project start and end dates first, then record the engineer's determination.",
                        rec.project_id.display_name, days))
        for rec in self.filtered(lambda r: r.state == "submitted"):
            vals = {"state": state, "determination_date": rec.determination_date or fields.Date.context_today(self)}
            if state == "agreed":
                vals.update({"days_granted": rec.days_granted or rec.days_claimed,
                             "amount_granted": rec.amount_granted or rec.amount_claimed})
            if state == "rejected":
                vals.update({"days_granted": 0, "amount_granted": 0.0})
            rec.write(vals)
            rec.activity_feedback(["mail.mail_activity_data_todo"])
            if state in ("agreed", "partial") and rec.days_granted:
                rec._apply_eot()

    def action_agree(self):
        self._determine("agreed")

    def action_partial(self):
        for rec in self:
            if not (rec.days_granted or rec.amount_granted):
                raise UserError(_("Enter the days / amount granted by the engineer."))
        self._determine("partial")

    def action_reject(self):
        self._determine("rejected")

    def action_dispute(self):
        for rec in self.filtered(lambda r: r.state in ("partial", "rejected")):
            rec.state = "dispute"
            rec.message_post(body=_("Notice of dissatisfaction sent: the determination is referred to the dispute board."))

    def action_withdraw(self):
        self.write({"state": "withdrawn"})

    def _apply_eot(self):
        """Extend the project completion date and warn about guarantees that no longer cover it."""
        self.ensure_one()
        if self.eot_applied or not self.days_granted:
            return
        project = self.project_id
        if project and project.date:
            old = project.date
            project.date = old + timedelta(days=self.days_granted)
            project.message_post(body=_("Completion extended by %(d)s days by %(c)s: %(o)s -> %(n)s.",
                                        d=self.days_granted, c=self._get_html_link(), o=old, n=project.date))
            dlp = self.sale_order_id.zc_dlp_months or 12
            short = self.sale_order_id.hom_guarantee_ids.filtered(
                lambda g: g.direction == "issued" and g.guarantee_type in ("performance", "advance")
                and g.state in ("active", "extended") and g.expiry_date and g.expiry_date < project.date)
            for g in short:
                g.activity_schedule("mail.mail_activity_data_warning", user_id=g.responsible_id.id or self.env.uid,
                                    summary=_("Extend %s after the extension of time", g.name),
                                    note=_("New completion date %(d)s; defects liability %(m)s months after.",
                                           d=project.date, m=dlp))
        self.eot_applied = True

    def action_create_invoice(self):
        self.ensure_one()
        if self.state not in ("agreed", "partial") or not self.amount_granted:
            raise UserError(_("Only an agreed amount is invoiced."))
        if self.invoice_id and self.invoice_id.state != "cancel":
            raise UserError(_("Already invoiced: %s", self.invoice_id.name))
        product = self.company_id.zc_claim_product_id
        if not product:
            raise UserError(_("Set the claim settlement product (code ZC-CLM)."))
        order = self.sale_order_id
        analytic = self.project_id.account_id
        line = {"product_id": product.id, "name": "%s - %s" % (self.name, self.title), "quantity": 1,
                "price_unit": self.amount_granted,
                "tax_ids": [Command.set(product.taxes_id.filtered(lambda t: t.company_id == self.company_id).ids)]}
        if analytic:
            line["analytic_distribution"] = {str(analytic.id): 100}
        move = self.env["account.move"].create({
            "move_type": "out_invoice", "partner_id": order.partner_invoice_id.id, "invoice_origin": order.name,
            "ref": _("Claim %s", self.name), "invoice_line_ids": [Command.create(line)]})
        self.invoice_id = move
        return move._get_records_action()

    def action_view_invoice(self):
        return self.invoice_id._get_records_action()
