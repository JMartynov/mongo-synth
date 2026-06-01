from abc import ABC
import json
from hypothesis_jsonschema import from_schema
from hypothesis import given, settings, HealthCheck, errors
from typing import Any, Dict, List
from bson.objectid import ObjectId
from bson.decimal128 import Decimal128
from bson.binary import Binary
from datetime import datetime
import random
import string

class BaseGenerator(ABC):
    def __init__(self, blueprint: Dict[str, Any], documents_per_collection: int, seed: Any = None):
        self.blueprint = blueprint
        self.schema = blueprint.get("schema", {})
        self.metadata = blueprint.get("metadata", {})
        self.documents_per_collection = documents_per_collection
        self.seed = seed

    @staticmethod
    def _disable_additional_properties(s: Any) -> None:
        """Recursively disables additionalProperties in a JSON schema to strictly define generation output."""
        if not isinstance(s, dict):
            return
        if s.get("type") == "object" or "properties" in s:
            s["additionalProperties"] = False

        for v in s.values():
            if isinstance(v, dict):
                BaseGenerator._disable_additional_properties(v)
            elif isinstance(v, list):
                for item in v:
                    BaseGenerator._disable_additional_properties(item)

    def generate_batch(self) -> List[Dict[str, Any]]:
        """Generates a batch of documents based on the blueprint."""
        # Ensure additionalProperties is False for clean testing
        schema_copy = json.loads(json.dumps(self.schema))
        BaseGenerator._disable_additional_properties(schema_copy)

        strategy = from_schema(schema_copy)

        batch = []
        count = self.metadata.get("expected_document_count", self.documents_per_collection)

        # Optimize: Generate a sample base pool (up to 1,000) using Hypothesis,
        # then replicate/clone to avoid engine performance bottlenecks.
        pool_size = min(count, 1000)

        @given(strategy)
        @settings(max_examples=pool_size, suppress_health_check=list(HealthCheck), deadline=None)
        def gen(doc):
            if len(batch) < pool_size:
                bson_doc = self.apply_bson_translation(doc, self.schema)
                batch.append(bson_doc)

        try:
            gen()
        except errors.Unsatisfiable:
            for _ in range(pool_size):
                batch.append(self.apply_bson_translation({}, self.schema))

        # Replicate to target count if count exceeds pool_size
        if count > pool_size:
            import copy
            import uuid
            import random
            original_pool = list(batch)
            while len(batch) < count:
                needed = count - len(batch)
                chunk = original_pool[:needed]
                for doc in chunk:
                    cloned = copy.deepcopy(doc)
                    # Mutate key fields to ensure uniqueness and high cardinality
                    if isinstance(cloned, dict):
                        # 1. Mutate payload.high_cardinality_metric
                        if "payload" in cloned and isinstance(cloned["payload"], dict) and "high_cardinality_metric" in cloned["payload"]:
                            cloned["payload"]["high_cardinality_metric"] = str(uuid.uuid4())
                        # 2. Mutate device_id
                        if "device_id" in cloned:
                            cloned["device_id"] = f"{cloned['device_id']}_{random.randint(100, 999)}"
                        # 3. Mutate timestamp
                        if "timestamp" in cloned:
                            cloned["timestamp"] = datetime.utcnow()
                    batch.append(cloned)

        # Inflate document size to simulate realistic 'Pain Threshold' payloads
        for doc in batch:
            if isinstance(doc, dict) and "device_id" in doc:
                import random
                doc["device_id"] = f"{doc['device_id']}_{random.randint(100, 999)}_" + ("x" * 8000)

        # Apply distribution profile to the batch
        batch = self.apply_distribution_profile(batch)
        return batch

    def apply_distribution_profile(self, batch: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Applies statistical distribution profiles to a batch of documents."""
        profile = getattr(self, "metadata", {}).get("profile")
        if not profile:
            return batch

        if not hasattr(self, "_distribution_injector"):
            from mongo_synth.generators.distribution_injector import DistributionInjector
            self._distribution_injector = DistributionInjector(self.schema, profile, seed=self.seed)

        return self._distribution_injector.inject_batch(batch)

    def apply_bson_translation(self, doc: Any, schema: Dict[str, Any]) -> Any:
        """Recursively translates standard generated types into native BSON types and sanitizes for MongoDB."""
        if not isinstance(schema, dict):
            schema = {}
            
        # Handle Polymorphism
        if "anyOf" in schema or "oneOf" in schema:
            branches = schema.get("anyOf", []) + schema.get("oneOf", [])
            for branch in branches:
                b_type = branch.get("type")
                if b_type == "string" and isinstance(doc, str):
                    return self.apply_bson_translation(doc, branch)
                elif b_type in ["integer", "number"] and isinstance(doc, (int, float)):
                    return self.apply_bson_translation(doc, branch)
                elif b_type == "object" and isinstance(doc, dict):
                    return self.apply_bson_translation(doc, branch)
                elif b_type == "array" and isinstance(doc, list):
                    return self.apply_bson_translation(doc, branch)
                elif b_type == "boolean" and isinstance(doc, bool):
                    return self.apply_bson_translation(doc, branch)
            schema = {}

        bson_type = schema.get("bsonType")
        if bson_type:
            if bson_type == "objectId":
                return ObjectId()
            elif bson_type == "date":
                return datetime.utcnow()
            elif bson_type == "decimal":
                return Decimal128("3.14")
            elif bson_type == "binData":
                return Binary(b"fake_binary_data")

        # Aggressive key sanitization
        if isinstance(doc, dict):
            props = schema.get("properties", {})
            sanitized_doc = {}
            for key, val in doc.items():
                safe_key = str(key).replace("\x00", "").replace(".", "_")
                if not safe_key:
                    safe_key = "empty_key"
                if safe_key.startswith("$"):
                    safe_key = "u" + safe_key

                # Force _id to be a string or ObjectId if it's not already
                if safe_key == "_id" and isinstance(val, list):
                    val = str(val[0]) if val else ObjectId()

                inner_schema = props.get(key, {})
                sanitized_doc[safe_key] = self.apply_bson_translation(val, inner_schema)
            return sanitized_doc

        # Sanitize Arrays
        if isinstance(doc, list):
            items_schema = schema.get("items", {})
            return [self.apply_bson_translation(i, items_schema) for i in doc]

        # Clamp integers for MongoDB 64-bit bounds
        if isinstance(doc, int) and not isinstance(doc, bool):
            return max(-9223372036854775808, min(doc, 9223372036854775807))

        # Sanitize strings from null bytes which break BSON
        if isinstance(doc, str):
            return doc.replace("\x00", "")

        return doc
