"""Imports every module in app/models so all tables are registered on Base.metadata (Alembic needs the full set)."""

import importlib
import pkgutil

import app.models as models_package


def import_all_models() -> None:
    for module in pkgutil.iter_modules(models_package.__path__):
        importlib.import_module(f"app.models.{module.name}")
