"""dbt context for sqlfluff's jinja templater.

Linting renders models without dbt, so it never needs AWS credentials or a
warehouse (the dbt templater opens an Athena connection and lists relations
for every model). sqlfluff supplies `ref`, `source`, `config`, `return` and
`is_incremental` (always true); this module adds the rest of what the
project's macros use, with the values a default dbt run would see.
Every public name here becomes a template global.
"""

import datetime as _datetime
import re as _re
from pathlib import Path as _Path
from types import SimpleNamespace as _SimpleNamespace
from typing import Any as _Any
from typing import NoReturn as _NoReturn

import pytz as _pytz
import yaml as _yaml

_PROJECT_FILE = _Path(__file__).resolve().parent.parent / "dbt_project.yml"
_PROJECT_VARS: dict[str, _Any] = _yaml.safe_load(_PROJECT_FILE.read_text())["vars"]
_UNSET = object()


def var(name: str, default: _Any = _UNSET) -> _Any:
    """dbt's `var()`: project defaults from dbt_project.yml, no CLI overrides."""
    if name in _PROJECT_VARS:
        return _PROJECT_VARS[name]
    if default is _UNSET:
        raise KeyError(f"var '{name}' is not defined in dbt_project.yml")
    return default


def _raise_compiler_error(message: str) -> _NoReturn:
    raise RuntimeError(message)


modules = _SimpleNamespace(datetime=_datetime, pytz=_pytz, re=_re)
flags = _SimpleNamespace(FULL_REFRESH=False)
exceptions = _SimpleNamespace(raise_compiler_error=_raise_compiler_error)
