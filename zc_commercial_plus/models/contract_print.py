from odoo import fields, models

FREQUENCY_AR = {"monthly": "شهري", "milestone": "عند إنجاز كل مرحلة", "on_demand": "عند الطلب", "interval": "كل %s يوم"}
TIMING_AR = {"invoice": "تُخصم من المستخلص", "payment": "تُخصم عند الصرف"}


def _words(currency, amount):
    """Amount in Arabic words (falls back to the user's language when Arabic is not installed)."""
    try:
        return currency.with_context(lang="ar_001").amount_to_text(amount)
    except Exception:  # noqa: BLE001 - a missing num2words language must not break the print
        return currency.amount_to_text(amount)


def _deductions(types):
    rows = []
    for t in types:
        value = "%s%%" % round(t.default_pct, 2) if t.method == "percent" else "مبلغ يُحدد في كل مستخلص"
        rows.append({"name": t.name, "value": value, "timing": TIMING_AR.get(t.timing, t.timing or ""),
                     "note": t.note or ""})
    return rows


def _guarantees(guarantees):
    gtype = dict(guarantees._fields["guarantee_type"]._description_selection(guarantees.env)) if guarantees else {}
    return [{"name": g.name, "type": gtype.get(g.guarantee_type, g.guarantee_type), "bank": g.bank_name or "",
             "amount": g.amount, "expiry": g.expiry_date, "state": g.state} for g in guarantees]


class SaleOrder(models.Model):
    _inherit = "sale.order"

    def _zc_contract_print(self):
        """Everything the printed owner contract needs, in one place."""
        self.ensure_one()
        order = self
        project = order.project_ids[:1]
        rows, n = [], 0
        for line in order.order_line.sorted("sequence"):
            if line.is_downpayment:
                continue
            if line.display_type == "line_section":
                rows.append({"section": line.name})
                continue
            if line.display_type:
                rows.append({"note": line.name})
                continue
            n += 1
            rows.append({"n": n, "code": line.product_id.default_code or "",
                         "name": line.name or line.product_id.display_name, "uom": line.product_uom_id.name,
                         "qty": line.product_uom_qty, "price": line.price_unit, "amount": line.price_subtotal,
                         "provisional": "zc_provisional" in line._fields and line.zc_provisional})
        vos = order.hom_variation_ids.filtered(lambda v: v.state in ("approved", "applied"))
        vo_total = sum(vos.filtered(lambda v: v.state == "applied").mapped("amount_total"))
        start = project.date_start
        end = project.date
        dlp = order.zc_dlp_months if "zc_dlp_months" in order._fields else 12
        prelim_pct = order.zc_retention_prelim_pct if "zc_retention_prelim_pct" in order._fields else 0.0
        escalation = None
        if "zc_escalation" in order._fields and order.zc_escalation:
            escalation = {
                "base": order.zc_escalation_base_date, "threshold": order.zc_escalation_threshold,
                "fixed": getattr(order, "zc_escalation_fixed", 0.0),
                "formula": [{"name": f.index_id.display_name, "weight": f.weight}
                            for f in order.zc_escalation_formula_ids],
            }
        return {
            "project": project, "start": start, "end": end,
            "days": (end - start).days if start and end else 0,
            "rows": rows, "items": n,
            "original": order.amount_untaxed - vo_total, "vo_total": vo_total, "vos": vos,
            "untaxed": order.amount_untaxed, "tax": order.amount_tax, "total": order.amount_total,
            "words": _words(order.currency_id, order.amount_total),
            "advance": order.amount_untaxed * order.hom_advance_pct / 100.0,
            "frequency": (FREQUENCY_AR.get(order.hom_certificate_frequency, order.hom_certificate_frequency or "")
                          .replace("%s", str(getattr(order, "zc_cert_interval_days", "") or ""))),
            "dlp": dlp, "prelim_pct": prelim_pct,
            "deductions": _deductions(order.zc_deduction_type_ids),
            "guarantees": _guarantees(order.hom_guarantee_ids),
            "escalation": escalation,
            "today": fields.Date.context_today(self),
        }


class PurchaseOrder(models.Model):
    _inherit = "purchase.order"

    def _zc_subcontract_print(self):
        """Everything the printed subcontract agreement needs."""
        self.ensure_one()
        po = self
        rows, n = [], 0
        for line in po.order_line.sorted("sequence"):
            if line.display_type == "line_section":
                rows.append({"section": line.name})
                continue
            if line.display_type:
                rows.append({"note": line.name})
                continue
            n += 1
            boq = line.zc_boq_line_id or ("hom_boq_line_id" in line._fields and line.hom_boq_line_id)
            rows.append({"n": n, "code": boq.product_id.default_code if boq else (line.product_id.default_code or ""),
                         "boq": boq.product_id.display_name if boq else "",
                         "name": line.name or line.product_id.display_name, "uom": line.uom_id.name,
                         "qty": line.product_qty, "price": line.price_unit, "amount": line.price_subtotal})
        boq_lines = po.order_line.mapped("zc_boq_line_id")
        contract = boq_lines.mapped("order_id")[:1]
        project = contract.project_ids[:1]
        if not project:
            for key in (po.order_line.mapped("analytic_distribution") or []):
                for acc in str(key or "").split(","):
                    if acc.isdigit():
                        project = self.env["project.project"].search([("account_id", "=", int(acc))], limit=1)
                        if project:
                            break
                if project:
                    break
            contract = contract or project.sale_order_id
        recovery = po.hom_advance_recovery_pct
        return {
            "contract": contract, "project": project, "rows": rows, "items": n,
            "start": project.date_start, "end": project.date,
            "untaxed": po.amount_untaxed, "tax": po.amount_tax, "total": po.amount_total,
            "words": _words(po.currency_id, po.amount_total),
            "advance": po.hom_advance_amount, "recovery": recovery,
            "deductions": _deductions(po.zc_deduction_type_ids),
            "guarantees": _guarantees(po.hom_guarantee_ids),
            "package": po.zc_package_id if "zc_package_id" in po._fields else False,
            "dlp": contract.zc_dlp_months if contract and "zc_dlp_months" in contract._fields else 12,
            "today": fields.Date.context_today(self),
        }
