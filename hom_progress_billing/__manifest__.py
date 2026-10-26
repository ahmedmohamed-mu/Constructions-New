{
    "name": "H.O.M Contracting - Owner Progress Certificates",
    "summary": "Owner progress certificates (مستخلصات المالك) on sales contracts: cumulative BOQ measurement, "
               "consultant approval, invoicing with advance recovery and retention.",
    "version": "20.0.1.3.1",
    "category": "Sales/Sales",
    "author": "Ziij AI",
    "license": "OPL-1",
    "depends": ["hom_contracting_base", "sale_project"],
    "data": [
        "security/ir.access.csv",
        "data/ir_sequence_data.xml",
        "data/ir_cron_data.xml",
        "report/certificate_report.xml",
        "views/progress_certificate_views.xml",
        "views/sale_order_views.xml",
        "views/project_views.xml",
        "views/account_move_views.xml",
        "views/site_measurement_views.xml",
    ],
    "post_init_hook": "post_init_hook",
    "installable": True,
}
