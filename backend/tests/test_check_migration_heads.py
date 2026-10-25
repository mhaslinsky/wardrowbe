import importlib.util
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
from unittest.mock import patch

SCRIPT = Path(__file__).parents[1] / "scripts" / "check_migration_heads.py"


class MigrationHeadCheckerTests(unittest.TestCase):
    def test_migration_graphs(self):
        cases = [
            ([("root", None, True), ("tip", "root", False)], 0, "Single migration head: tip"),
            ([("root", None, True), ("tip", None, False)], 1, "Multiple migration heads"),
            ([("root", None, True), ("root", None, False)], 1, "Duplicate revision ID"),
            ([("root", None, True), (None, None, False)], 1, "No revision"),
            (
                [("left", None, True), ("right", None, False), ("tip", ("left", "right"), False)],
                0,
                "Single migration head: tip",
            ),
            ([], 1, "No migration heads"),
            ([("", None, False)], 1, "Invalid revision"),
            ([("root", 12, False)], 1, "Invalid down_revision"),
        ]
        for migrations, expected_code, expected_message in cases:
            with self.subTest(migrations=migrations), tempfile.TemporaryDirectory() as directory:
                for index, (revision, parent, annotated) in enumerate(migrations):
                    annotation = ": str" if annotated else ""
                    content = (
                        ""
                        if revision is None
                        else f"revision{annotation} = {json.dumps(revision)}\n"
                    )
                    parent_annotation = ": str | None" if annotated else ""
                    content += f"down_revision{parent_annotation} = {parent!r}\n"
                    Path(directory, f"{index}.py").write_text(content)
                spec = importlib.util.spec_from_file_location("migration_checker", SCRIPT)
                checker = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(checker)
                output = StringIO()
                with (
                    patch.object(checker, "VERSIONS_DIR", Path(directory)),
                    redirect_stdout(output),
                ):
                    code = checker.main()
                self.assertEqual(code, expected_code, output.getvalue())
                self.assertIn(expected_message, output.getvalue())
