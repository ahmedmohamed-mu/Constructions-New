from . import models


def _copy_studio_etc(env):
    """Carry over the Studio 'remaining cost estimate' of projects, if it exists."""
    Project = env["project.project"].with_context(active_test=False)
    if "x_studio_remaining_cost_estimate" not in Project._fields:
        return
    for project in Project.search([("x_studio_remaining_cost_estimate", "!=", 0)]):
        if not project.hom_estimate_to_complete:
            project.hom_estimate_to_complete = project.x_studio_remaining_cost_estimate


def post_init_hook(env):
    _copy_studio_etc(env)
