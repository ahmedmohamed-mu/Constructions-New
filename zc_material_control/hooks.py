def _zc_post_init(env):
    """Book the contract balance products on their balance-sheet accounts when the chart has them.

    The retention / subcontract advance products are created without accounts; left like that, the owner
    retention would hit the revenue account. Done once, only where the product has no specific account yet.
    """
    env = env(su=True)
    # the advance is recovered as a fraction of the down-payment line: with 2 decimals the recovery is rounded
    # (e.g. 0.5511 -> 0.55 of 81,000 = 90 EGP short); 9 decimals keep it exact to the piaster
    precision = env.ref("uom.decimal_product_uom", raise_if_not_found=False) or \
        env["decimal.precision"].search([("name", "=", "Product Unit")], limit=1)
    if precision and precision.digits < 9:
        precision.digits = 9
    Account = env["account.account"]
    for company in env["res.company"].search([]):
        acc = lambda code: Account.with_company(company).search([("code", "=", code)], limit=1)
        plan = [
            (company.hom_retention_owner_product_id, "property_account_income_id", "102020"),
            (company.hom_retention_sub_product_id, "property_account_expense_id", "201031"),
            (company.hom_sub_advance_product_id, "property_account_expense_id", "102021"),
            (company.hom_sub_advance_recovery_product_id, "property_account_expense_id", "102021"),
        ]
        for product, field, code in plan:
            account = acc(code)
            tmpl = product.product_tmpl_id.with_company(company)
            if product and account and not tmpl[field]:
                tmpl[field] = account
