import docker
import time
import logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("behave.environment")

def before_scenario(context, scenario):
    context.mongo_uri = None
    context.mongo_container = None
    context.client = docker.from_env()

def after_scenario(context, scenario):
    import os
    if hasattr(context, "mongo_container") and context.mongo_container:
        try:
            logger.info(f"Stopping and removing MongoDB container: {context.mongo_container.name}")
            context.mongo_container.stop(timeout=5)
            context.mongo_container.remove()
        except Exception as e:
            logger.error(f"Error tearing down container: {e}")
            
    # Clean up temporary BDD files
    for filename in [
        "features/schemas/temp_verifiers.json",
        "features/schemas/app_log.txt",
        "features/schemas/temp_verifiers_clean.json",
        "features/schemas/clean_app_log.txt"
    ]:
        if os.path.exists(filename):
            try:
                os.unlink(filename)
            except Exception:
                pass

