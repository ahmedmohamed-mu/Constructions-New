ACCOUNTS = [
    # field, code, type, english, arabic
    ("zc_overhead_account_id", "400014", "expense_direct_cost",
     "Allocated G&A overhead - projects", "مصروفات عمومية محمّلة على المشروعات"),
    ("zc_overhead_absorbed_account_id", "429990", "expense",
     "G&A overhead absorbed by projects", "مصروفات عمومية مستوعبة في المشروعات (دائن)"),
    ("zc_equipment_cost_account_id", "400015", "expense_direct_cost",
     "Internal equipment rent - projects", "إيجار معدات داخلي محمّل على المشروعات"),
    ("zc_equipment_recovery_account_id", "429991", "expense",
     "Internal equipment rent recovered", "إيجار معدات داخلي مسترد (دائن)"),
]
LABOUR_ACCOUNT = ("400016", "expense_direct_cost", "Daily labour - projects", "عمالة يومية - مشروعات")


def _account(env, company, code, account_type, name_en, name_ar):
    Account = env["account.account"].with_company(company)
    account = Account.search([("code", "=", code)], limit=1)
    if account:
        return account
    account = Account.create({"code": code, "name": name_en, "account_type": account_type})
    if env["res.lang"].search_count([("code", "=", "ar_001")]):
        account.with_context(lang="ar_001").name = name_ar
    return account


def _zc_pc_post_init(env):
    """Accounts, the daily-labour service and payee, so the project controls work out of the box.

    Only on companies with a chart of accounts and only where nothing is set yet.
    """
    env = env(su=True)
    for company in env["res.company"].search([]):
        if not env["account.account"].with_company(company).search_count([], limit=1) or not company.chart_template:
            continue
        for field, code, account_type, en, ar in ACCOUNTS:
            if not company[field]:
                company[field] = _account(env, company, code, account_type, en, ar)
        if not company.zc_labour_product_id:
            labour_account = _account(env, company, *LABOUR_ACCOUNT)
            product = env["product.product"].create({
                "name": "Daily labour (weekly sheet)", "type": "service", "sale_ok": False, "purchase_ok": True,
                "default_code": "ZC-LAB-D", "taxes_id": [(5, 0, 0)], "supplier_taxes_id": [(5, 0, 0)],
                "company_id": False,
            })
            product.product_tmpl_id.with_company(company).property_account_expense_id = labour_account
            if env["res.lang"].search_count([("code", "=", "ar_001")]):
                product.with_context(lang="ar_001").name = "عمالة يومية (كشف أسبوعي)"
            company.zc_labour_product_id = product
        if not company.zc_labour_partner_id:
            company.zc_labour_partner_id = env["res.partner"].create({
                "name": "Daily labour - site cash", "company_id": company.id, "supplier_rank": 1})
        if not company.zc_overhead_journal_id:
            company.zc_overhead_journal_id = env["account.journal"].search(
                [("type", "=", "general"), ("company_id", "=", company.id)], limit=1)
    env["maintenance.equipment"]._zc_equipment_plan()
