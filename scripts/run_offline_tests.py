"""Isolated test runner: python -m scripts.run_offline_tests tests/... -q."""
import socket
import sys
from types import ModuleType
from unittest.mock import patch

import mongomock
import pytest


def forbidden(*args, **kwargs):
    raise RuntimeError('Network/database connections are forbidden in offline tests')


_socket_connect = socket.socket.connect


def loopback_only(sock, address):
    host = address[0] if isinstance(address, tuple) else ''
    if host in {'127.0.0.1', '::1', 'localhost'}:
        return _socket_connect(sock, address)
    return forbidden()


def loopback_connection(address, *args, **kwargs):
    host = address[0] if isinstance(address, tuple) else ''
    if host in {'127.0.0.1', '::1', 'localhost'}:
        return _create_connection(address, *args, **kwargs)
    return forbidden()


_create_connection = socket.create_connection


if __name__ == '__main__':
    database = mongomock.MongoClient().offline_tests
    module = ModuleType('db')
    module.db = database
    module.client = database.client
    # Legacy modules import collection aliases from db.py.
    module.__getattr__ = lambda name: database[name.removesuffix('_collection')] if name.endswith('_collection') else (_ for _ in ()).throw(AttributeError(name))
    sys.modules['db'] = module
    with patch('pymongo.MongoClient', forbidden), patch('pymongo.mongo_client.MongoClient', forbidden), patch.object(socket.socket, 'connect', loopback_only), patch('socket.create_connection', loopback_connection):
        raise SystemExit(pytest.main(sys.argv[1:]))
