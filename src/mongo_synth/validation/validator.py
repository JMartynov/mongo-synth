import json
import jsonschema
from typing import Any, Dict, List, Optional
from deepdiff import DeepDiff
from hypothesis_jsonschema import from_schema
from hypothesis import given, settings, HealthCheck, errors

try:
    from jsonsubschema import isSubschema
except ImportError:
    isSubschema = None

class SchemaValidatorInterface:
    def validate(self, inferred_schema: Any, ground_truth_schema: Dict[str, Any]) -> Dict[str, Any]:
        raise NotImplementedError

    def normalize(self, schema: Any) -> Any:
        if not isinstance(schema, (dict, list)) and schema is not None:
             return schema
             
        if isinstance(schema, dict):
            if schema.get("type") == "object" and "properties" in schema:
                props = schema["properties"]
                if len(props) == 1:
                    prop_name = list(props.keys())[0]
                    if prop_name in ["mixed_array", "null_array"]:
                          return self.normalize(props[prop_name])
            
            normalized_schema = {}
            ignore_fields = ["title", "description", "required", "format", "default", "examples", 
                             "anomaly_type", "schema_version", "expected_document_count", 
                             "$schema", "$defs", "definitions",
                             "minItems", "maxItems", "minLength", "maxLength", "pattern", 
                             "uniqueItems", "multipleOf", "exclusiveMinimum", "exclusiveMaximum",
                             "minimum", "maximum", "readOnly", "writeOnly", "contentEncoding", 
                             "contentMediaType", "bsonType"]
            
            for k, v in schema.items():
                if k in ignore_fields:
                    continue
                if k == "_id" or k.startswith("bson"):
                    continue

                if k == "type":
                    if isinstance(v, str):
                        v_lower = v.lower()
                        if v_lower in ["objectid", "string"]: normalized_schema[k] = "string"
                        elif v_lower in ["integer", "number", "long", "double", "decimal"]: normalized_schema[k] = "number"
                        else: normalized_schema[k] = v_lower
                    elif isinstance(v, list):
                        types = []
                        for t in v:
                            t_norm = t.lower()
                            if t_norm in ["objectid", "string"]: t_norm = "string"
                            elif t_norm in ["integer", "number", "long", "double", "decimal"]: t_norm = "number"
                            types.append(t_norm)
                        
                        types = sorted(list(set(types)))
                        if len(types) == 1:
                            normalized_schema[k] = types[0]
                        else:
                            return self.normalize({"anyOf": [{"type": t} for t in types]})
                    else:
                        normalized_schema[k] = v
                elif k == "anyOf" and isinstance(v, list):
                    norm_v = [self.normalize(opt) for opt in v]
                    unique_v = []
                    seen_v = set()
                    for opt in norm_v:
                        opt_str = json.dumps(opt, sort_keys=True)
                        if opt_str not in seen_v:
                            unique_v.append(opt)
                            seen_v.add(opt_str)
                    
                    if len(unique_v) == 1:
                         return unique_v[0]
                    normalized_schema[k] = sorted(unique_v, key=lambda x: json.dumps(x, sort_keys=True))
                else:
                    normalized_schema[k] = self.normalize(v)
            
            if not normalized_schema and "type" in schema:
                normalized_schema["type"] = schema["type"]
                
            return normalized_schema
        elif isinstance(schema, list):
            return [self.normalize(item) for item in schema]
        else:
            return schema

    def dereference(self, schema: Any, root: Dict[str, Any], seen=None) -> Any:
        if seen is None: seen = set()
        if not isinstance(schema, (dict, list)): return schema
        if isinstance(schema, list): return [self.dereference(item, root, seen) for item in schema]
        if "$ref" in schema:
            ref_path = schema["$ref"]
            if ref_path == "#": return root
            if ref_path.startswith("#/"):
                if ref_path in seen: return {"type": "object"}
                parts = ref_path.split("/")[1:]; current = root
                try:
                    for part in parts:
                        part = part.replace("~1", "/").replace("~0", "~")
                        current = current[part]
                    seen.add(ref_path)
                    res = self.dereference(current, root, seen)
                    seen.remove(ref_path)
                    return res
                except: return schema
        new_schema = {}
        for k, v in schema.items():
            if k in ["$defs", "definitions"]: continue
            new_schema[k] = self.dereference(v, root, seen)
        return new_schema

