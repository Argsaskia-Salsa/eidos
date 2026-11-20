"""
Base StreamRegistry for any stream element (patterns, poses, beats, etc.).
Uses structural typing (SpecProtocol) - no inheritance required for custom elements.
"""

import importlib
import sys
from pathlib import Path
from typing import Callable, Protocol, runtime_checkable


@runtime_checkable
class SpecProtocol(Protocol):
    """Structural type for element specifications. Custom elements just need these attrs."""
    name: str
    func: Callable
    args: dict
    description: str


class StreamRegistry:
    """
    Generic registry for stream elements.
    
    Subclasses configure:
    - custom_dir: Path to custom elements directory
    - module_prefix: Python import prefix (e.g. "patterns.custom")
    - spec_attr: Attribute name to look for (default "PATTERN")
    
    Usage:
        class MyRegistry(StreamRegistry):
            custom_dir = Path(__file__).parent / "patterns" / "custom"
            module_prefix = "my_stream.patterns.custom"
            spec_attr = "PATTERN"
    """
    
    custom_dir: Path
    module_prefix: str
    spec_attr: str = "PATTERN"
    
    _BUILTIN_SPECS: dict[str, SpecProtocol] = {}
    _custom_specs: dict[str, SpecProtocol] = {}
    
    # Runtime registries - THE SINGLE SOURCE OF TRUTH
    ELEMENTS: dict[str, Callable] = {}
    ELEMENT_SPECS: dict[str, SpecProtocol] = {}
    
    @classmethod
    def register_builtin(cls, name: str, spec: SpecProtocol) -> None:
        """Register a built-in element specification."""
        if not callable(spec.func):
            raise ValueError(f"spec.func must be callable for {name}")
        cls._BUILTIN_SPECS[name] = spec
    
    @classmethod
    def load_custom(cls) -> dict[str, SpecProtocol]:
        """
        Load custom elements from custom_dir.
        Returns dict of name -> spec for newly loaded custom elements.
        Fixes hot-reload bug: checks hasattr(mod, spec_attr) on EVERY pass.
        """
        custom = {}
        
        if not cls.custom_dir.exists():
            return custom
        
        # Ensure custom directory parent is in Python path
        parent_dir = cls.custom_dir.parent
        if str(parent_dir) not in sys.path:
            sys.path.insert(0, str(parent_dir))
        
        for py_file in cls.custom_dir.glob("*.py"):
            if py_file.name.startswith("_"):
                continue
            module_name = f"{cls.module_prefix}.{py_file.stem}"
            try:
                if module_name in sys.modules:
                    importlib.reload(sys.modules[module_name])
                else:
                    importlib.import_module(module_name)
                mod = sys.modules[module_name]
                
                # Check for spec_attr export (SpecProtocol) - checked EVERY time
                if hasattr(mod, cls.spec_attr):
                    spec = getattr(mod, cls.spec_attr)
                    if isinstance(spec, SpecProtocol):
                        custom[spec.name] = spec
                        print(f"Loaded custom element: {spec.name}")
            except Exception as e:
                print(f"Failed to load custom element {py_file}: {e}")
        
        cls._custom_specs = custom
        return custom
    
    @classmethod
    def reload_all(cls) -> list[str]:
        """
        Reload all elements (builtins + custom).
        Callable at runtime for hot-reload.
        Returns list of all available element names.
        """
        # Reload builtins
        cls.ELEMENTS = {name: spec.func for name, spec in cls._BUILTIN_SPECS.items()}
        cls.ELEMENT_SPECS = dict(cls._BUILTIN_SPECS)
        
        # Load custom (override builtins if same name)
        custom = cls.load_custom()
        for name, spec in custom.items():
            cls.ELEMENTS[name] = spec.func
            cls.ELEMENT_SPECS[name] = spec
        
        all_names = list(cls.ELEMENTS.keys())
        print(f"Elements reloaded: {all_names}")
        return all_names
    
    @classmethod
    def get(cls, name: str) -> Callable | None:
        """Get element function by name."""
        return cls.ELEMENTS.get(name)
    
    @classmethod
    def specs(cls) -> dict[str, SpecProtocol]:
        """Get all element specifications."""
        return dict(cls.ELEMENT_SPECS)
    
    @classmethod
    def builtin_names(cls) -> list[str]:
        """Get names of built-in elements."""
        return list(cls._BUILTIN_SPECS.keys())
    
    @classmethod
    def custom_names(cls) -> list[str]:
        """Get names of custom elements."""
        return list(cls._custom_specs.keys())


# Initial load - happens once when module is first imported
# Subclasses should call reload_all() in their module init
# StreamRegistry.reload_all()