# core/fms/__init__.py
# Forge Module System — discovery, validation, loading
from core.fms.loader import load_modules
from core.fms.validator import validate_manifest, validate_module_contract

__all__ = ["load_modules", "validate_manifest", "validate_module_contract"]