{
    "name": "H.O.M Contracting - Control Tower",
    "summary": "Executive, projects, finance and facility-management dashboards for the contracting cycle "
               "(portfolio, certificates, cash, margin, guarantees, alerts).",
    "version": "20.0.1.0.0",
    "category": "Services/Contracting",
    "author": "Ziij AI",
    "license": "OPL-1",
    "depends": ["hom_progress_billing", "hom_subcontract_billing", "hom_variation_order", "hom_guarantees"],
    "data": [
        "views/control_tower_views.xml",
    ],
    "assets": {
        "web.assets_backend": [
            "hom_control_tower/static/src/control_tower.js",
            "hom_control_tower/static/src/control_tower.xml",
            "hom_control_tower/static/src/control_tower.scss",
        ],
        "web.assets_web_dark": [
            "hom_control_tower/static/src/control_tower.dark.scss",
        ],
    },
    "installable": True,
}
