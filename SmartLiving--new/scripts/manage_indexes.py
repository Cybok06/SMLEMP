"""Discover literal declarations without importing the app. Apply only a reviewed JSON plan.

python -m scripts.manage_indexes --discover > docs/index-plan.json
python -m scripts.manage_indexes --apply docs/index-plan.json
Dynamic declarations appear as unresolved and require manual review; never silently ignored.
"""
import argparse
import ast
import json
from pathlib import Path


def discover(root):
    indexes, unresolved = [], []
    class Directions(ast.NodeTransformer):
        def visit_Name(self, node):
            if node.id in {'ASCENDING', 'DESCENDING'}:
                return ast.copy_location(ast.Constant(1 if node.id == 'ASCENDING' else -1), node)
            return node
    for path in sorted(root.rglob('*.py')):
        if any(part in {'node_modules', '.test-tools', 'tests', '.venv', 'venv', 'scripts'} for part in path.parts):
            continue
        tree = Directions().visit(ast.parse(path.read_text(encoding='utf-8-sig')))
        collections = {}
        for node in ast.walk(tree):
            if isinstance(node, ast.Assign):
                value = node.value
                name = None
                if isinstance(value, ast.Subscript) and isinstance(value.value, ast.Name) and value.value.id == 'db':
                    try: name = ast.literal_eval(value.slice)
                    except (ValueError, TypeError): pass
                elif isinstance(value, ast.Attribute) and isinstance(value.value, ast.Name) and value.value.id == 'db':
                    name = value.attr
                if isinstance(name, str):
                    for target in node.targets:
                        if isinstance(target, ast.Name): collections[target.id] = name
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute) or node.func.attr != 'create_index':
                continue
            source = f'{path.relative_to(root).as_posix()}:{node.lineno}'
            try:
                receiver = node.func.value
                if isinstance(receiver, ast.Name):
                    collection = collections.get(receiver.id) or {'inventory_locations_col': 'inventory_branch_locations'}[receiver.id]
                elif isinstance(receiver, ast.Attribute) and isinstance(receiver.value, ast.Name) and receiver.value.id in {'db', 'database'}:
                    collection = receiver.attr
                elif isinstance(receiver, ast.Attribute) and isinstance(receiver.value, ast.Name) and receiver.value.id == 'store' and receiver.attr == 'movements_col':
                    collection = 'inventory_delivery_movements'
                elif isinstance(receiver, ast.Subscript) and isinstance(receiver.value, ast.Name) and receiver.value.id == 'db': collection = ast.literal_eval(receiver.slice)
                else: raise ValueError('dynamic collection')
                keys = ast.literal_eval(node.args[0])
                options = {kw.arg: ast.literal_eval(kw.value) for kw in node.keywords}
                indexes.append({'collection': collection, 'keys': keys, 'options': options, 'source': source})
            except (KeyError, ValueError, TypeError, IndexError):
                unresolved.append(source)
    return {'indexes': indexes, 'unresolved': unresolved}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument('--discover', action='store_true')
    group.add_argument('--apply', type=Path)
    args = parser.parse_args()
    if args.discover:
        print(json.dumps(discover(Path(__file__).resolve().parents[1]), indent=2))
        return 0
    plan = json.loads(args.apply.read_text(encoding='utf-8-sig'))
    if plan.get('unresolved'):
        raise SystemExit('Resolve every dynamic declaration before applying this plan.')
    from db import db
    # Use the ordinary collection class only in this explicit management command.
    from pymongo.collection import Collection
    failures = 0
    for item in plan['indexes']:
        try:
            keys = item['keys']
            if isinstance(keys, list): keys = [tuple(key) for key in keys]
            name = Collection(db, item['collection']).create_index(keys, **item.get('options', {}))
            print(json.dumps({'collection': item['collection'], 'name': name, 'ok': True}))
        except Exception as error:
            failures += 1
            print(json.dumps({'collection': item['collection'], 'ok': False, 'error_type': type(error).__name__, 'source': item.get('source')}))
    return 1 if failures else 0


if __name__ == '__main__':
    raise SystemExit(main())
