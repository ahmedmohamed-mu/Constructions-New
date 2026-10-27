from odoo import _, api, fields, models
from odoo.exceptions import UserError


class ZcDeductionType(models.Model):
    """What the owner deducts from our certificates, or what we deduct from a subcontractor."""
    _name = "zc.deduction.type"
    _description = "Certificate Deduction Type"
    _order = "scope, sequence, id"
    _check_company_auto = True

    name = fields.Char(required=True, translate=True)
    code = fields.Char()
    sequence = fields.Integer(default=10)
    active = fields.Boolean(default=True)
    company_id = fields.Many2one("res.company", required=True, default=lambda self: self.env.company)
    scope = fields.Selection([("owner", "Owner certificate"), ("subcontract", "Subcontractor certificate")],
                             required=True, default="owner")
    timing = fields.Selection([
        ("invoice", "On the certificate invoice / bill"),
        ("payment", "Withheld at payment"),
    ], required=True, default="invoice",
        help="On the invoice / bill: the deduction is a line of the certificate invoice or bill "
             "(penalties, utilities, back-charges, materials issued).\n"
             "Withheld at payment: the invoice keeps its full value and the amount is withheld when it is paid "
             "(withholding tax, social insurance); it is settled against the receivable or payable.")
    method = fields.Selection([("percent", "% of the work of the certificate"), ("fixed", "Fixed amount")],
                              required=True, default="fixed")
    default_pct = fields.Float(string="Default %")
    account_id = fields.Many2one("account.account", required=True, check_company=True,
                                 help="Owner, withheld: the asset to recover (e.g. WHT receivable).\n"
                                      "Owner, on invoice: the project cost charged (penalty, utilities).\n"
                                      "Subcontractor, on bill: the project cost recovered.\n"
                                      "Subcontractor, withheld: the liability to pay to the authority.")
    analytic = fields.Boolean(string="Charge the project", default=True,
                              help="Book the line with the project analytic account (cost or cost recovery).")
    note = fields.Char()


class ZcCertificateDeduction(models.Model):
    _name = "zc.certificate.deduction"
    _description = "Certificate Deduction"
    _order = "sequence, id"

    sequence = fields.Integer(default=10)
    type_id = fields.Many2one("zc.deduction.type", required=True, ondelete="restrict")
    name = fields.Char(string="Description")
    owner_cert_id = fields.Many2one("hom.progress.certificate", ondelete="cascade", index="btree_not_null")
    sub_cert_id = fields.Many2one("hom.subcontract.certificate", ondelete="cascade", index="btree_not_null")
    company_id = fields.Many2one("res.company", compute="_compute_cert", store=True)
    currency_id = fields.Many2one("res.currency", compute="_compute_cert", store=True)
    base_amount = fields.Monetary(string="Base", compute="_compute_base_amount")
    timing = fields.Selection(related="type_id.timing", store=True)
    method = fields.Selection(related="type_id.method")
    pct = fields.Float(string="%", compute="_compute_pct", store=True, readonly=False, precompute=True)
    amount = fields.Monetary(compute="_compute_amount", store=True, readonly=False, precompute=True)
    reference = fields.Char(help="Penalty letter, delivery notes, violation report...")
    settled_move_id = fields.Many2one("account.move", string="Settled by", readonly=True, copy=False)
    picking_ids = fields.One2many("stock.picking", "zc_charge_deduction_id", string="Materials issued")

    @api.depends("owner_cert_id", "sub_cert_id")
    def _compute_cert(self):
        for rec in self:
            cert = rec.owner_cert_id or rec.sub_cert_id
            rec.company_id = cert.company_id
            rec.currency_id = cert.currency_id

    def _compute_base_amount(self):
        for rec in self:
            rec.base_amount = (rec.owner_cert_id or rec.sub_cert_id).amount_current

    @api.depends("type_id")
    def _compute_pct(self):
        for rec in self:
            rec.pct = rec.type_id.default_pct if rec.type_id.method == "percent" else 0.0

    @api.depends("pct", "type_id", "owner_cert_id.amount_current", "sub_cert_id.amount_current")
    def _compute_amount(self):
        for rec in self:
            if rec.type_id.method != "percent":
                rec.amount = rec.amount or 0.0
                continue
            cert = rec.owner_cert_id or rec.sub_cert_id
            rec.amount = cert.currency_id.round(cert.amount_current * rec.pct / 100.0) if cert.currency_id else 0.0

    @api.constrains("amount")
    def _check_amount(self):
        for rec in self:
            if rec.amount < 0:
                raise UserError(_("Enter deductions as positive amounts."))

    def _label(self):
        self.ensure_one()
        label = self.name or self.type_id.name
        if self.method == "percent":
            label = "%s (%g%%)" % (label, self.pct)
        return label

    def unlink(self):
        if self.filtered("settled_move_id"):
            raise UserError(_("A settled deduction cannot be deleted."))
        self.picking_ids.write({"zc_charge_deduction_id": False})
        return super().unlink()


