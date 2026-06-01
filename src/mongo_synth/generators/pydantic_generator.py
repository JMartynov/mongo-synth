from typing import Any, Dict
from mongo_synth.generators.base import BaseGenerator

class PydanticGenerator(BaseGenerator):
    def __init__(self, blueprint: Dict[str, Any], documents_per_collection: int, seed: Any = None):
        model = blueprint.get("model")
        model_path = blueprint.get("model_path")
        
        if not model and model_path:
            model = self._load_model_by_path(model_path)
            
        if not model:
            raise ValueError("PydanticGenerator requires 'model' or 'model_path' in the blueprint.")
            
        schema = self._translate_pydantic_to_schema(model)
        
        # Merge translated schema into the blueprint
        blueprint["schema"] = schema
        super().__init__(blueprint, documents_per_collection, seed=seed)

    def _load_model_by_path(self, model_path: str) -> Any:
        import importlib
        import sys
        import os
        
        if os.getcwd() not in sys.path:
            sys.path.insert(0, os.getcwd())
            
        if ":" in model_path:
            module_path, class_name = model_path.rsplit(":", 1)
        elif "." in model_path:
            module_path, class_name = model_path.rsplit(".", 1)
        else:
            raise ValueError(f"Invalid model path: {model_path}. Must be in the format 'module.submodule.ClassName' or 'module.submodule:ClassName'.")
            
        try:
            module = importlib.import_module(module_path)
        except ImportError as e:
            raise ImportError(f"Could not import module '{module_path}' for model path '{model_path}': {e}")
            
        if not hasattr(module, class_name):
            raise AttributeError(f"Module '{module_path}' has no attribute '{class_name}'")
            
        return getattr(module, class_name)

    def _translate_pydantic_to_schema(self, model_class: Any) -> Dict[str, Any]:
        if hasattr(model_class, "model_json_schema"):
            return model_class.model_json_schema()
        elif hasattr(model_class, "schema"):
            return model_class.schema()
        else:
            raise TypeError(f"Provided class {model_class} does not appear to be a Pydantic model.")
