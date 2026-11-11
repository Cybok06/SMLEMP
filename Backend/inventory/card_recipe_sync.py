"""Reconcile undelivered package recipes and stock when a card is edited."""
from copy import deepcopy
from datetime import datetime

from bson import ObjectId

from . import stock_deductions_store as store


def _requirements(recipe):
    quantities = {}
    for component in (recipe or {}).get('components') or []:
        key = str(component.get('inventory_product_id') or '')
        quantities[key] = quantities.get(key, 0) + store._integer(component.get('required_quantity'), 0)
    return quantities


def _return_excess(package, deduction, component, required, actor, now, session):
    """Return the most recent allocations first, retaining their original costs."""
    deducted = store._integer(component.get('deducted_quantity'), 0)
    excess = max(0, deducted - required)
    allocations = deepcopy(component.get('allocations') or [])
    if deducted and not allocations and component.get('location_id'):
        allocations = [{'location_id': str(component['location_id']), 'quantity': deducted,
                        'unit_cost': component.get('unit_cost'), 'location_name': ''}]
    if excess and sum(store._integer(a.get('quantity'), 0) for a in allocations) != deducted:
        raise ValueError('An existing stock deduction has incomplete warehouse allocations. Reconcile it before changing components.')
    product_id = store._oid(component.get('inventory_product_id'))
    returned_cost, movements = 0.0, []
    for allocation in reversed(allocations):
        quantity = min(excess, store._integer(allocation.get('quantity'), 0))
        if not quantity:
            continue
        location_id = store._oid(allocation.get('location_id'))
        unit_cost = store._number(allocation.get('unit_cost'), 0)
        if not product_id or not location_id or unit_cost <= 0:
            raise ValueError('An existing deduction is missing its warehouse or cost. Reconcile it before changing components.')
        location = store.locations_col.find_one({'_id': location_id}, session=session)
        product = store.inventory_products_col.find_one({'_id': product_id}, session=session)
        if not location or not product:
            raise ValueError('The original stock product or warehouse could not be found. Component changes were not saved.')
        before, _ = store._location_stock(product, str(location_id))
        movement_id = ObjectId()
        entry = {'location_id': str(location_id), 'location_name': location.get('name', ''),
                 'location_code': location.get('code', ''), 'branch': deduction.get('branch'),
                 'quantity': quantity, 'cost_price': unit_cost, 'selling_price': 0,
                 'created_at': now, 'updated_at': now, 'movement_type': 'card_recipe_stock_return',
                 'deduction_id': str(deduction['_id']), 'package_id': str(package['_id'])}
        store.inventory_products_col.update_one({'_id': product_id},
            {'$push': {'entries': entry}, '$set': {'updated_at': now}}, session=session)
        store.locations_col.update_one({'_id': location_id},
            {'$inc': {'stock_units': quantity}, '$set': {'updated_at': now}}, session=session)
        store.movements_col.insert_one({
            '_id': movement_id, 'deduction_id': deduction['_id'], 'package_id': package['_id'],
            'customer_id': package.get('customer_id'), 'product_index': package.get('product_index'),
            'product_id': product_id, 'product_name': product.get('name', ''), 'sku': product.get('sku', ''),
            'branch': deduction.get('branch'), 'location_id': location_id, 'location_name': location.get('name', ''),
            'before_quantity': before, 'after_quantity': before + quantity,
            'deducted_quantity': -quantity, 'unit_cost': unit_cost, 'total_cost': -round(quantity * unit_cost, 2),
            'movement_type': 'card_recipe_stock_return', 'created_at': now, 'actor': actor,
        }, session=session)
        allocation['quantity'] -= quantity
        excess -= quantity
        returned_cost += quantity * unit_cost
        movements.append(movement_id)
    return {
        **component, 'deducted_quantity': min(required, deducted),
        'total_cost': round(float(component.get('total_cost') or 0) - returned_cost, 2),
        'allocations': [a for a in allocations if a.get('quantity', 0) > 0],
    }, movements


