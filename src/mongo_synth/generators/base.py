from abc import ABC
import json
from hypothesis_jsonschema import from_schema
from hypothesis import given, settings, HealthCheck, errors
from typing import Any, Dict, List, Tuple
from bson.objectid import ObjectId
from bson.decimal128 import Decimal128
from bson.binary import Binary
from bson.regex import Regex
from datetime import datetime, timezone, timedelta
import random
import string
from mongo_synth.generators.sensitive import SensitiveDataTracker

class BaseGenerator(ABC):
    def __init__(self, blueprint: Dict[str, Any], documents_per_collection: int, seed: Any = None):
        self.blueprint = blueprint
        self.schema = blueprint.get("schema", {})
        self.metadata = blueprint.get("metadata", {})
        self.documents_per_collection = documents_per_collection
        if seed is not None:
            self.seed = seed
        else:
            try:
                from mongo_synth.config import generator_config
                self.seed = generator_config.get("generation.master_seed", 42)
            except Exception:
                self.seed = 42
        self._rng = random.Random(self.seed)
        
        run_id = self.metadata.get("run_id")
        locale = self.metadata.get("sensitive_locale")
        self.sensitive_tracker = SensitiveDataTracker(run_id=run_id, seed=self.seed, locale=locale)

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

    def _transform_enum_values(self, s: Any) -> None:
        """Recursively replaces enumValues with enum in a JSON schema if it is non-empty, and adds format: date-time for bsonType: date."""
        if not isinstance(s, dict):
            return
        if "enumValues" in s:
            ev = s["enumValues"]
            if isinstance(ev, list) and len(ev) > 0:
                s["enum"] = ev
        if s.get("bsonType") == "date" and "format" not in s:
            s["format"] = "date-time"
        for v in s.values():
            if isinstance(v, dict):
                self._transform_enum_values(v)
            elif isinstance(v, list):
                for item in v:
                    self._transform_enum_values(item)

    def generate_batch(self) -> List[Dict[str, Any]]:
        """Generates a batch of documents based on the blueprint."""
        if self.seed is not None:
            random.seed(self.seed)
        # Ensure additionalProperties is False for clean testing
        schema_copy = json.loads(json.dumps(self.schema))
        BaseGenerator._disable_additional_properties(schema_copy)
        self._transform_enum_values(schema_copy)

        strategy = from_schema(schema_copy)

        batch = []
        count = self.metadata.get("expected_document_count", self.documents_per_collection)

        # Optimize: Generate a sample base pool (up to 1,000) using Hypothesis,
        # then replicate/clone to avoid engine performance bottlenecks.
        pool_size = min(count, 1000)

        settings_kwargs = {
            "max_examples": pool_size,
            "suppress_health_check": list(HealthCheck),
            "deadline": None
        }
        if self.seed is not None:
            settings_kwargs["derandomize"] = True

        @given(strategy)
        @settings(**settings_kwargs)
        def gen(doc):
            if len(batch) < pool_size:
                bson_doc = self.apply_bson_translation(doc, self.schema)
                batch.append(bson_doc)

        try:
            gen()
        except errors.Unsatisfiable:
            for _ in range(pool_size):
                batch.append(self.apply_bson_translation({}, self.schema))

        # Dedup high cardinality fields in the base pool to ensure uniqueness from the start
        high_card_fields = self._find_high_cardinality_fields(self.schema)
        seen_values = {tuple(path): set() for path, _ in high_card_fields}
        for doc in batch:
            if isinstance(doc, dict):
                for path, field_schema in high_card_fields:
                    val = self._get_value_by_path(doc, path)
                    if val is not None:
                        path_tuple = tuple(path)
                        # Convert to comparison keys for unhashable types
                        comparison_val = val
                        if isinstance(val, (dict, list)):
                            comparison_val = json.dumps(val, sort_keys=True)
                        elif isinstance(val, Decimal128):
                            comparison_val = str(val)
                        elif isinstance(val, Binary):
                            comparison_val = bytes(val)
                        elif isinstance(val, Regex):
                            comparison_val = (val.pattern, val.flags)
                        
                        while comparison_val in seen_values[path_tuple]:
                            val = self._generate_unique_value(val, field_schema)
                            comparison_val = val
                            if isinstance(val, (dict, list)):
                                comparison_val = json.dumps(val, sort_keys=True)
                            elif isinstance(val, Decimal128):
                                comparison_val = str(val)
                            elif isinstance(val, Binary):
                                comparison_val = bytes(val)
                            elif isinstance(val, Regex):
                                comparison_val = (val.pattern, val.flags)
                        
                        seen_values[path_tuple].add(comparison_val)
                        self._set_value_by_path(doc, path, val)

        # Replicate to target count if batch size is less than count
        if len(batch) < count and len(batch) > 0:
            import copy
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

        # Apply percentileStats piecewise linear interpolation
        percentile_stats_input = (
            self.blueprint.get("percentileStats") or 
            self.schema.get("percentileStats") or 
            self.metadata.get("percentileStats")
        )
        if percentile_stats_input:
            percentile_stats_list = []
            if isinstance(percentile_stats_input, dict):
                percentile_stats_list = [percentile_stats_input]
            elif isinstance(percentile_stats_input, list):
                percentile_stats_list = percentile_stats_input

            for stats in percentile_stats_list:
                if not isinstance(stats, dict):
                    continue
                field_name = stats.get("fieldName")
                boundary_val = stats.get("boundaryValue")
                lower_percentile = stats.get("lowerPercentile")
                if field_name is None or boundary_val is None or lower_percentile is None:
                    continue

                path = field_name.split(".")
                
                # Gather indices and non-null values
                non_null_indices = []
                non_null_values = []
                for idx, doc in enumerate(batch):
                    val = self._get_value_by_path(doc, path)
                    if val is not None:
                        non_null_indices.append(idx)
                        non_null_values.append(val)
                
                if not non_null_values:
                    continue
                
                # Check if it is datetime or numeric
                is_date = all(isinstance(v, datetime) for v in non_null_values)
                
                if is_date:
                    # Convert boundary value to datetime if it is a string
                    b_val = boundary_val
                    if isinstance(b_val, str):
                        try:
                            if b_val.endswith("Z"):
                                b_val = b_val[:-1] + "+00:00"
                            b_val = datetime.fromisoformat(b_val)
                        except ValueError:
                            pass
                    
                    if isinstance(b_val, datetime):
                        if b_val.tzinfo is None:
                            b_val = b_val.replace(tzinfo=timezone.utc)
                        b_num = b_val.timestamp()
                    else:
                        b_num = float(b_val)
                    
                    numeric_values = [(v.replace(tzinfo=timezone.utc).timestamp(), idx) for idx, v in enumerate(non_null_values)]
                    numeric_values.sort(key=lambda x: x[0])
                else:
                    numeric_values = [(float(v), idx) for idx, v in enumerate(non_null_values)]
                    numeric_values.sort(key=lambda x: x[0])
                    b_num = float(boundary_val)
                
                N_val = len(numeric_values)
                M_val = int(N_val * lower_percentile)
                
                Min_val = numeric_values[0][0]
                Max_val = numeric_values[-1][0]
                
                interpolated_numeric = [0.0] * N_val
                for i in range(N_val):
                    if i < M_val:
                        if M_val > 0:
                            val_num = Min_val + (b_num - Min_val) * (i / M_val)
                        else:
                            val_num = b_num
                    else:
                        if N_val - M_val > 0:
                            val_num = b_num + (Max_val - b_num) * ((i - M_val) / (N_val - M_val))
                        else:
                            val_num = b_num
                    
                    orig_idx = numeric_values[i][1]
                    interpolated_numeric[orig_idx] = val_num
                
                # Map back to types
                interpolated_values = []
                field_schema = self._get_schema_by_path(self.schema, path)
                is_int = False
                if field_schema:
                    if field_schema.get("type") == "integer" or field_schema.get("bsonType") in ("int", "long"):
                        is_int = True
                
                for num_v in interpolated_numeric:
                    if is_date:
                        interpolated_values.append((datetime(1970, 1, 1, tzinfo=timezone.utc) + timedelta(seconds=num_v)).replace(tzinfo=None))
                    elif is_int:
                        interpolated_values.append(int(round(num_v)))
                    else:
                        interpolated_values.append(num_v)
                
                # Update documents
                for idx, orig_pos in enumerate(non_null_indices):
                    self._set_value_by_path(batch[orig_pos], path, interpolated_values[idx])

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
        enum_values = schema.get("enumValues")
        if enum_values and isinstance(enum_values, list) and len(enum_values) > 0:
            schema_id = id(schema)
            if not hasattr(self, "_unique_counters"):
                self._unique_counters = {}
            if schema_id not in self._unique_counters:
                self._unique_counters[schema_id] = 0
            
            token = None
            if isinstance(current_value, str):
                for ev in enum_values:
                    if current_value.endswith(ev):
                        token = ev
                        break
            if token is None:
                token = self._rng.choice(enum_values)
                
            prefix = f"{self._unique_counters[schema_id]}_"
            self._unique_counters[schema_id] += 1
            return prefix + token

        sensitive_type = schema.get("sensitiveType")
        if sensitive_type:
            return self.sensitive_tracker.generate_value(sensitive_type)

        bson_type = schema.get("bsonType")
        if bson_type == "objectId":
            return ObjectId()
        elif bson_type == "date":
            return datetime.now(timezone.utc).replace(tzinfo=None)
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
        profile = getattr(self, "metadata", {}).get("profile") or {}
        distribution = getattr(self, "metadata", {}).get("distribution") or {}
        merged_profile = {**profile, **distribution}
        if not merged_profile:
            return batch

        if not hasattr(self, "_distribution_injector"):
            from mongo_synth.generators.distribution_injector import DistributionInjector
            self._distribution_injector = DistributionInjector(self.schema, merged_profile, seed=self.seed)

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
                elif isinstance(doc, str):
                    try:
                        if doc.endswith("Z"):
                            doc = doc[:-1] + "+00:00"
                        return datetime.fromisoformat(doc).replace(tzinfo=None)
                    except ValueError:
                        pass
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

    def _get_value_by_path(self, doc: Any, path: List[str]) -> Any:
        if not path:
            return doc
        if not isinstance(doc, dict):
            return None
        key = path[0]
        if len(path) == 1:
            return doc.get(key)
        return self._get_value_by_path(doc.get(key), path[1:])

    def _set_value_by_path(self, doc: Any, path: List[str], val: Any) -> None:
        if not path or not isinstance(doc, dict):
            return
        key = path[0]
        if len(path) == 1:
            doc[key] = val
            return
        if key not in doc or not isinstance(doc[key], dict):
            doc[key] = {}
        self._set_value_by_path(doc[key], path[1:], val)

    def _get_schema_by_path(self, schema: Any, path: List[str]) -> Any:
        if not path:
            return schema
        if not isinstance(schema, dict):
            return None
        
        key = path[0]
        properties = schema.get("properties", {})
        if key in properties:
            return self._get_schema_by_path(properties[key], path[1:])
            
        if schema.get("type") == "array" or "items" in schema:
            return self._get_schema_by_path(schema.get("items", {}), path)
            
        return None
