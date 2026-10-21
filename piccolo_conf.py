from os import environ
from pathlib import Path
from urllib.parse import urlparse

from dotenv import load_dotenv
from piccolo.conf.apps import AppRegistry
from piccolo.engine.postgres import PostgresEngine

load_dotenv(Path.cwd() / ".env")

db_url = environ.get("DATABASE_URL", "").strip()
parsed_db_url = urlparse(db_url)
if not db_url:
    raise ValueError("DATABASE_URL environment variable is not set")
if parsed_db_url.scheme not in {"postgres", "postgresql"}:
    raise ValueError(
        "DATABASE_URL must be a PostgreSQL URL starting with "
        "'postgres://' or 'postgresql://'"
    )
if not parsed_db_url.netloc:
    raise ValueError("DATABASE_URL must include a database host")

DB = PostgresEngine(
    config={"dsn": db_url},
    # Uncomment for debugging:
    # log_queries=True,
    # log_responses=True,
)

# A list of paths to piccolo apps
APP_REGISTRY = AppRegistry(apps=["nephthys.piccolo_app"])
