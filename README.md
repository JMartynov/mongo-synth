# mongo-synth: MongoDB Schema-Based Data Generator & Ingester

`mongo-synth` is a standalone Python utility and command-line tool designed to generate high-fidelity, deterministic synthetic datasets from **JSON Schemas** (or Pydantic models) and seed them directly into **MongoDB** collections at scale. 

Whether you are performing database index optimization, latency stress testing, schema validation, or writing integration tests, `mongo-synth` allows you to rapidly populate mock databases with realistic data, statistical distributions, and edge-case anomalies.

---

## Key Features

* 🧬 **JSON Schema Synthesis**: Translates arbitrary JSON Schema specifications (Draft 2020-12) into deterministic property-based generation strategies using `hypothesis-jsonschema`.
* 🍃 **Native BSON Type Mapping**: Supports MongoDB-specific types (`ObjectId`, `ISODate`, `Decimal128`, `BinData`) via custom `"bsonType"` schema annotations.
* 📊 **Statistical Value Profiling**: Inject real-world data properties by defining relative probability weights for specific fields (e.g., status field containing 80% `active` / 20% `inactive`).
* ⚡ **High-Performance Bulk Ingestion**: Iterates over generated streams and inserts them in configurable batch chunks via PyMongo's unordered `insert_many` for maximum throughput.
* 🚨 **Anomaly & Schema Drift Injection**: Test system resilience under fire by injecting whitespace key anomalies, mixed-type arrays, extreme nesting depths, emojis, or string type impersonations.
* 🔒 **Production Safety Lock**: Protects production environments by automatically asserting connection strings against a configured live database URI and blocking execution on a match.

---

## Installation

```bash
pip install .
```

---

## Quick Start

### 1. CLI Usage

Generate and ingest 10,000 orders into a local database using a schema:

```bash
mongo-synth \
  --schema path/to/order_schema.json \
  --uri mongodb://localhost:27017 \
  --db testing_db \
  --collection orders \
  --count 10000 \
  --clear
```

### 2. Python API Usage

```python
from pymongo import MongoClient
from mongo_synth.generators import JsonSchemaGenerator
from mongo_synth.ingestion import DataIngester

# 1. Define your blueprint and schema
blueprint = {
    "schema": {
        "type": "object",
        "properties": {
            "_id": {"type": "string", "bsonType": "objectId"},
            "device_id": {"type": "string"},
            "status": {"type": "string", "enum": ["online", "offline"]},
            "timestamp": {"type": "string", "bsonType": "date"}
        },
        "required": ["device_id", "status"]
    },
    "metadata": {
        "profile": {
            "status": {"online": 0.9, "offline": 0.1} # 90% online, 10% offline
        }
    }
}

# 2. Generate synthetic data
generator = JsonSchemaGenerator(blueprint, documents_per_collection=5000, seed=42)
documents = generator.generate_batch()

# 3. Bulk ingest into MongoDB
client = MongoClient("mongodb://localhost:27017")
collection = client["iot_db"]["devices"]

ingester = DataIngester(
    target_collection=collection,
    target_uri="mongodb://localhost:27017",
    batch_size=1000,
    live_source_uri="mongodb+srv://prod-cluster" # Safety guardrail
)

inserted_count = ingester.ingest(documents)
print(f"Successfully seeded {inserted_count} documents.")
```

---

## 🔒 Sensitive Data Generation & Honeytoken Leak Verification

`mongo-synth` supports generating dynamic, high-fidelity Personally Identifiable Information (PII) and credentials (passwords, API keys) that can be seeded into MongoDB collections.

This feature is **disabled by default** to ensure clean testing, but can be enabled on-demand.

### Why this feature exists
Organizations need to periodically audit their staging, development, and production environments for compliance (GDPR, HIPAA, PCI-DSS) and security leaks. Rather than using real production data (which introduces security risks and privacy compliance violations), security teams utilize **Honeytokens**—realistic, synthetic records that act as tripwires. 

If any of the generated honeytoken values (like an API key or password) are detected in system logs, external search indexing engines, code repositories, or public paste sites, it serves as a high-confidence indicator of a data breach.

### Real-World Customer Use Cases
*   **Compliance Audit & Data Redaction**: Verify that system logging frameworks, crash reporting tools, or APMs (Application Performance Monitors) correctly redact or mask sensitive PII (like Social Security Numbers or Credit Cards) before storing them in logs.
*   **Leak Detection & Alerting (Honeytokens)**: Seed databases with custom API keys and passwords. Configure downstream monitoring tools (like SIEMs, Splunk, or DLP scanners) to watch for these exact values. If a value appears outside the database, alert security teams immediately.
*   **Accidental Production Writes Identification**: Use the `--run-id` option to prefix all sensitive values. If a value prefix is seen in logs, you can identify exactly which pipeline run or branch was responsible.
*   **Unique Index Integrity Testing**: Test that database index behaviors, constraints, and ingestion pipelines handle large volumes of high-cardinality values with graceful bulk writes.

