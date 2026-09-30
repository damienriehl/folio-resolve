"""Optional adapters report missing extras without hiding broken installations."""

import builtins
import sys

import pytest

import folio_resolve
from folio_resolve.embedding import LocalEmbeddingProvider
from folio_resolve.ontology import FolioPythonProvider

ADAPTERS = [
    ("folio", "folio", "FolioNotInstalledError", lambda: FolioPythonProvider().all_labels()),
    ("sentence_transformers", "embedding", "EmbeddingNotInstalledError", LocalEmbeddingProvider),
]


@pytest.mark.parametrize("module,extra,error_name,load", ADAPTERS)
def test_missing_extra_has_installation_message(monkeypatch, module, extra, error_name, load):
    monkeypatch.setitem(sys.modules, module, None)
    with pytest.raises(ImportError) as caught:
        load()
    assert type(caught.value).__name__ == error_name
    assert isinstance(caught.value, getattr(folio_resolve, error_name))
    assert error_name in folio_resolve.__all__
    assert f'pip install "folio-resolve[{extra}]"' in str(caught.value)
    assert isinstance(caught.value.__cause__, ModuleNotFoundError)
    assert caught.value.__cause__.name == module


@pytest.mark.parametrize("module,extra,error_name,load", ADAPTERS)
@pytest.mark.parametrize("failure", ["transitive", "submodule", "import_error"])
def test_broken_import_surfaces_unchanged(monkeypatch, module, extra, error_name, load, failure):
    if failure == "import_error":
        original = ImportError("incompatible installed API")
    else:
        name = "missing_dependency" if failure == "transitive" else f"{module}.missing"
        original = ModuleNotFoundError(f"No module named {name!r}", name=name)
    real_import = builtins.__import__

    def broken_import(name, *args, **kwargs):
        if name == module:
            raise original
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", broken_import)
    with pytest.raises(ImportError) as caught:
        load()
    assert caught.value is original


@pytest.mark.parametrize("module,extra,error_name,load", ADAPTERS)
def test_missing_extra_preserves_module_not_found_fallback(
    monkeypatch, module, extra, error_name, load
):
    original = ModuleNotFoundError("missing optional package", name=module, path="optional/path")
    real_import = builtins.__import__

    def missing(name, *args, **kwargs):
        if name == module:
            raise original
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", missing)
    try:
        load()
    except ModuleNotFoundError as exc:
        assert type(exc).__name__ == error_name
        assert exc.name == module
        assert exc.path == original.path
        assert exc.__cause__ is original
    else:
        pytest.fail("Existing ModuleNotFoundError fallback did not run")