class ZcDeductionMixin(models.AbstractModel):
    """Common deduction logic of owner and subcontractor certificates."""
    _name = "zc.deduction.mixin"
    _description = "Certificate deductions"

    def _zc_deduction_lines(self, timing=None):
        lines = self.zc_deduction_ids.filtered(lambda d: d.amount)
        return lines.filtered(lambda d: d.timing == timing) if timing else lines

    def _zc_add_contract_deductions(self, types):
        for cert in self:
            have = cert.zc_deduction_ids.type_id
            new = types.filtered(lambda t: t not in have and t.company_id == cert.company_id)
            if new:
                cert.zc_deduction_ids = [fields.Command.create({"type_id": t.id, "sequence": t.sequence})
                                         for t in new]

    def _zc_settle(self, move, partner, sign):
        """Withholding settlement entry against the counterpart (receivable sign=1 / payable sign=-1)."""
        self.ensure_one()
        lines = self._zc_deduction_lines("payment").filtered(lambda d: not d.settled_move_id)
        if not lines:
            raise UserError(_("No withholding to settle."))
        if move.state != "posted":
            raise UserError(_("Post %s first.", move.display_name))
        company = self.company_id
        journal = company.zc_settlement_journal_id or self.env["account.journal"].search(
            [("type", "=", "general"), ("company_id", "=", company.id)], limit=1)
        counterpart = move.line_ids.filtered(
            lambda l: l.account_id.account_type in ("asset_receivable", "liability_payable") and not l.reconciled)
        if not counterpart:
            raise UserError(_("%s is already fully settled.", move.display_name))
        total = sum(lines.mapped("amount"))
        open_amount = abs(sum(counterpart.mapped("amount_residual")))
        if move.currency_id.compare_amounts(total, open_amount) > 0:
            raise UserError(_("The withholdings (%(w)s) exceed the open amount of %(m)s (%(o)s).",
                              w=total, m=move.display_name, o=open_amount))
        items = []
        for d in lines:
            items.append(fields.Command.create({
                "name": "%s - %s" % (d._label(), move.name), "account_id": d.type_id.account_id.id,
                "partner_id": partner.id,
                "debit": d.amount if sign > 0 else 0.0, "credit": d.amount if sign < 0 else 0.0}))
        items.append(fields.Command.create({
            "name": _("Withheld by %(p)s on %(m)s", p=partner.name, m=move.name) if sign > 0
            else _("Withheld from %(p)s on %(m)s", p=partner.name, m=move.name),
            "account_id": counterpart[0].account_id.id, "partner_id": partner.id,
            "debit": total if sign < 0 else 0.0, "credit": total if sign > 0 else 0.0}))
        entry = self.env["account.move"].create({
            "move_type": "entry", "journal_id": journal.id, "date": fields.Date.context_today(self),
            "ref": _("Withholdings - %s", self.name), "line_ids": items})
        entry.action_post()
        (entry.line_ids.filtered(lambda l: l.account_id == counterpart[0].account_id) | counterpart[0]).reconcile()
        lines.write({"settled_move_id": entry.id})
        self.message_post(body=_("Withholdings settled by %s.", entry._get_html_link()))
        return entry
