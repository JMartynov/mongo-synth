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

def run_generation(args, parser):
    # Validate required arguments
    if not args.schema and not args.model:
        parser.error("one of the arguments --schema or --model is required for generate")

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

    # 3. Load blueprint / model
    anomaly_type = args.anomaly
    if args.model:
        blueprint = {"model_path": args.model, "metadata": {}}
    else:
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
    if anomaly_type and not args.model:
        blueprint["schema"]["anomaly_type"] = anomaly_type

    # Configure blueprint metadata expected count
    if "metadata" not in blueprint:
        blueprint["metadata"] = {}
    blueprint["metadata"]["expected_document_count"] = count

    inject_sensitive = args.inject_sensitive if args.inject_sensitive else generator_config.get("generation.inject_sensitive", False)
    run_id = args.run_id if args.run_id else generator_config.get("generation.run_id", None)
    sensitive_locale = args.sensitive_locale if args.sensitive_locale else generator_config.get("generation.sensitive_locale", None)
    verifier_output = args.verifier_output if args.verifier_output else generator_config.get("generation.verifier_output", None)

    blueprint["metadata"]["inject_sensitive"] = inject_sensitive
    blueprint["metadata"]["run_id"] = run_id
    blueprint["metadata"]["sensitive_locale"] = sensitive_locale

    # 4. Instantiate Generator
    if args.model:
        logger.info(f"Instantiating PydanticGenerator for model: {args.model}")
        from mongo_synth.generators.pydantic_generator import PydanticGenerator
        generator = PydanticGenerator(blueprint, count, seed=seed)
        if anomaly_type:
            logger.info(f"Wrapping Pydantic schema in AnomalyGenerator for anomaly: {anomaly_type}")
            blueprint["schema"]["anomaly_type"] = anomaly_type
            generator = AnomalyGenerator(blueprint, count, seed=seed)
    else:
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

    # Export verifier list
    if verifier_output:
        tracker = getattr(generator, "sensitive_tracker", None)
        if tracker:
            logger.info(f"Writing {len(tracker.verifiers)} verifiers to {verifier_output}...")
            try:
                with open(verifier_output, "w") as f:
                    json.dump(tracker.verifiers, f, indent=2)
                logger.info("Verifier list written successfully.")
            except Exception as e:
                logger.error(f"Failed to write verifier file: {e}")
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

def run_validation(args):
    from mongo_synth.validation.validator import (
        StructuralValidator,
        SubschemaValidator,
        FunctionalValidator,
        SimilarityValidator,
        ProjectedFunctionalValidator,
        PrecisionValidator,
    )

    validators = {
        "structural": StructuralValidator,
        "subschema": SubschemaValidator,
        "functional": FunctionalValidator,
        "similarity": SimilarityValidator,
        "projected": ProjectedFunctionalValidator,
        "precision": PrecisionValidator,
    }

    # Load ground truth schema
    logger.info(f"Loading ground truth schema from: {args.schema}")
    try:
        with open(args.schema, "r") as f:
            gt_schema = json.load(f)
    except Exception as e:
        logger.error(f"Failed to read ground truth schema {args.schema}: {e}")
        sys.exit(1)

    # Load inferred schema
    logger.info(f"Loading inferred schema from: {args.inferred}")
    try:
        with open(args.inferred, "r") as f:
            inferred_schema = json.load(f)
    except Exception as e:
        logger.error(f"Failed to read inferred schema {args.inferred}: {e}")
        sys.exit(1)

    # Select validator
    validator_class = validators.get(args.validator)
    if not validator_class:
        logger.error(f"Unknown validator type: {args.validator}")
        sys.exit(1)

    logger.info(f"Running validation with: {args.validator}")
    validator = validator_class()
    try:
        result = validator.validate(inferred_schema, gt_schema)
    except Exception as e:
        logger.error(f"Validation execution failed: {e}", exc_info=True)
        sys.exit(1)

    print(f"\n=== Validation Result (Method: {args.validator}) ===")
    if result.get("valid"):
        print("✅ VALID: The inferred schema conforms to the ground truth.")
        for k, v in result.items():
            if k not in ["valid", "method"]:
                print(f"  {k}: {v}")
        sys.exit(0)
    else:
        print("❌ INVALID: The inferred schema does not conform to the ground truth.")
        for k, v in result.items():
            if k not in ["valid", "method"]:
                print(f"  {k}: {v}")
        sys.exit(1)
