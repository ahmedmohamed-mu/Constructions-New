from odoo import Command, _, api, fields, models
from odoo.exceptions import UserError

STANDARD = [
    ("asbuilt", "As-built drawings", True),
    ("om_manual", "Operation & maintenance manuals", True),
    ("warranty", "Supplier warranties", True),
    ("test", "Test & commissioning certificates", True),
    ("training", "Training of the owner's staff", False),
    ("spares", "Spare parts & keys handed over", False),
]


class ZcHandoverDocument(models.Model):
    _name = "zc.handover.document"
    _description = "Handover Document"
    _order = "handover_id, sequence, id"

    handover_id = fields.Many2one("zc.handover", required=True, ondelete="cascade", index=True)
    sequence = fields.Integer(default=10)
    doc_type = fields.Selection([(k, label) for k, label, _r in STANDARD] + [("other", "Other")], default="other",
                                required=True)
    name = fields.Char(string="Document", required=True)
    required = fields.Boolean(default=True)
    received = fields.Boolean(string="Handed over")
    date = fields.Date(string="On")
    reference = fields.Char(string="Transmittal / ref.")
    partner_id = fields.Many2one("res.partner", string="Provided by")

    @api.onchange("received")
    def _onchange_received(self):
        if self.received and not self.date:
            self.date = fields.Date.context_today(self)


class ZcHandover(models.Model):
    _inherit = "zc.handover"

    document_ids = fields.One2many("zc.handover.document", "handover_id", string="Handover documents", copy=True)
    document_missing = fields.Integer(string="Required documents missing", compute="_compute_document_missing")

    @api.depends("document_ids.received", "document_ids.required")
    def _compute_document_missing(self):
        for rec in self:
            rec.document_missing = len(rec.document_ids.filtered(lambda d: d.required and not d.received))

    def action_load_documents(self):
        for rec in self.filtered(lambda r: not r.document_ids):
            rec.document_ids = [Command.create({"doc_type": k, "name": self.env._(label), "required": req,
                                                "sequence": i}) for i, (k, label, req) in enumerate(STANDARD)]

    def action_sign(self):
        for rec in self.filtered(lambda r: r.state == "draft" and r.document_missing):
            missing = rec.document_ids.filtered(lambda d: d.required and not d.received)
            raise UserError(_("Hand over these documents before signing: %s", ", ".join(missing.mapped("name"))))
        return super().action_sign()
