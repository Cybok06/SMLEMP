"""Explicit existing role provisioning; review the normalization before execution."""
if __name__ == '__main__':
    from Backend.inventory.settings_store import ensure_default_inventory_roles
    ensure_default_inventory_roles()
    print('Inventory roles initialized and legacy inventory users normalized.')
