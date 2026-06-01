import argparse
import sys
import json
import logging
from typing import Dict, Any
from pymongo import MongoClient

from mongo_synth.config import generator_config
from mongo_synth.generators.json_schema_generator import JsonSchemaGenerator
from mongo_synth.generators.anomaly_generator import AnomalyGenerator
from mongo_synth.ingestion.data_ingester import DataIngester

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s")
logger = logging.getLogger("mongo_synth.cli")

def main():
    parser = argparse.ArgumentParser(description="MongoDB Schema-Based Data Generator CLI")
    parser.add_argument("--schema", required=True, help="Path to JSON Schema or Blueprint JSON file")
    parser.add_argument("--uri", help="MongoDB connection URI (e.g. mongodb://localhost:27017)")
    parser.add_argument("--db", help="Target database name")
    parser.add_argument("--collection", help="Target collection name")
    parser.add_argument("--count", type=int, help="Number of records to generate")
    parser.add_argument("--batch-size", type=int, help="Batch size for bulk insertion")
    parser.add_argument("--seed", type=int, help="Deterministic master seed")
    parser.add_argument("--profile", help="Path to a JSON file containing statistical probability profiles")
    parser.add_argument("--anomaly", help="Inject a specific structural/type anomaly")
    parser.add_argument("--clear", action="store_true", help="Clear the target collection before insertion")
    parser.add_argument("--live-uri", help="Live URI to check safety lock against")
    parser.add_argument("--config-file", help="Path to a YAML configuration file to pre-populate parameters")
    parser.add_argument("--verbose", action="store_true", help="Enable verbose DEBUG logging")

    args = parser.parse_args()

    if args.verbose:
        logging.getLogger().setLevel(logging.DEBUG)

    # 1. Load configuration from file if provided
    if args.config_file:
        generator_config.load_from_yaml(args.config_file)

    # 2. Parse overrides with fallback logic
    uri = args.uri or generator_config.get("mongodb.uri", "mongodb://localhost:27017")
    db_name = args.db or generator_config.get("mongodb.db_name", "generator_db")
    collection_name = args.collection or generator_config.get("mongodb.collection_name", "generator_collection")
    live_uri = args.live_uri or generator_config.get("mongodb.live_source_uri", "")
    
    count = args.count if args.count is not None else generator_config.get("generation.count", 1000)
    batch_size = args.batch_size if args.batch_size is not None else generator_config.get("generation.batch_size", 5000)
    seed = args.seed if args.seed is not None else generator_config.get("generation.master_seed", 42)

    # 3. Read and parse schema/blueprint file
    logger.info(f"Loading schema from: {args.schema}")
    try:
        with open(args.schema, "r") as f:
            raw_data = json.load(f)
    except Exception as e:
        logger.error(f"Failed to read schema file {args.schema}: {e}")
        sys.exit(1)

    # Detect if file is raw schema or wrapper blueprint
    if isinstance(raw_data, dict) and "schema" in raw_data:
        blueprint = raw_data
    else:
        blueprint = {"schema": raw_data, "metadata": {}}

    # Load external profile if provided
    if args.profile:
        try:
            with open(args.profile, "r") as f:
                profile_data = json.load(f)
                if "metadata" not in blueprint:
                    blueprint["metadata"] = {}
                blueprint["metadata"]["profile"] = profile_data
        except Exception as e:
            logger.error(f"Failed to read profile file {args.profile}: {e}")
            sys.exit(1)

    # Set anomaly in blueprint if requested via CLI
    anomaly_type = args.anomaly
    if anomaly_type:
        blueprint["schema"]["anomaly_type"] = anomaly_type

    # Configure blueprint metadata expected count
    if "metadata" not in blueprint:
        blueprint["metadata"] = {}
    blueprint["metadata"]["expected_document_count"] = count

    # 4. Instantiate Generator
    is_anomaly = anomaly_type or blueprint["schema"].get("anomaly_type")
    if is_anomaly:
        logger.info(f"Instantiating AnomalyGenerator for anomaly type: {is_anomaly}")
        generator = AnomalyGenerator(blueprint, count, seed=seed)
    else:
        logger.info("Instantiating JsonSchemaGenerator")
        generator = JsonSchemaGenerator(blueprint, count, seed=seed)

    # 5. Generate batch data
    logger.info(f"Generating {count} synthetic documents...")
    try:
        documents = generator.generate_batch()
    except Exception as e:
        logger.error(f"Failed to generate documents: {e}", exc_info=True)
        sys.exit(1)

    # 6. MongoDB Ingestion Setup
    logger.info(f"Connecting to MongoDB at {uri}...")
    try:
        client = MongoClient(uri, serverSelectionTimeoutMS=5000)
        client.admin.command("ping")
    except Exception as e:
        logger.error(f"Failed to connect to MongoDB: {e}")
        sys.exit(1)

    db = client[db_name]
    collection = db[collection_name]

    if args.clear:
        logger.info(f"Clearing collection '{collection_name}' before ingestion...")
        try:
            collection.delete_many({})
        except Exception as e:
            logger.error(f"Failed to clear collection: {e}")
            sys.exit(1)

    # 7. Ingest synthetic data
    logger.info(f"Ingesting documents into collection '{collection_name}' (batch_size={batch_size})...")
    try:
        ingester = DataIngester(collection, uri, batch_size=batch_size, live_source_uri=live_uri)
        inserted_count = ingester.ingest(documents)
        logger.info(f"Successfully ingested {inserted_count} documents.")
    except Exception as e:
        logger.error(f"Ingestion failed: {e}")
        sys.exit(1)

    print(f"\n✅ Data generation and ingestion complete! {inserted_count} records inserted into {db_name}.{collection_name}")

if __name__ == "__main__":
    main()
