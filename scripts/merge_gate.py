"""Evaluate the merge gate for one pull request, and merge it on a pass with --merge.

    make merge-check PR=19   # dry run
    make merge PR=19         # squash-merge if the gate allows it

Run it through make, which exports this file, the gate module and the policy
from origin/main first (ADR-011). The module is loaded by file path rather
than imported, because an import would resolve to the installed working-tree
package and a branch could then loosen its own gate.
"""

import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
MODULE = ROOT / "src" / "mind_the_data" / "ops" / "merge_gate.py"
POLICY = ROOT / "config" / "merge_policy.yaml"


def main() -> int:
    spec = importlib.util.spec_from_file_location("merge_gate_pinned", MODULE)
    if spec is None or spec.loader is None:
        print(f"cannot load {MODULE}", file=sys.stderr)
        return 2
    module = importlib.util.module_from_spec(spec)
    # Dataclasses look their module up in sys.modules while being created.
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    code: int = module.main(sys.argv[1:], policy_path=POLICY)
    return code


if __name__ == "__main__":
    sys.exit(main())