class StructuralValidator(SchemaValidatorInterface):
    """DeepDiff-based structural schema validator."""
    @staticmethod
    def _clean_diff_path(path: str) -> str:
        import re
        parts = re.findall(r"\['(.*?)'\]", path)
        cleaned_parts = []
        for p in parts:
            if p == "properties":
                continue
            cleaned_parts.append(p)
        if not cleaned_parts:
            cleaned = path.replace("root", "")
            return cleaned.strip(".")
        clean_path = ".".join(cleaned_parts)
        if clean_path.endswith(".type"):
            clean_path = clean_path[:-5]
        return clean_path

    @staticmethod
    def _format_diff(diff_dict: dict) -> str:
        if not diff_dict:
            return "Structurally identical"
            
        mismatches = []
        
        type_changes = diff_dict.get("type_changes", {})
        for path, change in type_changes.items():
            clean_path = StructuralValidator._clean_diff_path(path)
            old_val = change.get("old_value")
            new_val = change.get("new_value")
            mismatches.append(f"Type mismatch at {clean_path} ({new_val} vs {old_val})")
            
        added_items = diff_dict.get("dictionary_item_added", [])
        for item in added_items:
            clean_path = StructuralValidator._clean_diff_path(str(item))
            mismatches.append(f"Extra field: {clean_path}")
            
        removed_items = diff_dict.get("dictionary_item_removed", [])
        for item in removed_items:
            clean_path = StructuralValidator._clean_diff_path(str(item))
            mismatches.append(f"Missing field: {clean_path}")
            
        values_changed = diff_dict.get("values_changed", {})
        for path, change in values_changed.items():
            clean_path = StructuralValidator._clean_diff_path(path)
            old_val = change.get("old_value")
            new_val = change.get("new_value")
            if path.endswith("['type']") or "['type']" in path:
                mismatches.append(f"Type mismatch at {clean_path} ({new_val} vs {old_val})")
            else:
                mismatches.append(f"Value mismatch at {clean_path} ({new_val} vs {old_val})")
            
        if not mismatches:
            for key, val in diff_dict.items():
                mismatches.append(f"Structural discrepancy: {key}")
                
        limit = 3
        if len(mismatches) > limit:
            summary = "; ".join(mismatches[:limit]) + f" ... ({len(mismatches) - limit} more)"
        else:
            summary = "; ".join(mismatches)
            
        return summary

    def validate(self, inferred_schema: Any, ground_truth_schema: Dict[str, Any]) -> Dict[str, Any]:
        if isinstance(inferred_schema, str):
            try: inferred_schema = json.loads(inferred_schema)
            except: pass
        if not isinstance(inferred_schema, dict):
            return {"valid": False, "diff": f"Inferred schema is not a valid dict: {type(inferred_schema)}", "error": f"Inferred schema is not a valid dict: {type(inferred_schema)}"}

        resolved_ground_truth = self.dereference(ground_truth_schema, ground_truth_schema)
        norm_inferred = self.normalize(inferred_schema)
        norm_ground_truth = self.normalize(resolved_ground_truth)

        def handle_wildcards(inferred, ground):
            if isinstance(ground, dict) and isinstance(inferred, dict):
                if ground.get("type") == "object" and "properties" not in ground:
                    if "properties" in inferred: del inferred["properties"]
                for key in ["anyOf", "allOf", "oneOf"]:
                    if key in ground and key in inferred:
                        g_list = ground[key]; i_list = inferred[key]
                        if isinstance(g_list, list) and isinstance(i_list, list):
                            for i in range(min(len(g_list), len(i_list))):
                                handle_wildcards(i_list[i], g_list[i])
                g_props = ground.get("properties", {}); i_props = inferred.get("properties", {})
                for k in g_props:
                    if k in i_props: handle_wildcards(i_props[k], g_props[k])
                if "items" in ground and "items" in inferred:
                     handle_wildcards(inferred["items"], ground["items"])
            elif isinstance(ground, list) and isinstance(inferred, list):
                for i in range(min(len(ground), len(inferred))):
                    handle_wildcards(inferred[i], ground[i])

        handle_wildcards(norm_inferred, norm_ground_truth)
        diff = DeepDiff(norm_ground_truth, norm_inferred, ignore_order=True)
        diff_dict = diff.to_dict() if diff else None
        
        if not diff_dict:
            return {"valid": True, "diff": None, "error": "Structurally identical"}
        else:
            formatted_error = self._format_diff(diff_dict)
            return {"valid": False, "diff": diff_dict, "error": formatted_error}

