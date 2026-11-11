"""Card edits reconcile real stock services against an isolated database."""
from copy import deepcopy
from datetime import datetime
from unittest.mock import patch
from uuid import uuid4

from bson import ObjectId
import mongomock
import pytest

from Backend.inventory import card_recipe_sync as sync
from Backend.inventory import packaging_stock as packaging
from Backend.inventory import product_cards_store as cards
from Backend.inventory import stock_deductions_store as store


@pytest.fixture
def flow(monkeypatch):
    database = mongomock.MongoClient().recipe_tests
    names = {'packages_col': 'packages', 'customers_col': 'customers', 'users_col': 'users',
             'products_col': 'products', 'inventory_products_col': 'inventory_products',
             'locations_col': 'inventory_branch_locations', 'outflows_col': 'inventory_products_outflow',
             'deductions_col': 'inventory_delivery_deductions', 'movements_col': 'inventory_delivery_movements',
             'stock_sessions_col': 'inventory_stock_update_sessions', 'activity_logs_col': 'activity_logs'}
    for module in (store, cards):
        for attr, collection in names.items():
            if hasattr(module, attr):
                monkeypatch.setattr(module, attr, database[collection])
    monkeypatch.setattr(store, 'db', database)
    monkeypatch.setattr(packaging, 'db', database)
    monkeypatch.setattr(packaging, 'attempts', database.inventory_deduction_attempts)
    monkeypatch.setattr(cards, 'invalidate_product_card_cache', lambda: None)
    monkeypatch.setattr(cards, 'list_product_cards', lambda: [])

    class Transaction:
        def __enter__(self): return self
        def __exit__(self, *args): return False
        def with_transaction(self, callback):
            backup = {name: list(database[name].find()) for name in database.list_collection_names()}
            try:
                return callback(None)
            except Exception:
                for name in database.list_collection_names():
                    database[name].delete_many({})
                    if backup.get(name): database[name].insert_many(backup[name])
                raise
    monkeypatch.setattr(database.client, 'start_session', lambda: Transaction())
    ids = {key: ObjectId() for key in ('card', 'rice', 'tv', 'agent', 'customer', 'package', 'location', 'second')}
    for key in ('location', 'second'):
        database.inventory_branch_locations.insert_one({'_id': ids[key], 'name': key, 'code': key,
            'branch': 'Accra', 'status': 'active', 'stock_units': 10 if key == 'location' else 0})
    database.inventory_products.insert_many([
        {'_id': ids['rice'], 'name': 'Rice', 'sku': 'RICE', 'entries': [
            {'location_id': str(ids['location']), 'quantity': 10, 'cost_price': 5, 'created_at': datetime(2026, 1, 1)}]},
        {'_id': ids['tv'], 'name': 'TV', 'sku': 'TV', 'entries': []},
    ])
    components = [{'_id': ids[key], 'quantity': 1, 'source_collection': 'inventory_products'} for key in ('rice', 'tv')]
    product = {'_id': ids['card'], 'name': 'Bundle', 'components': components}
    database.products.insert_one(product)
    database.users.insert_one({'_id': ids['agent'], 'role': 'agent', 'branch': 'Accra'})
    database.customers.insert_one({'_id': ids['customer'], 'name': 'Customer', 'agent_id': ids['agent'],
        'purchases': [{'product': {'_id': ids['card'], 'name': 'Bundle', 'status': 'submitted_for_packaging'}}]})
    database.packages.insert_one({'_id': ids['package'], 'customer_id': ids['customer'], 'product_index': 0,
        'product': {'_id': ids['card']}, 'status': 'pending', 'qty': 1,
        'inventory_recipe_snapshot': store.build_submission_recipe_snapshot(product, 1)})
    identity = {'role': 'inventory', 'user_id': str(ObjectId()), 'name': 'Stock staff', 'stock_branch_scope': 'Accra'}

    def transition(target='packaging', **kwargs):
        return packaging.transition(str(ids['package']), target, identity, **kwargs)

    def edit(requirements, full=False):
        rows = [{'key': 'inventory_product:' + str(ids[key]), 'quantity': quantity} for key, quantity in requirements]
        payload = {'components': rows}
        if full:
            payload.update(name='Bundle', price=100, cashPrice=90, costPrice=30, paymentRate=5)
            return cards.update_product_card(cards._product_display_group_key(product), payload, identity)
        return cards.update_product_card_components(cards._product_display_group_key(product), payload, identity)

    return database, ids, transition, edit


@pytest.mark.parametrize('full', [False, True])
def test_removed_shortage_allows_delivery_without_repeating_deduction(flow, full):
    db, ids, transition, edit = flow
    assert transition()['status'] == 'partially_deducted'
    assert edit([('rice', 1)], full)['updatedPackages'] == 1
    record = db.inventory_delivery_deductions.find_one()
    assert record['status'] == 'deducted' and record['remaining_units'] == 0
    assert len(record['components']) == 1
    assert transition('delivered')['status'] == 'deducted'
    assert db.inventory_delivery_movements.count_documents({}) == 1
    assert db.packages.find_one()['status'] == 'delivered'


def test_removed_deducted_item_returns_stock_at_original_cost(flow):
    db, ids, transition, edit = flow
    transition()
    edit([('tv', 1)])
    assert store._location_stock(db.inventory_products.find_one({'_id': ids['rice']}), str(ids['location'])) == (10, 5)
    assert db.inventory_branch_locations.find_one({'_id': ids['location']})['stock_units'] == 10
    record = db.inventory_delivery_deductions.find_one()
    assert record['status'] == 'not_deducted' and record['total_cost_value'] == 0
    movement = db.inventory_delivery_movements.find_one({'movement_type': 'card_recipe_stock_return'})
    assert movement['deducted_quantity'] == -1 and movement['total_cost'] == -5
    edit([('tv', 1)])
    assert db.inventory_delivery_movements.count_documents({}) == 2


