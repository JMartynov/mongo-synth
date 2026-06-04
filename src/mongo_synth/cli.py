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

def _run_generation_worker(worker_idx, task_count, seed, args_dict, blueprint):
    """
    Worker function executed in a separate process.
    """
    import os
    import sys
    import json
    import logging
    from pymongo import MongoClient
    from mongo_synth.generators.json_schema_generator import JsonSchemaGenerator
    from mongo_synth.generators.anomaly_generator import AnomalyGenerator
    from mongo_synth.ingestion.data_ingester import DataIngester

    # Set up basic logging for child process
    logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s")
    logger = logging.getLogger(f"mongo_synth.cli.worker_{worker_idx}")

    blueprint = json.loads(json.dumps(blueprint))  # Deep copy/sanitize
    if "metadata" not in blueprint:
        blueprint["metadata"] = {}
    blueprint["metadata"]["expected_document_count"] = task_count

    anomaly_type = args_dict.get("anomaly")
    model = args_dict.get("model")

    if model:
        logger.info(f"Worker {worker_idx}: Instantiating PydanticGenerator for model: {model}")
        from mongo_synth.generators.pydantic_generator import PydanticGenerator
        generator = PydanticGenerator(blueprint, task_count, seed=seed)
        if anomaly_type:
            logger.info(f"Worker {worker_idx}: Wrapping Pydantic schema in AnomalyGenerator for anomaly: {anomaly_type}")
            blueprint["schema"]["anomaly_type"] = anomaly_type
            generator = AnomalyGenerator(blueprint, task_count, seed=seed)
    else:
        is_anomaly = anomaly_type or blueprint["schema"].get("anomaly_type")
        if is_anomaly:
            logger.info(f"Worker {worker_idx}: Instantiating AnomalyGenerator for anomaly type: {is_anomaly}")
            generator = AnomalyGenerator(blueprint, task_count, seed=seed)
        else:
            logger.info(f"Worker {worker_idx}: Instantiating JsonSchemaGenerator")
            generator = JsonSchemaGenerator(blueprint, task_count, seed=seed)

    try:
        documents = generator.generate_batch()
    except Exception as e:
        logger.error(f"Worker {worker_idx} failed to generate documents: {e}", exc_info=True)
        return {"success": False, "error": str(e)}

    verifiers = []
    tracker = getattr(generator, "sensitive_tracker", None)
    if tracker:
        verifiers = tracker.verifiers

    dry_run = args_dict.get("dry_run", False)
    if dry_run:
        import jsonschema
        from bson import json_util
        from mongo_synth.validation.validator import SchemaValidator
        
        normalizer = SchemaValidator()
        norm_schema = normalizer.normalize(generator.schema)
        
        valid_count = 0
        error_count = 0
        for idx, doc in enumerate(documents):
            serialized = json.loads(json_util.dumps(doc))
            try:
                jsonschema.validate(instance=serialized, schema=norm_schema)
                valid_count += 1
            except Exception as e:
                logger.error(f"Worker {worker_idx}: Document failed client-side validation: {e}")
                error_count += 1
        
        return {
            "success": True,
            "inserted_count": 0,
            "error_count": error_count,
            "verifiers": verifiers
        }

    uri = args_dict.get("uri")
    db_name = args_dict.get("db")
    collection_name = args_dict.get("collection")
    batch_size = args_dict.get("batch_size")
    live_uri = args_dict.get("live_uri")
    ordered = args_dict.get("ordered", False)

    logger.info(f"Worker {worker_idx}: Connecting to MongoDB...")
    try:
        client = MongoClient(uri, serverSelectionTimeoutMS=5000)
        db = client[db_name]
        collection = db[collection_name]
        ingester = DataIngester(collection, uri, batch_size=batch_size, live_source_uri=live_uri, ordered=ordered)
        inserted_count = ingester.ingest(documents)
        client.close()
        return {
            "success": True,
            "inserted_count": inserted_count,
            "error_count": 0,
            "verifiers": verifiers
        }
    except Exception as e:
        logger.error(f"Worker {worker_idx} failed to ingest: {e}", exc_info=True)
        return {"success": False, "error": str(e)}

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

    # Parse workers parameter
    workers_val = getattr(args, "workers", None)
    if isinstance(workers_val, int) and not isinstance(workers_val, bool):
        workers = workers_val
    else:
        workers = generator_config.get("generation.workers", 1)

    if isinstance(workers, int) and workers > 1:
        logger.info(f"Running parallel generation/ingestion with {workers} workers...")
        import multiprocessing
        
        dry_run_val = getattr(args, "dry_run", False)
        is_dry_run = isinstance(dry_run_val, bool) and dry_run_val
        
        ordered_val = getattr(args, "ordered", False)
        is_ordered = isinstance(ordered_val, bool) and ordered_val
        
        if not is_dry_run:
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
                    client.close()
                    sys.exit(1)
            client.close()

        # Divide count and seeds
        base_count = count // workers
        remainder = count % workers
        
        tasks = []
        for i in range(workers):
            task_count = base_count + (remainder if i == workers - 1 else 0)
            if task_count <= 0:
                continue
            task_seed = (seed + i) if seed is not None else None
            
            args_dict = {
                "anomaly": args.anomaly,
                "model": args.model,
                "dry_run": is_dry_run,
                "uri": uri,
                "db": db_name,
                "collection": collection_name,
                "batch_size": batch_size,
                "live_uri": live_uri,
                "ordered": is_ordered
            }
            tasks.append((i, task_count, task_seed, args_dict, blueprint))

        ctx = multiprocessing.get_context("spawn")
        pool = ctx.Pool(processes=workers)
        
        try:
            results = pool.starmap(_run_generation_worker, tasks)
        except Exception as e:
            logger.error(f"Parallel execution failed: {e}")
            pool.terminate()
            sys.exit(1)
        finally:
            pool.close()
            pool.join()

        total_inserted = 0
        total_errors = 0
        combined_verifiers = []
        
        for res in results:
            if not res.get("success", False):
                logger.error(f"Worker process failed: {res.get('error')}")
                sys.exit(1)
                return
            total_inserted += res.get("inserted_count", 0)
            total_errors += res.get("error_count", 0)
            combined_verifiers.extend(res.get("verifiers", []))

        # Export combined verifier list
        if verifier_output and combined_verifiers:
            logger.info(f"Writing {len(combined_verifiers)} combined verifiers to {verifier_output}...")
            try:
                with open(verifier_output, "w") as f:
                    json.dump(combined_verifiers, f, indent=2)
                logger.info("Verifier list written successfully.")
            except Exception as e:
                logger.error(f"Failed to write verifier file: {e}")
                sys.exit(1)

        if is_dry_run:
            print(f"\n✅ Dry run complete: Generated {count} documents.")
            print(f"   - Valid: {count - total_errors}")
            print(f"   - Invalid: {total_errors}")
            if total_errors > 0:
                sys.exit(1)
            sys.exit(0)
            return
        else:
            logger.info(f"Successfully ingested {total_inserted} documents.")
            print(f"\n✅ Parallel data generation and ingestion complete! {total_inserted} records inserted into {db_name}.{collection_name}")
            sys.exit(0)
            return

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

    # Dry-run validation
    dry_run = getattr(args, "dry_run", False)
    if isinstance(dry_run, bool) and dry_run:
        logger.info("Dry-run mode enabled. Running client-side schema validation on generated documents...")
        import jsonschema
        from bson import json_util
        from mongo_synth.validation.validator import SchemaValidator
        
        normalizer = SchemaValidator()
        norm_schema = normalizer.normalize(generator.schema)
        
        valid_count = 0
        error_count = 0
        for idx, doc in enumerate(documents):
            serialized = json.loads(json_util.dumps(doc))
            try:
                jsonschema.validate(instance=serialized, schema=norm_schema)
                valid_count += 1
            except Exception as e:
                logger.error(f"Document at index {idx} failed client-side validation: {e}")
                error_count += 1
        
        print(f"\n✅ Dry run complete: Generated {len(documents)} documents.")
        print(f"   - Valid: {valid_count}")
        print(f"   - Invalid: {error_count}")
        
        if error_count > 0:
            sys.exit(1)
        sys.exit(0)

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
        ordered_val = getattr(args, "ordered", False)
        ordered = ordered_val if isinstance(ordered_val, bool) and ordered_val else generator_config.get("generation.ordered", False)
        ingester = DataIngester(collection, uri, batch_size=batch_size, live_source_uri=live_uri, ordered=ordered)
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
def main():
    # Insert 'generate' subcommand if legacy flags are used directly
    if len(sys.argv) > 1 and sys.argv[1] not in ["generate", "validate", "-h", "--help"]:
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
    gen_parser.add_argument("--ordered", action="store_true", help="Enforce ordered bulk write ingestion (halts on first error)")
    gen_parser.add_argument("--dry-run", action="store_true", help="Generate and validate documents locally without writing to MongoDB")
    gen_parser.add_argument("--workers", type=int, help="Number of parallel generation/ingestion workers")
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

if __name__ == "__main__":
    main()


