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
