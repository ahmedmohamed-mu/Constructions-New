{
    "name": "H.O.M Contracting - Variation Orders",
    "summary": "Variation orders (أوامر التغيير): CEO approval, then the contract (sales order) and, when "
               "Budget is installed, the project cost budget are revised automatically.",
    "version": "20.0.1.1.1",
    "category": "Sales/Sales",
    "author": "Ziij AI",
    "license": "OPL-1",
    "depends": ["hom_contracting_base", "sale_project"],
    "data": [
        "security/ir.access.csv",
        "views/variation_order_views.xml",
        "views/sale_order_views.xml",
    ],
    "installable": True,
}
