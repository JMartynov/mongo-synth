from abc import ABC
import json
from hypothesis_jsonschema import from_schema
from hypothesis import given, settings, HealthCheck, errors
from typing import Any, Dict, List, Tuple
from bson.objectid import ObjectId
from bson.decimal128 import Decimal128
from bson.binary import Binary
from datetime import datetime
import random
import string
from mongo_synth.generators.sensitive import SensitiveDataTracker

class BaseGenerator(ABC):
    def __init__(self, blueprint: Dict[str, Any], documents_per_collection: int, seed: Any = None):
        self.blueprint = blueprint
        self.schema = blueprint.get("schema", {})
        self.metadata = blueprint.get("metadata", {})
        self.documents_per_collection = documents_per_collection
        self.seed = seed
        
        run_id = self.metadata.get("run_id")
        self.sensitive_tracker = SensitiveDataTracker(run_id=run_id)

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
            high_card_fields = self._find_high_cardinality_fields(self.schema)
            original_pool = list(batch)
            while len(batch) < count:
                needed = count - len(batch)
                chunk = original_pool[:needed]
                for doc in chunk:
                    cloned = copy.deepcopy(doc)
                    # Dynamically mutate high-cardinality/unique fields
                    if isinstance(cloned, dict):
                        for path, field_schema in high_card_fields:
                            self._mutate_nested_value(cloned, path, field_schema)
                    batch.append(cloned)

        # Apply distribution profile to the batch
        batch = self.apply_distribution_profile(batch)

        # Auto-inject sensitive PII if enabled
        if self.metadata.get("inject_sensitive", False):
            batch = [self.sensitive_tracker.auto_inject(doc) for doc in batch]

        return batch

    def _find_high_cardinality_fields(self, schema: Dict[str, Any], current_path: List[str] = None) -> List[Tuple[List[str], Dict[str, Any]]]:
        """Traverses the schema to find fields that require uniqueness or high cardinality."""
        if current_path is None:
            current_path = []
            
        if not isinstance(schema, dict):
            return []
            
        fields = []
        is_unique = False
        
        # 1. Any field named "_id" must be unique
        if current_path and current_path[-1] == "_id":
            is_unique = True
            
        # 2. Schema formats that imply high cardinality
        fmt = schema.get("format")
        if fmt in ("uuid", "uuid4"):
            is_unique = True
            
        # 3. Custom properties for uniqueness/cardinality
        if schema.get("unique") or schema.get("uniqueItems") or schema.get("cardinality") in ("high", "unique"):
            is_unique = True
            
        # 4. bsonType annotations that should be refreshed
        bson_type = schema.get("bsonType")
        if bson_type in ("objectId", "date", "timestamp", "regex", "decimal", "binData"):
            is_unique = True
            
        # 5. sensitiveType annotations should be treated as unique/high-cardinality
        if schema.get("sensitiveType"):
            is_unique = True
            
        if is_unique and current_path:
            fields.append((current_path, schema))
            
        # Recurse into properties
        if schema.get("type") == "object" or "properties" in schema:
            props = schema.get("properties", {})
            for prop_name, prop_schema in props.items():
                fields.extend(self._find_high_cardinality_fields(prop_schema, current_path + [prop_name]))
                
        return fields

    def _mutate_nested_value(self, doc: Any, path: List[str], schema: Dict[str, Any]) -> None:
        """Mutates a nested field value along a specified key path."""
        if not isinstance(doc, dict):
            return
        
        if len(path) == 1:
            key = path[0]
            if key in doc:
                doc[key] = self._generate_unique_value(doc[key], schema)
            return
            
        key = path[0]
        if key in doc and isinstance(doc[key], dict):
            self._mutate_nested_value(doc[key], path[1:], schema)

    def _generate_unique_value(self, current_value: Any, schema: Dict[str, Any]) -> Any:
        """Generates a new, unique value based on the field schema and current value type."""
        sensitive_type = schema.get("sensitiveType")
        if sensitive_type:
            return self.sensitive_tracker.generate_value(sensitive_type)

        bson_type = schema.get("bsonType")
        if bson_type == "objectId":
            return ObjectId()
        elif bson_type == "date":
            return datetime.utcnow()
        elif bson_type == "timestamp":
            from bson.timestamp import Timestamp
            import time
            import random
            sys_rand = random.SystemRandom()
            return Timestamp(int(time.time()) + sys_rand.randint(1, 1000), sys_rand.randint(1, 100))
        elif bson_type == "regex":
            from bson.regex import Regex
            import uuid
            return Regex(f"^pattern_{uuid.uuid4().hex[:6]}_{uuid.uuid4().hex[:6]}$")
        elif bson_type == "decimal":
            import random
            sys_rand = random.SystemRandom()
            return Decimal128(f"{sys_rand.randint(-1000, 1000)}.{sys_rand.randint(10, 99)}")
        elif bson_type == "binData":
            import os
            return Binary(os.urandom(16))
        elif bson_type == "double":
            import random
            sys_rand = random.SystemRandom()
            return float(sys_rand.uniform(-1000.0, 1000.0))
        elif bson_type == "long":
            from bson.int64 import Int64
            import random
            sys_rand = random.SystemRandom()
            return Int64(sys_rand.randint(-9223372036854775808, 9223372036854775807))
        
        fmt = schema.get("format")
        if fmt in ("uuid", "uuid4"):
            import uuid
            return str(uuid.uuid4())
        
        # Fallback type-based mutations to ensure uniqueness
        if isinstance(current_value, str):
            import uuid
            if len(current_value) == 36 and current_value.count("-") == 4:
                return str(uuid.uuid4())
            suffix = f"_{uuid.uuid4().hex[:6]}"
            max_len = schema.get("maxLength")
            if max_len is not None:
                base = current_value[:max_len - len(suffix)]
                return base + suffix
            return current_value + suffix
            
        elif isinstance(current_value, int) and not isinstance(current_value, bool):
            import random
            sys_rand = random.SystemRandom()
            val = current_value + sys_rand.randint(1, 10000)
            max_val = schema.get("maximum")
            if max_val is not None and val > max_val:
                min_val = schema.get("minimum", 0)
                val = sys_rand.randint(min_val, max_val)
            return val
            
        elif isinstance(current_value, float):
            import random
            sys_rand = random.SystemRandom()
            val = current_value + sys_rand.uniform(0.1, 100.0)
            max_val = schema.get("maximum")
            if max_val is not None and val > max_val:
                min_val = schema.get("minimum", 0.0)
                val = sys_rand.uniform(min_val, max_val)
            return val
            
        elif isinstance(current_value, ObjectId):
            return ObjectId()
            
        elif isinstance(current_value, datetime):
            import datetime as dt
            import random
            return current_value + dt.timedelta(seconds=random.randint(1, 60))
            
        elif isinstance(current_value, Decimal128):
            import random
            return Decimal128(f"{random.randint(10, 99)}.{random.randint(10, 99)}")
            
        elif isinstance(current_value, Binary):
            import uuid
            return Binary(uuid.uuid4().bytes)
            
        return current_value

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
            
        sensitive_type = schema.get("sensitiveType")
        if sensitive_type:
            return self.sensitive_tracker.generate_value(sensitive_type)
            
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

        # Check standard unique/high-cardinality formats and custom annotations during translation
        fmt = schema.get("format")
        if fmt in ("uuid", "uuid4"):
            import uuid
            return str(uuid.uuid4())

        if schema.get("unique") or schema.get("uniqueItems") or schema.get("cardinality") in ("high", "unique"):
            return self._generate_unique_value(doc, schema)

        bson_type = schema.get("bsonType")
        if bson_type:
            if bson_type == "objectId":
                return ObjectId()
            elif bson_type == "date":
                if isinstance(doc, datetime):
                    return doc
                elif isinstance(doc, (int, float)):
                    try:
                        return datetime.utcfromtimestamp(doc)
                    except Exception:
                        pass
                return datetime.utcnow()
            elif bson_type == "decimal":
                if isinstance(doc, (int, float)):
                    return Decimal128(str(doc))
                elif isinstance(doc, str):
                    try:
                        return Decimal128(doc)
                    except Exception:
                        pass
                import random
                return Decimal128(f"{random.randint(-1000, 1000)}.{random.randint(0, 99)}")
            elif bson_type == "binData":
                if isinstance(doc, bytes):
                    return Binary(doc)
                elif isinstance(doc, str):
                    return Binary(doc.encode("utf-8"))
                import os
                return Binary(os.urandom(16))
            elif bson_type == "double":
                if isinstance(doc, (int, float)):
                    return float(doc)
                import random
                return float(random.uniform(-1000.0, 1000.0))
            elif bson_type == "long":
                from bson.int64 import Int64
                if isinstance(doc, (int, float)):
                    return Int64(int(doc))
                import random
                return Int64(random.randint(-9223372036854775808, 9223372036854775807))
            elif bson_type == "timestamp":
                from bson.timestamp import Timestamp
                import time
                if isinstance(doc, (int, float)):
                    clamped_time = max(0, min(int(doc), 4294967295))
                    return Timestamp(clamped_time, 1)
                return Timestamp(int(time.time()), 1)
            elif bson_type == "regex":
                from bson.regex import Regex
                if isinstance(doc, str):
                    return Regex(doc)
                return Regex("^fake_pattern_[0-9]+$")

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