def run_verify_leak(args, parser):
    import os
    # 1. Load verifiers file
    logger.info(f"Loading verifier file: {args.verifier_file}")
    try:
        with open(args.verifier_file, "r") as f:
            verifiers = json.load(f)
    except Exception as e:
        logger.error(f"Failed to read verifiers file {args.verifier_file}: {e}")
        sys.exit(1)

    if not isinstance(verifiers, list):
        logger.error("Verifier file must contain a JSON list of objects.")
        sys.exit(1)

    # Map value -> type to check leaks efficiently
    verifier_map = {}
    for entry in verifiers:
        if isinstance(entry, dict) and "value" in entry and "type" in entry:
            verifier_map[str(entry["value"])] = entry["type"]

    if not verifier_map:
        logger.warning("No verifiers found in the verifier file.")
        print("No verifiers to scan for.")
        sys.exit(0)

    # 2. Collect target files
    target = args.target
    files_to_scan = []
    if os.path.isfile(target):
        files_to_scan.append(target)
    elif os.path.isdir(target):
        for root, _, filenames in os.walk(target):
            for filename in filenames:
                files_to_scan.append(os.path.join(root, filename))
    else:
        logger.error(f"Target path '{target}' is not a valid file or directory.")
        sys.exit(1)

    # 3. Scan files
    leaks = []
    for filepath in files_to_scan:
        logger.debug(f"Scanning: {filepath}")
        try:
            with open(filepath, "r", encoding="utf-8", errors="ignore") as f:
                for line_idx, line in enumerate(f, 1):
                    for val, val_type in verifier_map.items():
                        if val in line:
                            leaks.append({
                                "file": filepath,
                                "line": line_idx,
                                "type": val_type,
                                "value": val
                            })
        except Exception as e:
            logger.warning(f"Could not read file {filepath}: {e}")

    # 4. Report leaks and exit appropriately
    if leaks:
        print(f"\n❌ LEAK DETECTED! Found {len(leaks)} sensitive data leak(s):")
        for leak in leaks:
            masked = leak["value"]
            if len(masked) > 8:
                masked = masked[:3] + "..." + masked[-3:]
            print(f"  - [{leak['type']}] Value '{masked}' found in {leak['file']} on line {leak['line']}")
        sys.exit(1)
    else:
        print("\n✅ SECURE: No sensitive data leaks detected in the scanned target.")
        sys.exit(0)

def main():
    # Insert 'generate' subcommand if legacy flags are used directly
    if len(sys.argv) > 1 and sys.argv[1] not in ["generate", "validate", "verify-leak", "-h", "--help"]:
        sys.argv.insert(1, "generate")

    parser = argparse.ArgumentParser(description="MongoDB Schema-Based Data Generator CLI")
    subparsers = parser.add_subparsers(dest="command", help="Sub-commands")

    # --- GENERATE SUBCOMMAND ---
    gen_parser = subparsers.add_parser("generate", help="Generate and ingest synthetic data")
    gen_parser.add_argument("--schema", help="Path to JSON Schema or Blueprint JSON file")
    gen_parser.add_argument("--model", help="Python path to Pydantic model class (e.g. my_module.MyModel)")
    gen_parser.add_argument("--uri", help="MongoDB connection URI (e.g. mongodb://localhost:27017)")
    gen_parser.add_argument("--db", help="Target database name")
    gen_parser.add_argument("--collection", help="Target collection name")
    gen_parser.add_argument("--count", type=int, help="Number of records to generate")
    gen_parser.add_argument("--batch-size", type=int, help="Batch size for bulk insertion")
    gen_parser.add_argument("--seed", type=int, help="Deterministic master seed")
    gen_parser.add_argument("--profile", help="Path to a JSON file containing statistical probability profiles")
    gen_parser.add_argument("--anomaly", help="Inject a specific structural/type anomaly")
    gen_parser.add_argument("--clear", action="store_true", help="Clear the target collection before insertion")
    gen_parser.add_argument("--inject-sensitive", action="store_true", help="Automatically inject sensitive PII/secrets fields into all documents")
    gen_parser.add_argument("--verifier-output", help="Path to write the JSON leak verifier list file")
    gen_parser.add_argument("--run-id", help="Canary run identifier to prefix/salt sensitive values with")
    gen_parser.add_argument("--sensitive-locale", help="Locale for generating synthetic PII (e.g. en_US, de_DE)")
    gen_parser.add_argument("--live-uri", help="Live URI to check safety lock against")
    gen_parser.add_argument("--config-file", help="Path to a YAML configuration file to pre-populate parameters")
    gen_parser.add_argument("--verbose", action="store_true", help="Enable verbose DEBUG logging")

    # --- VALIDATE SUBCOMMAND ---
    val_parser = subparsers.add_parser("validate", help="Validate inferred schema against ground truth")
    val_parser.add_argument("--schema", required=True, help="Path to ground truth schema JSON file")
    val_parser.add_argument("--inferred", required=True, help="Path to inferred schema JSON file")
    val_parser.add_argument(
        "--validator", 
        choices=["structural", "subschema", "functional", "similarity", "projected", "precision"],
        default="structural",
        help="Type of validation to run (default: structural)"
    )
    val_parser.add_argument("--verbose", action="store_true", help="Enable verbose DEBUG logging")

    # --- VERIFY LEAK SUBCOMMAND ---
    leak_parser = subparsers.add_parser("verify-leak", help="Scan files or directories for leaks using a verifiers list")
    leak_parser.add_argument("--verifier-file", required=True, help="Path to the JSON leak verifier list file")
    leak_parser.add_argument("--target", required=True, help="Path to the file or directory to scan for leaks")
    leak_parser.add_argument("--verbose", action="store_true", help="Enable verbose DEBUG logging")

    args = parser.parse_args()

    # Default command is generate if none specified
    if not args.command:
        parser.print_help()
        sys.exit(0)

    if args.verbose:
        logging.getLogger().setLevel(logging.DEBUG)

    if args.command == "generate":
        run_generation(args, gen_parser)
    elif args.command == "validate":
        run_validation(args)
    elif args.command == "verify-leak":
        run_verify_leak(args, leak_parser)

if __name__ == "__main__":
    main()