### How to Use

#### 1. Schema-Driven Generation
Annotate any string properties in your JSON Schema with `"sensitiveType"`:
*   Supported types: `name`, `email`, `phone`, `ssn`, `credit_card`, `address`, `password`, `api_key`.

```json
{
  "type": "object",
  "properties": {
    "username": {"type": "string"},
    "personal_email": {"type": "string", "sensitiveType": "email"},
    "api_token": {"type": "string", "sensitiveType": "api_key"}
  },
  "required": ["username", "personal_email", "api_token"]
}
```

When generating, these fields will be populated using standard libraries (Faker for PII, cryptographically secure `secrets` module for credentials).

#### 2. Automatic CLI-Driven Injection (`--inject-sensitive`)
To automatically append a set of standard sensitive fields (including nested `personal_info`, `billing`, and `credentials` sub-documents) to every document generated, use the `--inject-sensitive` flag:
```bash
mongo-synth generate \
  --schema path/to/schema.json \
  --count 1000 \
  --inject-sensitive
```

#### 3. Canary Run Tagging (`--run-id`) & Localization (`--sensitive-locale`)
*   **Canary Prefixing**: Prefix generated values with a custom ID (e.g., pipeline run number or environment name) to trace the origin of a leak:
```bash
mongo-synth generate \
  --schema path/to/schema.json \
  --count 1000 \
  --inject-sensitive \
  --run-id dev_stage_pipeline_94
```
This prefixes names, emails, and passwords with `dev_stage_pipeline_94_` and salts API keys like `key_live_dev_stage_pipeline_94_...`.

*   **PII Localization**: Specify a locale (default `en_US`) to generate localized synthetic names, addresses, and phone formats (e.g., `de_DE`, `fr_FR`, `en_GB`):
```bash
mongo-synth generate \
  --schema path/to/schema.json \
  --count 1000 \
  --inject-sensitive \
  --sensitive-locale de_DE
```

#### 4. Leak Verifiers Export (`--verifier-output`)
Export the list of all generated sensitive values to a structured JSON file to act as the leak audit checklist:
```bash
mongo-synth generate \
  --schema path/to/schema.json \
  --count 100 \
  --inject-sensitive \
  --run-id audit_run_1 \
  --verifier-output verifier_checklist.json
```
Example `verifier_checklist.json`:
```json
[
  {
    "type": "email",
    "value": "audit_run_1_john.doe@example.com"
  },
  {
    "type": "api_key",
    "value": "key_live_audit_run_1_f8b2c4d9a..."
  }
]
```
### Ingestion Robustness, Safety & Performance Options

When generating and inserting millions of mock documents, several database-level constraints, schema constraints, and payload limits must be handled safely:

1. **Ordered Ingestion (`--ordered`)**:
   By default, `mongo-synth` performs unordered bulk writes (`ordered=False`) to maximize write speed and ignore duplicate key violations (MongoDB error codes `11000`/`11001`) dynamically. 
   If you require sequential database insertions where the exact order of documents matters (e.g., time-series data or referenced keys), use the `--ordered` flag. This will enforce sequential inserts and immediately halt execution on the first error.

2. **Client-Side Schema Validation Dry Run (`--dry-run`)**:
   Under large-scale runs, sending invalid BSON structures or documents that violate schema constraints to MongoDB is slow and requires manual database cleanup. 
   Use the `--dry-run` flag to generate mock documents and run the JSON Schema validator locally client-side using `jsonschema` without connecting or writing to MongoDB.

3. **Dynamic Batch Resizing (Network Safeties)**:
   By default, `mongo-synth` bulk-inserts documents in batch size chunks (default `5000`). If your schema generates extremely large records (e.g., deeply nested subdocuments, large arrays, or long texts), a large batch can exceed MongoDB's maximum 16MB BSON payload limit.
   The ingestion pipeline automatically samples the BSON size of generated documents during initial ingestion and dynamically scales down the batch size if needed to fit under a safe 12MB limit.

4. **Parallel Ingestion Workers (`--workers`)**:
   Python's single-threaded execution can limit both CPU generation throughput and I/O write speed. 
   Use the `--workers W` flag (with `W > 1`) to generate and ingest documents concurrently in `W` isolated processes. When running in parallel:
   * The total count is split evenly across workers.
   * If a master seed is provided, seeds are offset (`master_seed + worker_index`) to guarantee that workers generate distinct datasets.
   * Target collection clearing (`--clear`) is coordinated once by the parent process.
   * Leak verifiers are collected and merged across all workers.

Any structural errors (such as MongoDB Schema Document Validation failures, error code `121`) are **re-raised immediately** to prevent silent configuration or constraint validation bugs.

```
