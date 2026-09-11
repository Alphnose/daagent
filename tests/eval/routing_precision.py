"""File-based entry point for the `routing_precision` judge.

`eval_config.yaml` declares routing precision twice on purpose:

* `routing_precision` is a **declarative** metric - the rubric lives in the YAML
  as a `prompt_template` and the Vertex eval service runs the judge. That is the
  cheapest way to author a judge, but the service renders the template
  server-side and hard-requires every placeholder, so `{prompt}` makes it usable
  only on single-turn cases. Multi-turn cases carry their final user turn inside
  `agent_data.turns` and have no top-level `prompt`, and the run fails with
  "Variable prompt is required but not provided".

* `routing_precision_multiturn` points at this file instead. A `custom_function`
  receives the whole instance dict and can decide for itself what to do with a
  missing field, so the same rubric survives both dataset shapes.

Both delegate to the single implementation in ``metrics.py`` so the rubric text
cannot drift between the two.
"""

from __future__ import annotations

import importlib.util
import pathlib
from typing import Any

_MODULE = None


def _metrics():
    """Loads the sibling ``metrics.py``.

    ``custom_function_file`` contents are inlined and compiled with ``exec``, so
    ``__file__`` is not available here and a normal relative import would fail.
    """
    global _MODULE
    if _MODULE is None:
        for base in [pathlib.Path.cwd(), *pathlib.Path.cwd().parents]:
            candidate = base / "tests" / "eval" / "metrics.py"
            if candidate.exists():
                spec = importlib.util.spec_from_file_location(
                    "cymbal_eval_metrics", candidate
                )
                module = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(module)
                _MODULE = module
                break
        else:
            raise RuntimeError(
                "tests/eval/metrics.py not found from the working directory"
            )
    return _MODULE


def evaluate(instance: dict[str, Any]) -> dict[str, Any]:
    return _metrics().routing_precision(instance)
