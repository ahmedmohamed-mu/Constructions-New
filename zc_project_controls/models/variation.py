from odoo import Command, _, api, fields, models
from odoo.exceptions import UserError


class HomVariationOrder(models.Model):
    """Owner side of a variation (CRVO): instruction letter -> impact statement -> submitted to the owner's
    consultant -> negotiation -> rate agreed.

    The work may start once the variation is approved internally: its items are added to the contract as
    *provisional* (they can be measured on site) but stay out of the owner certificates until the rate is
    agreed. On agreement the agreed rates replace the proposed ones and the variation is applied.
    """
    _inherit = "hom.variation.order"

    # the company is related to the contract: checking the contract against it on a new record (no company yet)
    # left the contract list empty in the form
    sale_order_id = fields.Many2one(check_company=False)
    zc_requires_agreement = fields.Boolean(
        string="Owner Agreement Required", tracking=True,
        default=lambda self: self.env.company.zc_vo_owner_agreement,
        help="The rate must be agreed with the owner's consultant before the items can be certified.")
    zc_stage = fields.Selection([
        ("instruction", "Instruction received"), ("impact", "Impact statement"),
        ("submitted", "Submitted to consultant"), ("negotiation", "Negotiation"), ("agreed", "Rate agreed"),
    ], string="Owner Stage", default="instruction", tracking=True, copy=False)
    zc_instruction_date = fields.Date(string="Instruction Date")
    zc_time_impact_days = fields.Integer(string="Time Impact (days)", tracking=True)
    zc_submitted_date = fields.Date(string="Submitted to Consultant", copy=False)
    zc_proposed_amount = fields.Monetary(string="Proposed Amount", readonly=True, copy=False)
    zc_agreed_amount = fields.Monetary(string="Agreed Amount", readonly=True, copy=False)
    zc_agreed_date = fields.Date(string="Agreement Date", readonly=True, copy=False)
    zc_negotiation_note = fields.Html(string="Negotiation Notes")
    zc_quote_ids = fields.Many2many("ir.attachment", string="Supporting Quotes")
    zc_provisional = fields.Boolean(string="Executing at Risk", compute="_compute_zc_provisional", store=True,
                                    help="Items added to the contract, work may be measured, not billable yet.")

    @api.depends("state", "zc_stage", "zc_requires_agreement")
    def _compute_zc_provisional(self):
        for vo in self:
            vo.zc_provisional = vo.state == "approved" and vo.zc_requires_agreement and vo.zc_stage != "agreed"

    # ------------------------------------------------------------------
    def action_zc_impact(self):
        self.write({"zc_stage": "impact"})

    def action_zc_submit_owner(self):
        for vo in self:
            if not vo.line_ids:
                raise UserError(_("Add the priced items of the variation first."))
            vo.write({"zc_stage": "submitted", "zc_submitted_date": fields.Date.context_today(self),
                      "zc_proposed_amount": vo.amount_total})
            vo.message_post(body=_("Submitted to the consultant: %(a)s, time impact %(d)s days.",
                                   a=vo.amount_total, d=vo.zc_time_impact_days))

    def action_zc_negotiate(self):
        self.write({"zc_stage": "negotiation"})

    def action_zc_agree(self):
        """Rates on the variation lines are now the agreed ones: apply them to the contract."""
        for vo in self:
            if vo.state not in ("approved", "submitted", "draft"):
                raise UserError(_("Only an open variation order can be agreed."))
            vo.write({"zc_stage": "agreed", "zc_agreed_date": fields.Date.context_today(self),
                      "zc_agreed_amount": vo.amount_total})
            vo.message_post(body=_("Rate agreed with the consultant: %(a)s (proposed %(p)s).",
                                   a=vo.amount_total, p=vo.zc_proposed_amount or vo.amount_total))
            if vo.state == "approved":
                vo._apply()
        return True

    # ------------------------------------------------------------------
    def _zc_provisional_lines(self):
        return self.sale_order_id.order_line.filtered(lambda l: l.zc_variation_line_id.variation_id == self)

    def _apply(self):
        self.ensure_one()
        if not self.zc_requires_agreement:
            return super()._apply()
        if self.zc_stage != "agreed":
            return self._zc_apply_provisional()
        provisional = self._zc_provisional_lines()
        if not provisional:
            return super()._apply()
        return self._zc_finalize(provisional)

    def _zc_apply_provisional(self):
        """Add the new items to the contract as provisional so the site can measure them."""
        order = self.sale_order_id
        new_lines = self.line_ids.filtered(lambda l: l.line_type == "add")
        if new_lines and not self._zc_provisional_lines():
            seq = max(order.order_line.mapped("sequence") or [0]) + 1
            cmds = [Command.create({"display_type": "line_section", "sequence": seq,
                                    "name": _("Variation Order %(vo)s - %(t)s (rate under negotiation)",
                                              vo=self.name, t=self.title)})]
            for line in new_lines:
                seq += 1
                cmds.append(Command.create({
                    "sequence": seq, "product_id": line.product_id.id,
                    "name": line.name or line.product_id.display_name, "product_uom_qty": line.quantity,
                    "price_unit": line.price_unit, "zc_provisional": True, "zc_variation_line_id": line.id,
                }))
            order.write({"order_line": cmds})
        order.message_post(body=_("%(vo)s approved internally: work may start, items are provisional and not "
                                  "billable until the rate is agreed with the consultant.", vo=self.name))
        return True

    def _zc_finalize(self, provisional):
        order = self.sale_order_id
        for sol in provisional:
            vline = sol.zc_variation_line_id
            sol.write({"price_unit": vline.price_unit, "product_uom_qty": vline.quantity, "zc_provisional": False})
        section = order.order_line.filtered(
            lambda l: l.display_type == "line_section" and self.name in (l.name or "") and "(" in (l.name or ""))
        section.write({"name": _("Variation Order %(vo)s - %(t)s", vo=self.name, t=self.title)})
        cmds = []
        for line in self.line_ids.filtered(lambda l: l.line_type == "change"):
            sol = line.sale_line_id
            if sol.qty_invoiced > sol.product_uom_qty + line.quantity:
                raise UserError(_("%s: the new quantity is lower than what is already invoiced.",
                                  sol.product_id.display_name))
            cmds.append(Command.update(sol.id, {"product_uom_qty": sol.product_uom_qty + line.quantity}))
        if cmds:
            order.write({"order_line": cmds})
        order.message_post(body=_("%(vo)s applied at the agreed rate (contract impact %(a)s).",
                                  vo=self.name, a=self.amount_total))
        self._apply_budget()
        self.state = "applied"
        return True


class SaleOrderLine(models.Model):
    _inherit = "sale.order.line"

    zc_provisional = fields.Boolean(string="Provisional (VO under negotiation)", copy=False)
    zc_variation_line_id = fields.Many2one("hom.variation.order.line", copy=False, index="btree_not_null")


class HomProgressCertificate(models.Model):
    _inherit = "hom.progress.certificate"

    def _apply_measurements(self):
        """Provisional variation items are measured on site but stay out of the certificate."""
        for cert in self.filtered(lambda c: c.state == "draft"):
            provisional = cert.line_ids.filtered(lambda l: l.sale_line_id.zc_provisional)
            if provisional:
                cert.message_post(body=_("Not certified until the rate is agreed: %s",
                                         ", ".join(provisional.mapped("product_id.display_name"))))
                provisional.unlink()
        return super()._apply_measurements()
