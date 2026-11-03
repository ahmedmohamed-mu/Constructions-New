from dateutil.relativedelta import relativedelta

from odoo import _, api, fields, models
from odoo.exceptions import UserError


class ZcHandover(models.Model):
    """Handover minutes (محضر استلام) signed with the owner and the consultant."""
    _name = "zc.handover"
    _description = "Handover Minutes"
    _inherit = ["mail.thread", "mail.activity.mixin"]
    _order = "date desc, id desc"
    _check_company_auto = True

    name = fields.Char(default=lambda self: _("New"), readonly=True, copy=False)
    kind = fields.Selection([("preliminary", "Preliminary handover"), ("final", "Final handover")],
                            required=True, default="preliminary", tracking=True)
    state = fields.Selection([("draft", "Draft"), ("signed", "Signed"), ("cancel", "Cancelled")],
                             default="draft", required=True, tracking=True, copy=False)
    sale_order_id = fields.Many2one("sale.order", string="Contract", required=True,
                                    domain="[('hom_project_type', '=', 'contracting'), ('state', '=', 'sale')]")
    company_id = fields.Many2one(related="sale_order_id.company_id", store=True)
    currency_id = fields.Many2one(related="sale_order_id.currency_id")
    partner_id = fields.Many2one(related="sale_order_id.partner_id", store=True, string="Owner")
    consultant_id = fields.Many2one(related="sale_order_id.hom_consultant_id", string="Consultant")
    project_id = fields.Many2one("project.project", compute="_compute_project_id", store=True)
    date = fields.Date(required=True, default=fields.Date.context_today, tracking=True)
    dlp_months = fields.Integer(string="Defects liability (months)", default=12,
                                help="Maintenance period between the preliminary and the final handover.")
    dlp_end = fields.Date(string="End of defects liability", compute="_compute_dlp_end", store=True)
    attendees = fields.Text(string="Committee", help="Owner, consultant and contractor representatives.")
    user_id = fields.Many2one("res.users", string="Project Manager", default=lambda self: self.env.user)
    snag_ids = fields.One2many("zc.handover.snag", "handover_id", string="Snag list", copy=True)
    snag_open = fields.Integer(string="Open snags", compute="_compute_snags")
    snag_total = fields.Integer(compute="_compute_snags")
    note = fields.Html(string="Minutes")
    retention_amount = fields.Monetary(string="Retention released", readonly=True, copy=False)
    release_move_ids = fields.Many2many("account.move", string="Release invoices / bills", readonly=True, copy=False)
    guarantee_ids = fields.Many2many("hom.bank.guarantee", string="Guarantees returned", readonly=True, copy=False)

    @api.depends("sale_order_id")
    def _compute_project_id(self):
        for rec in self:
            rec.project_id = rec.sale_order_id.project_ids[:1]

    @api.depends("date", "dlp_months", "kind")
    def _compute_dlp_end(self):
        for rec in self:
            rec.dlp_end = rec.kind == "preliminary" and rec.date and rec.date + relativedelta(months=rec.dlp_months)

    @api.depends("snag_ids.state")
    def _compute_snags(self):
        for rec in self:
            rec.snag_total = len(rec.snag_ids)
            rec.snag_open = len(rec.snag_ids.filtered(lambda s: s.state == "open"))

    @api.onchange("sale_order_id")
    def _onchange_sale_order_id(self):
        if self.sale_order_id:
            self.dlp_months = self.sale_order_id.zc_dlp_months or 12

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if vals.get("name", _("New")) == _("New"):
                vals["name"] = self.env["ir.sequence"].next_by_code("zc.handover") or _("New")
        return super().create(vals_list)

    # ------------------------------------------------------------------
    def action_sign(self):
        for rec in self:
            if rec.state != "draft":
                continue
            order = rec.sale_order_id
            if rec.kind == "preliminary":
                rec._sign_preliminary()
            else:
                rec._sign_final()
            rec.state = "signed"
            order.message_post(body=_("%(kind)s signed: %(ho)s.", kind=rec._kind_label(), ho=rec._get_html_link()))
        return True

    def _kind_label(self):
        return dict(self._fields["kind"]._description_selection(self.env))[self.kind]

    def _sign_preliminary(self):
        order = self.sale_order_id
        if order.zc_prelim_handover_id:
            raise UserError(_("%s already has a signed preliminary handover.", order.name))
        busy = order.hom_certificate_ids.filtered(lambda c: c.state in ("draft", "submitted"))
        if busy:
            raise UserError(_("Finish the certificates in progress first: %s", ", ".join(busy.mapped("name"))))
        order.write({"zc_prelim_handover_id": self.id, "zc_dlp_months": self.dlp_months})
        pct = order.zc_retention_prelim_pct
        if pct and order.hom_retention_balance > 0:
            move = order._zc_release_retention(order.currency_id.round(order.hom_retention_balance * pct / 100.0),
                                               _("Preliminary handover %s", self.name))
            self.write({"retention_amount": move.amount_total, "release_move_ids": [(4, move.id)]})
        # performance guarantees must cover the defects liability period
        short = order.hom_guarantee_ids.filtered(
            lambda g: g.direction == "issued" and g.guarantee_type in ("performance", "retention")
            and g.state in ("active", "extended") and g.expiry_date and g.expiry_date < self.dlp_end)
        for g in short:
            g.activity_schedule("mail.mail_activity_data_warning", user_id=g.responsible_id.id or self.env.uid,
                                summary=_("Extend %s to cover the defects liability period", g.name),
                                note=_("The guarantee expires on %(e)s, the defects liability period ends on %(d)s.",
                                       e=g.expiry_date, d=self.dlp_end))
        if short:
            self.message_post(body=_("Guarantees to extend before the end of the defects liability period: %s",
                                     ", ".join(short.mapped("name"))))
        # the advance should be fully recovered by now
        if order.hom_advance_outstanding > 0:
            self.message_post(body=_("Advance still to recover: %s - recover it in the final account.",
                                     order.currency_id.format(order.hom_advance_outstanding)))
        responsible = self.project_id.user_id or self.user_id
        if responsible:
            self.activity_schedule("mail.mail_activity_data_todo", user_id=responsible.id,
                                   date_deadline=self.dlp_end - relativedelta(days=30),
                                   summary=_("Prepare the final handover of %s", order.name),
                                   note=_("The defects liability period ends on %s: close the snags, invite the "
                                          "consultant and prepare the final account.", self.dlp_end))

    def _sign_final(self):
        order = self.sale_order_id
        prelim = order.zc_prelim_handover_id
        if not prelim:
            raise UserError(_("Sign the preliminary handover of %s first.", order.name))
        open_snags = (prelim.snag_ids | self.snag_ids).filtered(lambda s: s.state == "open")
        if open_snags:
            raise UserError(_("%s snag(s) are still open: close them before the final handover.", len(open_snags)))
        claims = self.env["zc.quantity.dispute"].sudo().search([("sale_order_id", "=", order.id),
                                                                ("state", "in", ("open", "resubmitted"))])
        if claims:
            raise UserError(_("Settle the quantity claims first (agree or accept the cut): %s",
                              ", ".join(claims.mapped("name"))))
        order.write({"zc_final_handover_id": self.id})
        moves = self.env["account.move"]
        if order.hom_retention_balance > 0:
            moves |= order._zc_release_retention(order.hom_retention_balance, _("Final handover %s", self.name))
        retention = sum(moves.mapped("amount_total"))
        # back-to-back: the subcontractors' retention follows the owner's
        for po in order._zc_subcontracts().filtered(lambda p: p.hom_retention_balance > 0
                                                    and not p.hom_retention_release_ids.filtered(lambda m: m.state == "draft")):
            action = po.action_hom_release_retention()
            moves |= self.env["account.move"].browse(action.get("res_id"))
        guarantees = order.hom_guarantee_ids.filtered(lambda g: g.state in ("active", "extended") and (
            g.direction == "issued" and g.guarantee_type in ("performance", "retention")))
        guarantees |= order._zc_subcontracts().mapped("hom_guarantee_ids").filtered(
            lambda g: g.state in ("active", "extended") and g.guarantee_type in ("performance", "retention"))
        if guarantees:
            guarantees.action_release()
            for g in guarantees:
                g.message_post(body=_("Released with the final handover %s.", self.name))
        self.write({"retention_amount": retention, "release_move_ids": [(6, 0, moves.ids)],
                    "guarantee_ids": [(6, 0, guarantees.ids)]})
        accountant = order.company_id.hom_certificate_accountant_id
        for move in moves:
            if accountant:
                move.activity_schedule("mail.mail_activity_data_todo", user_id=accountant.id,
                                       summary=_("Post the retention release %s", move.ref or move.name))

    def action_cancel(self):
        for rec in self:
            if rec.state == "signed" and rec.release_move_ids.filtered(lambda m: m.state == "posted"):
                raise UserError(_("A release invoice of this handover is posted."))
            order = rec.sale_order_id
            if order.zc_prelim_handover_id == rec:
                order.zc_prelim_handover_id = False
            if order.zc_final_handover_id == rec:
                order.zc_final_handover_id = False
            rec.release_move_ids.filtered(lambda m: m.state == "draft").unlink()
            rec.state = "cancel"

    def action_draft(self):
        self.filtered(lambda r: r.state == "cancel").write({"state": "draft"})

    def action_print_final_account(self):
        self.ensure_one()
        return self.env.ref("zc_contract_closeout.action_report_final_account").report_action(self.sale_order_id)


class ZcHandoverSnag(models.Model):
    _name = "zc.handover.snag"
    _description = "Snag (handover remark)"
    _order = "state desc, due_date, id"

    handover_id = fields.Many2one("zc.handover", required=True, ondelete="cascade", index=True)
    sale_order_id = fields.Many2one(related="handover_id.sale_order_id", store=True)
    name = fields.Char(string="Remark", required=True)
    location = fields.Char()
    trade = fields.Char()
    subcontractor_id = fields.Many2one("res.partner", string="By subcontractor",
                                       help="Remedied by (and at the cost of) this subcontractor.")
    user_id = fields.Many2one("res.users", string="Follow-up")
    due_date = fields.Date()
    state = fields.Selection([("open", "Open"), ("done", "Remedied")], default="open", required=True)
    done_date = fields.Date(readonly=True)

    def action_done(self):
        self.write({"state": "done", "done_date": fields.Date.context_today(self)})

    def action_reopen(self):
        self.write({"state": "open", "done_date": False})
