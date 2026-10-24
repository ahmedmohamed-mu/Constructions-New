# H.O.M Contracting add-ons (Odoo 20)

Thin modules on top of standard Odoo. Everything that Studio/standard covers stays in Studio.

| Module | App / persona | What it adds |
|---|---|---|
| hom_contracting_base | Sales + Purchase + Accounting | Contract terms (advance %, retention %, consultant) on SO; subcontract terms on PO; contracting products & settings |
| hom_progress_billing | Sales (QS) → Consultant → Accounting | Owner progress certificate: cumulative BOQ measurement, approval pushes Delivered qty to the SO, invoice with pro-rata advance recovery + retention, "مستخلص جاري" report |
| hom_subcontract_billing | Site / Purchase → Accounting | Subcontract advance bill + subcontractor certificate: approval pushes Received qty to the PO, bill with advance recovery + retention |
| hom_variation_order | Sales → CEO | Variation order with approval; applies to the SO (new section / qty changes) and revises the project budget when Budget is installed |

## Deploy on Odoo.sh
1. Copy the 4 folders to the root of the project repo, on a **staging** branch (not main).
2. Push → Odoo.sh builds staging on a copy of production → install the modules from Apps.
3. `hom_contracting_base` copies the existing `x_studio_*` values (SO/PO) automatically on install.
4. Accounting > Settings > Contracting: check retention / advance products and their balance-sheet accounts (102020, 201031, 102021), certificates accountant, VO approver.
5. Archive the Studio server actions "احسب المستخلص" / "احسب مستخلص المقاول" and hide the old x_studio fields once validated.

Tests: `odoo-bin --test-tags /hom_progress_billing,/hom_subcontract_billing,/hom_variation_order` (6 tests, they reproduce the demo figures: subcontractor certificate 388,936).
