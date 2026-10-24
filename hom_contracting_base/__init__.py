from . import models


def post_init_hook(env):
    """Carry over values that were captured earlier with Studio fields (x_studio_*)."""
    mapping = {
        "sale.order": {
            "x_studio_project_type": "hom_project_type",
            "x_studio_consultant": "hom_consultant_id",
            "x_studio_advance_pct": "hom_advance_pct",
            "x_studio_retention_pct": "hom_retention_pct",
        },
        "purchase.order": {
            "x_studio_is_subcontract": "hom_is_subcontract",
            "x_studio_advance_amount": "hom_advance_amount",
            "x_studio_advance_recovery_pct": "hom_advance_recovery_pct",
            "x_studio_retention_pct": "hom_retention_pct",
        },
    }
    for model, fields_map in mapping.items():
        Model = env[model].with_context(active_test=False)
        present = {src: dst for src, dst in fields_map.items() if src in Model._fields}
        if not present:
            continue
        for rec in Model.search([]):
            vals = {}
            for src, dst in present.items():
                value = rec[src]
                if Model._fields[src].type == "many2one":
                    value = value.id
                if value not in (False, None):
                    vals[dst] = value
            if vals:
                rec.write(vals)