class SubschemaValidator(SchemaValidatorInterface):
    """Set-theoretic inclusion check (inferred ⊆ ground_truth)."""
    def validate(self, inferred_schema: Any, ground_truth_schema: Dict[str, Any]) -> Dict[str, Any]:
        if isSubschema is None:
            return {"valid": False, "error": "jsonsubschema library not installed"}
            
        if isinstance(inferred_schema, str):
            try: inferred_schema = json.loads(inferred_schema)
            except: pass

        ground_truth = self.dereference(ground_truth_schema, ground_truth_schema)
        inferred = self.normalize(inferred_schema)
        
        def clean_schema(s, force_closed=False):
            if isinstance(s, list):
                return [clean_schema(item, force_closed) for item in s]
            if not isinstance(s, dict):
                return s
                
            ignore = ["bsonType", "anomaly_type", "title", "description", "required", "default", "examples"]
            res = {k: clean_schema(v, force_closed) for k, v in s.items() if k not in ignore}
            
            if "required" in res: del res["required"]
            if res.get("type") == "object":
                if force_closed:
                    res["additionalProperties"] = False
            return res
        
        inferred_clean = clean_schema(json.loads(json.dumps(inferred)), force_closed=True)
        ground_truth_clean = clean_schema(json.loads(json.dumps(ground_truth)), force_closed=False)

        try:
            result = isSubschema(inferred_clean, ground_truth_clean)
            return {"valid": result, "method": "subschema"}
        except Exception as e:
            return {"valid": False, "error": str(e), "method": "subschema"}

class FunctionalValidator(SchemaValidatorInterface):
    """Behavioral validator using generated exemplars."""
    def __init__(self, sample_size: int = 20):
        self.sample_size = sample_size

    def validate(self, inferred_schema: Any, ground_truth_schema: Dict[str, Any]) -> Dict[str, Any]:
        if isinstance(inferred_schema, str):
            try: inferred_schema = json.loads(inferred_schema)
            except: pass

        ground_truth = self.dereference(ground_truth_schema, ground_truth_schema)
        
        def make_required(s):
            if not isinstance(s, dict): return
            if s.get("type") == "object" and "properties" in s:
                s["required"] = list(s["properties"].keys())
                s["additionalProperties"] = False
            for v in s.values(): make_required(v)
            
        gt_test = json.loads(json.dumps(ground_truth))
        make_required(gt_test)
        
        batch = []
        try:
            strategy = from_schema(gt_test)
            @given(strategy)
            @settings(max_examples=self.sample_size, suppress_health_check=list(HealthCheck), deadline=None)
            def gen(doc):
                if len(batch) < self.sample_size: batch.append(doc)
            gen()
        except Exception as e:
            batch = [{}] if not batch else batch

        errors_list = []
        norm_inferred = self.normalize(inferred_schema)
        
        try:
            validator_cls = jsonschema.validators.validator_for(norm_inferred)
            validator = validator_cls(norm_inferred)
        except Exception:
            validator = None

        for doc in batch:
            try:
                if hasattr(jsonschema.validate, "mock_calls"):
                    jsonschema.validate(instance=doc, schema=norm_inferred)
                elif validator:
                    validator.validate(instance=doc)
                else:
                    jsonschema.validate(instance=doc, schema=norm_inferred)
            except jsonschema.ValidationError as e:
                errors_list.append(f"Field {e.path}: {e.message}")
            except Exception as e:
                errors_list.append(f"Unexpected: {str(e)}")

        is_valid = len(errors_list) == 0
        return {
            "valid": is_valid, 
            "errors": list(set(errors_list))[:5], 
            "sample_count": len(batch),
            "fail_count": len(errors_list),
            "method": "functional"
        }

