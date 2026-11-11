import os
from pathlib import Path

from dotenv import load_dotenv

from pymongo.mongo_client import MongoClient
from services.performance import DatabaseTimings
from pymongo.server_api import ServerApi




# Resolve relative to this file so startup works from any working directory.
# Existing deployment environment variables take priority over local .env values.
load_dotenv(Path(__file__).resolve().with_name(".env"), override=False)


def _mongo_uri() -> str:
    uri = os.environ.get("MONGODB_URI") or os.environ.get("MONGO_URI")
    if not uri:
        raise RuntimeError("Set MONGODB_URI in the environment or the app's .env file")
    return uri


def _mongo_db_name() -> str:
    name = os.environ.get("MONGODB_DB") or os.environ.get("MONGO_DB_NAME")
    if not name:
        raise RuntimeError("Set MONGODB_DB in the environment or the app's .env file")
    return name


client = MongoClient(
    _mongo_uri(),
    event_listeners=[DatabaseTimings()],
    server_api=ServerApi("1"),
    maxPoolSize=int(os.environ.get("MONGO_MAX_POOL_SIZE", "100")),
    minPoolSize=int(os.environ.get("MONGO_MIN_POOL_SIZE", "0")),
    connectTimeoutMS=int(os.environ.get("MONGO_CONNECT_TIMEOUT_MS", "10000")),
    socketTimeoutMS=int(os.environ.get("MONGO_SOCKET_TIMEOUT_MS", "30000")),
    serverSelectionTimeoutMS=int(os.environ.get("MONGO_SERVER_SELECTION_TIMEOUT_MS", "10000")),
    retryWrites=True,
)

try:
    client.admin.command("ping")
    print("Connected to MongoDB!")
except Exception as e:
    print("MongoDB connection failed:", e)

from services.index_management import ManagedDatabase
db = ManagedDatabase(client, _mongo_db_name())

users_collection = db["users"]
tasks_collection = db["tasks"]
