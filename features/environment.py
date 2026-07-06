import docker
import time
import logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("behave.environment")

_mongo_container = None
_mongo_uri = None
_mongo_client = None

def before_all(context):
    context.client = docker.from_env()

def before_scenario(context, scenario):
    global _mongo_container, _mongo_uri, _mongo_client
    context.client = docker.from_env()
    
    # Populate the context with the active container details if they exist
    context.mongo_container = _mongo_container
    context.mongo_uri = _mongo_uri
    context.mongo_client = _mongo_client
    
    # Clean the database to guarantee clean state isolation between scenarios
    if _mongo_client:
        try:
            _mongo_client.drop_database("test_db")
        except Exception as e:
            logger.warning(f"Failed to drop test_db: {e}")

def after_scenario(context, scenario):
    global _mongo_container, _mongo_uri, _mongo_client
    # If the scenario spawned a container, store it globally for reuse
    if hasattr(context, "mongo_container") and context.mongo_container:
        _mongo_container = context.mongo_container
        _mongo_uri = context.mongo_uri
        _mongo_client = context.mongo_client

def after_all(context):
    global _mongo_container
    if _mongo_container:
        try:
            logger.info(f"Stopping and removing global MongoDB container: {_mongo_container.name}")
            _mongo_container.stop(timeout=5)
            _mongo_container.remove()
        except Exception as e:
            logger.error(f"Error tearing down global container: {e}")