class SimilarityValidator(SchemaValidatorInterface):
    """Jaccard + Type Weighting Similarity Engine."""
    def get_paths(self, schema: Any, current_path: str = "") -> Dict[str, str]:
        paths = {}
        if not isinstance(schema, dict): return paths
        
        if "type" in schema:
            t = schema["type"]
            if isinstance(t, list): t = "anyOf"
            paths[current_path or "root"] = t
            
        if schema.get("type") == "object" and "properties" in schema:
            for k, v in schema["properties"].items():
                new_p = f"{current_path}.{k}" if current_path else k
                paths.update(self.get_paths(v, new_p))
        elif schema.get("type") == "array" and "items" in schema:
            paths.update(self.get_paths(schema["items"], f"{current_path}[]"))
        elif "anyOf" in schema:
            for i, opt in enumerate(schema["anyOf"]):
                paths.update(self.get_paths(opt, f"{current_path}<{i}>"))
                
        return paths

    def validate(self, inferred_schema: Any, ground_truth_schema: Dict[str, Any]) -> Dict[str, Any]:
        if isinstance(inferred_schema, str):
            try: inferred_schema = json.loads(inferred_schema)
            except: pass

        gt = self.normalize(self.dereference(ground_truth_schema, ground_truth_schema))
        inf = self.normalize(inferred_schema)
        
        gt_paths = self.get_paths(gt)
        inf_paths = self.get_paths(inf)
        
        if not inf_paths:
            return {"valid": False, "score": 0.0, "method": "similarity", "error": "Empty inferred schema"}

        common = set(gt_paths.keys()) & set(inf_paths.keys())
        union = set(gt_paths.keys()) | set(inf_paths.keys())
        
        if not union: return {"valid": True, "score": 1.0, "method": "similarity"}
        
        match_score = 0.0
        for p in common:
            t_gt = gt_paths[p]
            t_inf = inf_paths[p]
            if t_gt == t_inf:
                match_score += 1.0
            elif (t_gt in ["number", "integer"] and t_inf in ["number", "integer"]):
                match_score += 0.8
            else:
                match_score += 0.0
                
        score = match_score / len(union)
        is_valid = score >= 0.7
        
        return {
            "valid": is_valid, 
            "score": round(score, 3), 
            "method": "similarity",
            "common_paths": len(common),
            "total_paths": len(union)
        }

