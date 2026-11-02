from odoo import Command, _, api, fields, models
from odoo.exceptions import UserError

CRITERIA = [
    ("experience", "Experience in this type of work"),
    ("capacity", "Free capacity of our teams and equipment"),
    ("client", "Client: payment record and relationship"),
    ("schedule", "Realistic duration"),
    ("funding", "Funding need versus our cash"),
    ("risk", "Contract risks versus the expected return"),
]


class CrmLead(models.Model):
    _inherit = "crm.lead"

    zc_is_tender = fields.Boolean(string="Tender")
    zc_tender_ref = fields.Char(string="Tender reference")
    zc_tender_type = fields.Selection([("public", "Public tender"), ("limited", "Limited tender"),
                                       ("direct", "Direct invitation")], string="Tender type", default="limited")
    zc_pricing_basis = fields.Selection([("remeasure", "Remeasurement (BOQ x rates)"), ("lump_sum", "Lump sum"),
                                         ("cost_plus", "Cost plus fee")], string="Pricing basis", default="remeasure")
    zc_submission_deadline = fields.Date(string="Submission deadline", tracking=True)
    zc_site_visit_date = fields.Date(string="Site visit")
    zc_clarification_deadline = fields.Date(string="Last day for clarifications")
    zc_duration_months = fields.Integer(string="Duration (months)")
    zc_estimated_cost = fields.Monetary(string="Estimated cost", currency_field="company_currency")
    zc_expected_margin = fields.Monetary(string="Expected margin", compute="_compute_zc_margin",
                                         currency_field="company_currency")
    zc_expected_margin_pct = fields.Float(string="Expected margin %", compute="_compute_zc_margin")
    zc_peak_funding = fields.Monetary(string="Peak funding need", currency_field="company_currency",
                                      help="Cash the project needs before the owner's payments cover the costs.")
    zc_bid_bond_amount = fields.Monetary(string="Bid bond", currency_field="company_currency")
    zc_bid_bond_id = fields.Many2one("hom.bank.guarantee", string="Bid bond issued", copy=False)
    zc_criterion_ids = fields.One2many("zc.tender.criterion", "lead_id", string="Bid / No-Bid assessment")
    zc_score = fields.Float(string="Score %", compute="_compute_zc_score")
    zc_decision = fields.Selection([("open", "To decide"), ("bid", "Bid"), ("no_bid", "No bid")],
                                   string="Decision", default="open", tracking=True, copy=False)
    zc_decision_by_id = fields.Many2one("res.users", string="Decided by", readonly=True, copy=False)
    zc_decision_date = fields.Date(readonly=True, copy=False)
    zc_decision_note = fields.Text(string="Decision reasoning")
    zc_clarification_ids = fields.One2many("zc.tender.clarification", "lead_id", string="Clarifications & addenda")
    zc_days_left = fields.Integer(string="Days to submission", compute="_compute_zc_days_left")

    @api.depends("expected_revenue", "zc_estimated_cost")
    def _compute_zc_margin(self):
        for lead in self:
            margin = (lead.expected_revenue - lead.zc_estimated_cost) if lead.zc_estimated_cost else 0.0
            lead.zc_expected_margin = margin
            lead.zc_expected_margin_pct = (margin / lead.expected_revenue * 100.0) if lead.expected_revenue and \
                lead.zc_estimated_cost else 0.0

    @api.depends("zc_criterion_ids.score")
    def _compute_zc_score(self):
        for lead in self:
            rows = lead.zc_criterion_ids
            lead.zc_score = (sum(rows.mapped("score")) / (5.0 * len(rows)) * 100.0) if rows else 0.0

    def _compute_zc_days_left(self):
        today = fields.Date.context_today(self)
        for lead in self:
            lead.zc_days_left = (lead.zc_submission_deadline - today).days if lead.zc_submission_deadline else 0

    @api.onchange("zc_is_tender")
    def _onchange_zc_is_tender(self):
        if self.zc_is_tender and not self.zc_criterion_ids:
            self.zc_criterion_ids = [Command.create({"criterion": c}) for c, _label in CRITERIA]

    def action_zc_load_criteria(self):
        for lead in self.filtered(lambda l: not l.zc_criterion_ids):
            lead.zc_criterion_ids = [Command.create({"criterion": c}) for c, _label in CRITERIA]

    def _zc_check_decider(self):
        if not (self.env.user.has_group("sales_team.group_sale_manager")):
            raise UserError(_("The Bid / No-Bid decision is taken by management (Sales manager rights)."))

    def action_zc_bid(self):
        self._zc_check_decider()
        for lead in self:
            unscored = lead.zc_criterion_ids.filtered(lambda c: not c.score)
            if not lead.zc_criterion_ids or unscored:
                raise UserError(_("Score every criterion of the assessment before deciding."))
            lead.write({"zc_decision": "bid", "zc_decision_by_id": self.env.uid,
                        "zc_decision_date": fields.Date.context_today(self)})
            lead.message_post(body=_("Decision: BID (score %(s).0f%%). %(n)s", s=lead.zc_score,
                                     n=lead.zc_decision_note or ""))

    def action_zc_no_bid(self):
        self._zc_check_decider()
        for lead in self:
            if not lead.zc_decision_note:
                raise UserError(_("Write why we do not bid: it is the memory for the next tender of this client."))
            lead.write({"zc_decision": "no_bid", "zc_decision_by_id": self.env.uid,
                        "zc_decision_date": fields.Date.context_today(self)})
            lead.message_post(body=_("Decision: NO BID. %s", lead.zc_decision_note))
            lead.action_set_lost()

    def action_zc_bid_bond(self):
        """Issue the bid bond from the tender (letter of guarantee register)."""
        self.ensure_one()
        if not self.zc_bid_bond_amount:
            raise UserError(_("Enter the bid bond amount."))
        if not self.zc_bid_bond_id:
            self.zc_bid_bond_id = self.env["hom.bank.guarantee"].create({
                "name": _("Bid bond %s", self.zc_tender_ref or self.name), "direction": "issued",
                "guarantee_type": "bid", "partner_id": self.partner_id.id, "amount": self.zc_bid_bond_amount,
                "expiry_date": fields.Date.add(self.zc_submission_deadline or fields.Date.context_today(self), days=90)})
        return {"type": "ir.actions.act_window", "res_model": "hom.bank.guarantee", "res_id": self.zc_bid_bond_id.id,
                "view_mode": "form"}

    def action_sale_quotations_new(self):
        for lead in self.filtered("zc_is_tender"):
            if lead.zc_decision != "bid":
                raise UserError(_("Take the Bid / No-Bid decision before pricing the tender."))
        return super().action_sale_quotations_new()


class ZcTenderCriterion(models.Model):
    _name = "zc.tender.criterion"
    _description = "Bid / No-Bid Criterion"
    _order = "id"

    lead_id = fields.Many2one("crm.lead", required=True, ondelete="cascade", index=True)
    criterion = fields.Selection(CRITERIA, required=True)
    score = fields.Integer(help="1 = poor ... 5 = excellent")
    note = fields.Char()

    _score_range = models.Constraint("CHECK(score >= 0 AND score <= 5)", "Scores go from 1 to 5.")


class ZcTenderClarification(models.Model):
    _name = "zc.tender.clarification"
    _description = "Tender Clarification / Addendum"
    _order = "date, id"

    lead_id = fields.Many2one("crm.lead", required=True, ondelete="cascade", index=True)
    date = fields.Date(default=fields.Date.context_today, required=True)
    question = fields.Text(required=True)
    answer = fields.Text()
    is_addendum = fields.Boolean(string="Addendum (changes the tender)")
    reference = fields.Char(string="Letter / addendum ref.")
    price_impact = fields.Boolean(string="Affects our price")
