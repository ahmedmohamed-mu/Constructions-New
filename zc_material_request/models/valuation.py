from odoo import models

from odoo.addons.zc_project_controls.hooks import _account

# The material cost of the projects: what leaves the stores for the work, for a subcontractor or as a stock difference.
MATERIAL_COST = ("400011", "expense_direct_cost", "Materials & equipment cost - projects", "تكلفة خامات ومعدات - مشروعات")


class ResCompany(models.Model):
    _inherit = "res.company"

    def _zc_setup_stock_valuation_accounts(self):
        """Perpetual valuation posts an entry only when material leaves the stores for a location that carries an
        account. The places material goes to on a contracting company (the work, a subcontractor, a stock count)
        get the project material cost account, so the cost reaches the project when the material is used."""
        env = self.env(su=True)
        for company in self:
            if not company.chart_template or not env["account.account"].with_company(company).search_count([], limit=1):
                continue
            account = _account(env, company, *MATERIAL_COST)
            locations = company._zc_consumption_location()
            locations |= env["stock.location"].search([("usage", "=", "inventory"),
                                                       ("company_id", "in", (company.id, False))])
            customers = env.ref("stock.stock_location_customers", raise_if_not_found=False)
            if customers:
                locations |= customers  # issues to subcontractors (charged back on their certificates)
            locations.filtered(lambda l: not l.valuation_account_id).write({"valuation_account_id": account.id})


class StockMove(models.Model):
    _inherit = "stock.move"

    def _zc_valuation_project(self):
        """The project whose site store the material leaves (or enters, for a count found more)."""
        self.ensure_one()
        if self.zc_measurement_id:
            m = self.zc_measurement_id
            project = m.project_id if "project_id" in m._fields else m.sale_line_id.order_id.project_ids[:1]
            if project:
                return project
        store = self.location_id if self.location_id.usage in ("internal", "transit") else self.location_dest_id
        warehouse = store.warehouse_id
        if not warehouse:
            return self.env["project.project"]
        return self.env["project.project"].sudo().search([("zc_warehouse_id", "=", warehouse.id)], limit=1)

    def _get_account_move_line_vals(self):
        """The cost side of a perpetual stock entry is booked on the project of the site store."""
        vals_list = super()._get_account_move_line_vals()
        account = self._zc_valuation_project().account_id
        if not account:
            return vals_list
        stock_account = self.product_id._get_product_accounts()["stock_valuation"]
        for vals in vals_list:
            if vals["account_id"] != stock_account.id:
                vals["analytic_distribution"] = {str(account.id): 100}
        return vals_list


class AccountMoveLine(models.Model):
    _inherit = "account.move.line"

    def _zc_is_stock_purchase_line(self):
        """A vendor bill line of a material valued perpetually lands on the stock account: the material is still in
        the store, so it is not a project cost yet (the project is charged when the material is issued). The
        analytic distribution stays on the line to tell which project bought it."""
        self.ensure_one()
        if self.display_type != "product" or not self.product_id \
                or self.move_id.move_type not in ("in_invoice", "in_refund", "in_receipt"):
            return False
        if not self._use_inventory_valuation():
            return False
        return self.account_id == self.product_id._get_product_accounts()["stock_valuation"]

    def _prepare_analytic_lines(self):
        if self._zc_is_stock_purchase_line():
            return []
        return super()._prepare_analytic_lines()