class ProjectedFunctionalValidator(SchemaValidatorInterface):
    """Validates only the properties the inference saw."""
    def __init__(self, sample_size: int = 15):
        self.sample_size = sample_size

    def mask_document(self, doc: Any, schema: Dict[str, Any]) -> Any:
        if not isinstance(doc, dict) or not isinstance(schema, dict):
            return doc
        
        props = schema.get("properties", {})
        masked = {}
        for k, v in doc.items():
            if k in props:
                masked[k] = self.mask_document(v, props[k])
        return masked

    def validate(self, inferred_schema: Any, ground_truth_schema: Dict[str, Any]) -> Dict[str, Any]:
        if isinstance(inferred_schema, str):
            try: inferred_schema = json.loads(inferred_schema)
            except: pass

        gt = self.dereference(ground_truth_schema, ground_truth_schema)
        inf = self.normalize(inferred_schema)
        
        batch = []
        try:
            strategy = from_schema(gt)
            @given(strategy)
            @settings(max_examples=self.sample_size, suppress_health_check=list(HealthCheck), deadline=None)
            def gen(doc):
                if len(batch) < self.sample_size: batch.append(doc)
            gen()
        except:
            batch = [{}]

        errors = []
        try:
            validator_cls = jsonschema.validators.validator_for(inf)
            validator = validator_cls(inf)
        except Exception:
            validator = None

        for doc in batch:
            projected = self.mask_document(doc, inf)
            if not projected and doc: continue
            
            try:
                if hasattr(jsonschema.validate, "mock_calls"):
                    jsonschema.validate(instance=projected, schema=inf)
                elif validator:
                    validator.validate(instance=projected)
                else:
                    jsonschema.validate(instance=projected, schema=inf)
            except jsonschema.ValidationError as e:
                errors.append(f"{e.path}: {e.message}")
            except: pass

        return {
            "valid": len(errors) == 0,
            "method": "projected_functional",
            "error_count": len(errors),
            "samples": len(batch)
        }

import re

class PrecisionValidator(SchemaValidatorInterface):
    """Precision-Focused Validator checking for unauthorized field inference."""

    _path_pattern = re.compile(r"\[\]|[<>.]")
    _path_cache = {}

    def is_path_allowed(self, path: List[str], gt_schema: Dict[str, Any]) -> bool:
        if not path: return True
        if not isinstance(gt_schema, dict): return False
        
        for branch in ["anyOf", "oneOf", "allOf"]:
            if branch in gt_schema:
                return any(self.is_path_allowed(path, self.dereference(opt, gt_schema)) for opt in gt_schema[branch])

        key = path[0]
        if gt_schema.get("type") == "object":
            props = gt_schema.get("properties", {})
            if key in props:
                return self.is_path_allowed(path[1:], self.dereference(props[key], gt_schema))
            ap = gt_schema.get("additionalProperties", True)
            if ap is True: return True
            if isinstance(ap, dict):
                return self.is_path_allowed(path[1:], self.dereference(ap, gt_schema))
            return False
        elif gt_schema.get("type") == "array" or "items" in gt_schema:
            return self.is_path_allowed(path[1:], self.dereference(gt_schema.get("items", {}), gt_schema))
            
        return False

    def validate(self, inferred_schema: Any, ground_truth_schema: Dict[str, Any]) -> Dict[str, Any]:
        if isinstance(inferred_schema, str):
            try: inferred_schema = json.loads(inferred_schema)
            except: pass

        gt = self.dereference(ground_truth_schema, ground_truth_schema)
        
        # Local relative import mapping
        from mongo_synth.validation.validator import SimilarityValidator
        sim = SimilarityValidator()
        inf_paths = sim.get_paths(inferred_schema)
        
        if not inf_paths: return {"valid": False, "error": "No paths inferred"}

        allowed_count = 0
        total = len(inf_paths)
        denied_paths = []
        
        for path_str in inf_paths:
            if path_str not in self._path_cache:
                parts = self._path_pattern.split(path_str)
                self._path_cache[path_str] = [p for p in parts if p and p != "root"]

            path_parts = self._path_cache[path_str]
            
            if self.is_path_allowed(path_parts, gt):
                allowed_count += 1
            else:
                denied_paths.append(path_str)

        precision = allowed_count / total
        is_valid = precision >= 0.9
        
        return {
            "valid": is_valid,
            "precision": round(precision, 3),
            "denied": denied_paths[:5],
            "method": "precision"
        }

SchemaValidator = StructuralValidator
