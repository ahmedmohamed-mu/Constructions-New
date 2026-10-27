def _zc_ce_post_init(env):
    env = env(su=True)
    arabic = bool(env["res.lang"].search_count([("code", "=", "ar_001")]))
    for company in env["res.company"].search([]):
        tax = company.account_sale_tax_id
        for field, code, en, ar in (("zc_escalation_product_id", "ZC-ESC", "Price escalation", "فروق أسعار"),
                                    ("zc_claim_product_id", "ZC-CLM", "Claim settlement", "تسوية مطالبة")):
            if company[field]:
                continue
            product = env["product.product"].search([("default_code", "=", code)], limit=1) or env["product.product"].create({
                "name": en, "default_code": code, "type": "service", "sale_ok": True, "purchase_ok": False,
                "invoice_policy": "order", "taxes_id": [(6, 0, tax.ids)], "company_id": False})
            if arabic:
                product.with_context(lang="ar_001").name = ar
            company[field] = product
    defaults = [("STEEL", "Reinforcement steel", "حديد التسليح"), ("CEMENT", "Cement", "الأسمنت"),
                ("LABOUR", "Labour wages", "أجور العمالة"), ("FUEL", "Fuel (diesel)", "الوقود (سولار)"),
                ("ELEC", "Electrical cables & fittings", "الكابلات والمهمات الكهربائية")]
    Index = env["zc.price.index"]
    for code, en, ar in defaults:
        if not Index.search_count([("code", "=", code)]):
            rec = Index.create({"code": code, "name": en})
            if arabic:
                rec.with_context(lang="ar_001").name = ar
