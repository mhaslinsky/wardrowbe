"""Check that Alembic migrations have exactly one head and no duplicate revision IDs."""

import ast
import sys
from pathlib import Path

VERSIONS_DIR = Path(__file__).resolve().parent.parent / "migrations" / "versions"


def main() -> int:
    revisions: dict[str, str] = {}
    down_refs: set[str] = set()
    errors = 0

    for path in sorted(VERSIONS_DIR.glob("*.py")):
        declarations = {}
        try:
            for node in ast.parse(path.read_text(), filename=str(path)).body:
                if isinstance(node, (ast.Assign, ast.AnnAssign)):
                    targets = node.targets if isinstance(node, ast.Assign) else [node.target]
                    for target in targets:
                        if isinstance(target, ast.Name) and target.id in (
                            "revision",
                            "down_revision",
                        ):
                            declarations[target.id] = ast.literal_eval(node.value)
            if "revision" not in declarations:
                raise ValueError("No revision defined")
            rev_id = declarations["revision"]
            if not isinstance(rev_id, str) or not rev_id:
                raise ValueError("Invalid revision ID")
            parents = declarations.get("down_revision")
            if parents is None:
                parents = ()
            elif isinstance(parents, str):
                parents = (parents,)
            if not isinstance(parents, (tuple, list)) or any(
                not isinstance(parent, str) or not parent for parent in parents
            ):
                raise ValueError("Invalid down_revision")
        except (SyntaxError, ValueError, TypeError) as error:
            print(f"FAIL: {path.name}: {error}")
            errors += 1
            continue

        if rev_id in revisions:
            print(
                f"FAIL: Duplicate revision ID '{rev_id}' "
                f"in {path.name} and {Path(revisions[rev_id]).name}"
            )
            errors += 1

        revisions[rev_id] = str(path)

        down_refs.update(parents)

    heads = set(revisions.keys()) - down_refs

    if len(heads) == 0:
        print("FAIL: No migration heads found (circular dependency?)")
        errors += 1
    elif len(heads) > 1:
        print(f"FAIL: Multiple migration heads found: {heads}")
        for head in sorted(heads):
            print(f"  {head} -> {Path(revisions[head]).name}")
        errors += 1
    elif not errors:
        print(f"OK: Single migration head: {heads.pop()}")

    if errors:
        print(f"\n{errors} error(s) found. Merge the migration heads before deploying.")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
