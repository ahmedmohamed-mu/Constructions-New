from odoo import Command, _, api, fields, models
from odoo.exceptions import UserError, ValidationError


class ZcClaim(models.Model):
    _inherit = "zc.claim"

    claimant = fields.Selection([("us", "Our claim to the owner"), ("sub", "Subcontractor claim to us")],
                                default="us", required=True, tracking=True)
    subcontract_id = fields.Many2one("purchase.order", string="Subcontract",
                                     domain="[('hom_is_subcontract', '=', True), ('state', 'in', ('purchase', 'done'))]")
    sub_partner_id = fields.Many2one(related="subcontract_id.partner_id", string="Subcontractor")
    owner_claim_id = fields.Many2one("zc.claim", string="Passed to the owner as", copy=False,
                                     domain="[('claimant', '=', 'us')]")
    sub_claim_ids = fields.One2many("zc.claim", "owner_claim_id", string="Subcontractor claims behind it")
    bill_id = fields.Many2one("account.move", string="Settlement bill", readonly=True, copy=False)

    @api.constrains("claimant", "subcontract_id")
    def _check_sub(self):
        for rec in self:
            if rec.claimant == "sub" and not rec.subcontract_id:
                raise ValidationError(_("A subcontractor claim needs its subcontract."))

    def action_pass_to_owner(self):
        """Back-to-back: when the event is the owner's responsibility, our own claim to the owner is opened with
        the same event date, so our notice clock starts from the subcontractor's facts."""
        self.ensure_one()
        if self.claimant != "sub":
            raise UserError(_("Only a subcontractor claim is passed to the owner."))
        if not self.owner_claim_id:
            self.owner_claim_id = self.create({
                "claimant": "us", "sale_order_id": self.sale_order_id.id, "title": self.title,
                "event_date": self.event_date, "cause": self.cause, "claim_type": self.claim_type,
                "days_claimed": self.days_claimed, "amount_claimed": self.amount_claimed,
                "description": self.description})
            self.owner_claim_id.message_post(body=_("Opened from the subcontractor claim %s.", self.name))
        return {"type": "ir.actions.act_window", "res_model": "zc.claim", "res_id": self.owner_claim_id.id,
                "view_mode": "form"}

    def action_create_invoice(self):
        if self.claimant != "sub":
            return super().action_create_invoice()
        self.ensure_one()
        if self.state not in ("agreed", "partial") or not self.amount_granted:
            raise UserError(_("Only an agreed amount is billed."))
        if self.bill_id and self.bill_id.state != "cancel":
            raise UserError(_("Already billed: %s", self.bill_id.name))
        product = self.company_id.zc_claim_product_id
        analytic = self.project_id.account_id
        line = {"product_id": product.id, "name": "%s - %s" % (self.name, self.title), "quantity": 1,
                "price_unit": self.amount_granted, "tax_ids": [Command.clear()]}
        if analytic:
            line["analytic_distribution"] = {str(analytic.id): 100}
        self.bill_id = self.env["account.move"].create({
            "move_type": "in_invoice", "partner_id": self.sub_partner_id.id, "invoice_origin": self.subcontract_id.name,
            "ref": _("Claim %s", self.name), "invoice_date": fields.Date.context_today(self),
            "invoice_line_ids": [Command.create(line)]})
        return self.bill_id._get_records_action()

    def _apply_eot(self):
        if self.claimant == "sub":
            # time granted to a subcontractor does not move the contract completion date
            self.eot_applied = True
            return
        return super()._apply_eot()