def reconcile_package(package, product, identity, session):
    """Run inside the same transaction as the product edit."""
    now = datetime.utcnow()
    actor = {key: identity.get(key) for key in ('user_id', 'name', 'role')}
    recipe = store.build_submission_recipe_snapshot(
        product, package.get('qty') or (package.get('product') or {}).get('quantity') or 1, session=session)
    if not recipe or not recipe.get('components'):
        raise ValueError('The updated card must have valid inventory components.')
    recipe.update(source='updated_card_recipe', updated_by=actor)
    for component in recipe['components']:
        component['recipe_source'] = 'updated_card_recipe'
    previous = package.get('inventory_recipe_snapshot') or {}
    deduction = store.deductions_col.find_one({'package_id': package['_id']}, session=session)
    if _requirements(previous) == _requirements(recipe) and (
        not deduction or _requirements(deduction.get('recipe')) == _requirements(recipe)
    ):
        return False
    # Historical outflows without allocation records cannot be safely reversed.
    if not deduction:
        historical = store.outflows_col.find_one({'customer_id': package.get('customer_id'),
            'packaged_product_index': package.get('product_index'), 'source': 'Agent_deliveries'}, session=session)
        if any(c.get('status') == 'deducted' for c in (historical or {}).get('components_deducted', [])):
            raise ValueError('An undelivered card has historical stock deductions without allocation records. Reconcile it before changing components.')
    updates = {'inventory_recipe_snapshot': recipe, 'updated_at': now}
    returned_movements = []
    if deduction:
        original_units = sum(store._integer(c.get('deducted_quantity'), 0) for c in deduction.get('components') or [])
        original_cost = float(deduction.get('total_cost_value') or 0)
        requirements = {}
        for spec in recipe['components']:
            key = str(spec.get('inventory_product_id') or '')
            required = store._integer(spec.get('required_quantity'), 0)
            if required <= 0 or not store._oid(key):
                raise ValueError('Every component needs a valid inventory link and positive quantity.')
            if key not in requirements:
                requirements[key] = {**spec, 'required_quantity': 0}
            requirements[key]['required_quantity'] += required
        prior = {}
        for component in deduction.get('components') or []:
            key = str(component.get('inventory_product_id') or '')
            if key in prior:
                raise ValueError('Duplicate historical component allocations require reconciliation before editing this card.')
            retained, movements = _return_excess(package, deduction, component,
                requirements.get(key, {}).get('required_quantity', 0), actor, now, session)
            prior[key] = retained
            returned_movements.extend(movements)
        components = []
        for key, spec in requirements.items():
            old = prior.get(key) or {}
            deducted = store._integer(old.get('deducted_quantity'), 0)
            remaining = spec['required_quantity'] - deducted
            components.append({**old, 'inventory_product_id': store._oid(key),
                'name': spec.get('component_name') or spec.get('name') or old.get('name', ''),
                'sku': spec.get('sku') or old.get('sku', ''), 'required_quantity': spec['required_quantity'],
                'deducted_quantity': deducted, 'deducted_now': 0, 'remaining_quantity': remaining,
                'total_cost': old.get('total_cost', 0), 'allocations': old.get('allocations', []),
                'status': 'undeducted' if remaining else 'deducted',
                'reason': 'recipe_changed' if remaining else ''})
        units = sum(c['deducted_quantity'] for c in components)
        remaining = sum(c['remaining_quantity'] for c in components)
        status = 'deducted' if not remaining else 'partially_deducted' if units else 'not_deducted'
        deduction.update(recipe=recipe, recipe_source=recipe['source'], components=components,
            total_units=units, remaining_units=remaining, status=status, workflow='packaging',
            total_cost_value=round(sum(c['total_cost'] for c in components), 2),
            movement_ids=list(deduction.get('movement_ids') or []) + returned_movements,
            recipe_updated_at=now, recipe_updated_by=actor)
        store.deductions_col.replace_one({'package_id': package['_id']}, deduction, session=session)
        updates['stock_deduction_status'] = status
        store.outflows_col.update_one({'customer_id': package.get('customer_id'),
            'packaged_product_index': package.get('product_index'), 'source': 'Agent_deliveries'},
            {'$set': {'components_deducted': components, 'components_status': status,
                      'product_def.components': product['components'], 'recipe_updated_at': now}}, session=session)
        from .packaging_stock import attempts
        attempts.insert_one({'_id': ObjectId(), 'package_id': package['_id'],
            'request_id': 'recipe:' + recipe['recipe_version'], 'deduction_id': deduction['_id'],
            'branch': deduction.get('branch'), 'kind': 'recipe_update', 'created_at': now,
            'status': status, 'components': components, 'actor': actor,
            'deducted_now': units - original_units,
            'value_now': round(deduction['total_cost_value'] - original_cost, 2),
            'returned_movement_ids': returned_movements}, session=session)
    store.packages_col.update_one({'_id': package['_id']}, {'$set': updates}, session=session)
    store.activity_logs_col.insert_one({'action': 'product_card_recipe_updated', 'entity_type': 'package',
        'entity_id': str(package['_id']), 'timestamp': now, 'user_id': identity.get('user_id'),
        'role': identity.get('role'), 'meta': {'previous_recipe': previous, 'recipe': recipe,
                                            'returned_movement_ids': returned_movements}}, session=session)
    return True


def update_cards_with_recipes(operations, identity):
    """Atomically save product changes, stock returns and undelivered recipes."""
    def commit(session):
        changed = 0
        for product_id, operation in operations:
            store.products_col.update_one({'_id': product_id}, operation, session=session)
            product = store.products_col.find_one({'_id': product_id}, session=session)
            references = [product_id, str(product_id)]
            query = {'status': {'$in': ['pending', 'packaging', 'delivering']}, '$or': [
                {'product._id': {'$in': references}}, {'package_def_id': {'$in': references}},
                {'inventory_recipe_snapshot.card_id': {'$in': references}}]}
            for package in store.packages_col.find(query, session=session):
                changed += bool(reconcile_package(package, product, identity or {}, session))
        return changed
    try:
        with store.db.client.start_session() as session:
            return session.with_transaction(commit)
    except Exception as exc:
        if isinstance(exc, ValueError):
            raise
        raise ValueError(str(store._transaction_error(exc))) from exc
