{
    "name": "Ziij Construction - Material Control & Stage Valuation",
    "summary": "Connects the technical office, purchasing, stores and the site: BOQ cost breakdown, shop-drawing "
               "quantities from approved submittals, purchase checks against quantity and item budget with a "
               "project-manager exception, supplier return deadlines, the material reconciliation (Material Truth) "
               "and stage valuation of certificates (supplied / installed).",
    "version": "20.0.1.0.1",
    "category": "Services/Project",
    "author": "Ziij AI",
    "license": "OPL-1",
    "depends": ["hom_progress_billing", "hom_subcontract_billing", "hom_submittals", "hom_control_tower",
                "purchase_stock", "sale_project"],
    "data": [
        "security/ir.access.csv",
        "data/cron_data.xml",
        "views/boq_views.xml",
        "views/purchase_views.xml",
        "views/stock_views.xml",
        "views/measurement_views.xml",
        "views/recon_views.xml",
        "views/settings_views.xml",
    ],
    "post_init_hook": "_zc_post_init",
    "installable": True,
}
