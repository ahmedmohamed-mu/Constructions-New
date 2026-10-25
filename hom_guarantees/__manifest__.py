{
    "name": "H.O.M Contracting - Bank Guarantees",
    "summary": "Letters of guarantee (خطابات الضمان): bid, advance payment, performance and retention bonds "
               "issued for owners or received from subcontractors, with expiry alerts and advance reduction.",
    "version": "20.0.1.1.1",
    "category": "Accounting/Accounting",
    "author": "Ziij AI",
    "license": "OPL-1",
    "depends": ["hom_contracting_base"],
    "data": [
        "security/ir.access.csv",
        "data/ir_cron_data.xml",
        "views/bank_guarantee_views.xml",
        "views/order_views.xml",
    ],
    "installable": True,
}
