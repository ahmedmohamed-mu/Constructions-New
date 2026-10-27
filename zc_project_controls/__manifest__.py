{
    "name": "Ziij Construction - Project Controls",
    "summary": "Earned value against an approved baseline (PV / EV / AC, SPI / CPI, S-curve), G&A overhead "
               "allocation to projects, company equipment charged to projects as internal rent, the owner side "
               "of variation orders (instruction, impact, negotiation, agreed rate; executed work not billable "
               "until agreed) and weekly sheets for daily labour.",
    "version": "20.0.1.0.0",
    "category": "Services/Project",
    "author": "Ziij AI",
    "license": "OPL-1",
    "depends": ["zc_material_control", "hom_variation_order", "maintenance", "account"],
    "data": [
        "security/ir.access.csv",
        "views/evm_views.xml",
        "views/overhead_views.xml",
        "views/equipment_views.xml",
        "views/variation_views.xml",
        "views/labour_views.xml",
        "views/settings_views.xml",
        "views/menus.xml",
    ],
    "post_init_hook": "_zc_pc_post_init",
    "installable": True,
}
