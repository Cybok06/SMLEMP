"""Defer index DDL from request/import paths; use a reviewed explicit plan."""
import logging
from pymongo.collection import Collection
from pymongo.database import Database

logger = logging.getLogger(__name__)
declarations = {}


class ManagedCollection(Collection):
    def create_index(self, keys, session=None, comment=None, **kwargs):
        normalized = [(keys, 1)] if isinstance(keys, str) else list(keys)
        name = kwargs.get('name') or '_'.join(f'{key}_{direction}' for key, direction in normalized)
        signature = (self.name, name)
        if signature not in declarations:
            declarations[signature] = {'collection': self.name, 'keys': normalized, 'options': kwargs}
            logger.info('index_deferred collection=%s name=%s', self.name, name)
        return name


class ManagedDatabase(Database):
    def __getitem__(self, name):
        return ManagedCollection(self, name)

    def get_collection(self, name, codec_options=None, read_preference=None, write_concern=None, read_concern=None):
        return ManagedCollection(self, name, codec_options=codec_options, read_preference=read_preference,
                                 write_concern=write_concern, read_concern=read_concern)
