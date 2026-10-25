{
    "name": "H.O.M Contracting - Subcontractor Certificates",
    "summary": "Subcontract advance bills and subcontractor progress certificates (مستخلصات مقاولي الباطن) "
               "with advance recovery and retention, billed from the subcontract purchase order.",
    "version": "20.0.1.2.2",
    "category": "Inventory/Purchase",
    "author": "Ziij AI",
    "license": "OPL-1",
    "depends": ["hom_contracting_base", "purchase", "project"],
    "data": [
        "security/ir.access.csv",
        "report/certificate_report.xml",
        "views/subcontract_certificate_views.xml",
        "views/purchase_order_views.xml",
    ],
    "installable": True,
}