def test_increase_complete_card_requires_only_extra_units_to_be_settled(flow):
    db, ids, transition, edit = flow
    edit([('rice', 1)])
    transition()
    transition('delivering')
    edit([('rice', 3)])
    record = db.inventory_delivery_deductions.find_one()
    assert record['components'][0]['deducted_quantity'] == 1
    assert record['remaining_units'] == 2
    with pytest.raises(store.StockDeductionError, match='Settle all outstanding'):
        transition('delivered')
    assert transition(settle=True, request_id=str(uuid4()))['deducted_now'] == 2
    transition('delivered')
    assert store._location_stock(db.inventory_products.find_one({'_id': ids['rice']}), str(ids['location']))[0] == 7


def test_replacement_returns_old_stock_and_settles_new_component_once(flow):
    db, ids, transition, edit = flow
    transition()
    edit([('tv', 1)])
    db.inventory_products.update_one({'_id': ids['tv']}, {'$push': {'entries': {
        'location_id': str(ids['location']), 'quantity': 1, 'cost_price': 20,
        'created_at': datetime(2026, 1, 2)}}})
    db.inventory_branch_locations.update_one({'_id': ids['location']}, {'$inc': {'stock_units': 1}})
    result = transition(settle=True, request_id=str(uuid4()))
    assert result['deducted_now'] == 1 and result['status'] == 'deducted'
    transition('delivered')
    record = db.inventory_delivery_deductions.find_one()
    assert record['total_units'] == 1 and record['total_cost_value'] == 20
    assert store._location_stock(db.inventory_products.find_one({'_id': ids['rice']}), str(ids['location']))[0] == 10
    assert sum(m['deducted_quantity'] for m in db.inventory_delivery_movements.find()) == 1


def test_reduction_returns_latest_warehouse_allocation(flow):
    db, ids, transition, edit = flow
    edit([('rice', 12)])
    db.inventory_products.update_one({'_id': ids['rice']}, {'$push': {'entries': {
        'location_id': str(ids['second']), 'quantity': 5, 'cost_price': 8, 'created_at': datetime(2026, 1, 2)}}})
    db.inventory_branch_locations.update_one({'_id': ids['second']}, {'$set': {'stock_units': 5}})
    transition()
    edit([('rice', 9)])
    record = db.inventory_delivery_deductions.find_one()
    assert record['total_units'] == 9 and record['total_cost_value'] == 45
    assert record['components'][0]['allocations'][0]['quantity'] == 9
    assert store._location_stock(db.inventory_products.find_one({'_id': ids['rice']}), str(ids['second']))[0] == 5
    assert sum(m['deducted_quantity'] for m in db.inventory_delivery_movements.find()) == 9


def test_delivered_packages_and_stock_are_unchanged(flow):
    db, ids, transition, edit = flow
    edit([('rice', 1)])
    transition()
    transition('delivered')
    package, record = db.packages.find_one(), db.inventory_delivery_deductions.find_one()
    assert edit([('tv', 1)])['updatedPackages'] == 0
    assert db.packages.find_one() == package
    assert db.inventory_delivery_deductions.find_one() == record
    assert db.inventory_delivery_movements.count_documents({}) == 1


def test_edit_failure_rolls_back_card_stock_and_deductions(flow):
    db, ids, transition, edit = flow
    transition()
    before = {name: list(db[name].find()) for name in db.list_collection_names()}
    with patch.object(store.movements_col, 'insert_one', side_effect=RuntimeError('write failed')):
        with pytest.raises(ValueError, match='write failed'):
            edit([('tv', 1)])
    for name, rows in before.items():
        assert list(db[name].find()) == rows


def test_pending_card_quantity_and_duplicate_components_are_preserved(flow):
    db, ids, transition, edit = flow
    db.packages.update_one({'_id': ids['package']}, {'$set': {'qty': 2}})
    edit([('rice', 1), ('rice', 2)])
    assert db.inventory_delivery_deductions.count_documents({}) == 0
    assert transition()['components'][0]['required_quantity'] == 6
    assert db.inventory_delivery_deductions.find_one()['total_units'] == 6


def test_updated_recipe_overrides_historical_outflow(flow):
    db, ids, transition, edit = flow
    transition()
    db.inventory_products_outflow.insert_one({'customer_id': ids['customer'], 'packaged_product_index': 0,
        'source': 'Agent_deliveries', 'product_def': deepcopy(db.products.find_one()),
        'components_deducted': db.inventory_delivery_deductions.find_one()['components']})
    edit([('rice', 1)])
    recipe, _ = store.resolve_recipe(db.packages.find_one())
    assert recipe['source'] == 'updated_card_recipe' and len(recipe['components']) == 1
    assert db.inventory_products_outflow.find_one()['components_status'] == 'deducted'


def test_missing_original_allocation_blocks_edit_without_losing_stock(flow):
    db, ids, transition, edit = flow
    transition()
    record = db.inventory_delivery_deductions.find_one()
    record['components'][0]['allocations'] = []
    db.inventory_delivery_deductions.replace_one({'_id': record['_id']}, record)
    product = db.products.find_one()
    with pytest.raises(ValueError, match='incomplete warehouse allocations'):
        edit([('tv', 1)])
    assert db.products.find_one() == product
    assert db.inventory_delivery_movements.count_documents({}) == 1
