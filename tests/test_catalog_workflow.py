import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class CatalogWorkflowTests(unittest.TestCase):
    def test_invalid_manifest_fails_without_exposing_a_traceback(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            workspace = Path(temporary_directory)
            source = workspace / "candidate.csv"
            source.write_text("Name\nExample\n", encoding="utf-8")
            manifest = workspace / "invalid.json"
            manifest.write_text("{", encoding="utf-8")

            audit = self.run_catalog(
                "audit",
                "--manifest",
                str(manifest),
                "--source",
                str(source),
                "--report",
                str(workspace / "report.json"),
            )

            self.assertEqual(audit.returncode, 1)
            self.assertIn("Catalog workflow failed", audit.stderr)
            self.assertNotIn("Traceback", audit.stderr)

    def test_audit_cannot_pass_on_status_labels_without_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            workspace = Path(temporary_directory)
            source = workspace / "candidate.csv"
            source.write_text("Name\nExample\n", encoding="utf-8")
            manifest = workspace / "audit.json"
            manifest.write_text(
                json.dumps(
                    {
                        "dataset": {
                            "id": "unsupported-claim-v1",
                            "download_url": "https://example.test/candidate.csv",
                            "publisher": "Example Publisher",
                            "claimed_license": "CC0-1.0",
                            "license_evidence": [],
                            "license_chain_status": "passed",
                            "provenance_evidence": [],
                            "provenance_status": "passed",
                            "expected_schema": ["Name"],
                            "expected_row_count": 1,
                            "quality_status": "passed",
                            "quality_risks": [],
                        }
                    }
                ),
                encoding="utf-8",
            )
            report = workspace / "audit-report.json"

            audit = self.run_catalog(
                "audit",
                "--manifest",
                str(manifest),
                "--source",
                str(source),
                "--report",
                str(report),
            )

            self.assertEqual(audit.returncode, 2, audit.stderr)
            self.assertEqual(json.loads(report.read_text())["verdict"], "inconclusive")

    def test_inconclusive_audit_blocks_real_catalog_import(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            workspace = Path(temporary_directory)
            source = workspace / "candidate.csv"
            source.write_text(
                "Name,Brand,Description,Notes,Image URL\n"
                "Example,Example Brand,A description,Cedar,https://example.test/image.jpg\n",
                encoding="utf-8",
            )
            manifest = workspace / "audit.json"
            manifest.write_text(
                json.dumps(
                    {
                        "dataset": {
                            "id": "candidate-perfume-recommendation-v1",
                            "download_url": "https://example.test/candidate.csv",
                            "publisher": "Example Publisher",
                            "claimed_license": "CC0: Public Domain",
                            "license_evidence": ["https://example.test/license"],
                            "license_chain_status": "inconclusive",
                            "provenance_evidence": [],
                            "provenance_status": "inconclusive",
                            "expected_schema": [
                                "Name",
                                "Brand",
                                "Description",
                                "Notes",
                                "Image URL",
                            ],
                            "expected_row_count": 1,
                            "quality_status": "passed",
                            "quality_risks": ["Original source is not identified."],
                        }
                    }
                ),
                encoding="utf-8",
            )
            report = workspace / "audit-report.json"
            database = workspace / "catalog.sqlite3"

            audit = self.run_catalog(
                "audit",
                "--manifest",
                str(manifest),
                "--source",
                str(source),
                "--report",
                str(report),
            )
            imported = self.run_catalog(
                "import",
                "--audit-report",
                str(report),
                "--source",
                str(source),
                "--database",
                str(database),
            )
            tampered_report = json.loads(report.read_text(encoding="utf-8"))
            tampered_report["verdict"] = "passed"
            report.write_text(json.dumps(tampered_report), encoding="utf-8")
            bypass_attempt = self.run_catalog(
                "import",
                "--audit-report",
                str(report),
                "--source",
                str(source),
                "--database",
                str(database),
            )

            self.assertEqual(audit.returncode, 2, audit.stderr)
            self.assertIn("INCONCLUSIVE", audit.stdout)
            self.assertEqual(imported.returncode, 2, imported.stderr)
            self.assertIn("Import blocked", imported.stderr)
            self.assertEqual(bypass_attempt.returncode, 2, bypass_attempt.stderr)
            self.assertFalse(database.exists())

    def test_passing_audit_imports_traceable_real_catalog_record_idempotently(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            workspace = Path(temporary_directory)
            source = workspace / "approved.csv"
            source.write_text(
                "Name,Brand,Description,Notes,Image URL\n"
                '  Example Scent  ,Example Brand,A description,"Cedar, Bergamot",\n',
                encoding="utf-8",
            )
            manifest = workspace / "audit.json"
            manifest.write_text(
                json.dumps(
                    {
                        "dataset": {
                            "id": "approved-fixture-v1",
                            "download_url": "https://example.test/approved.csv",
                            "publisher": "Fixture Publisher",
                            "claimed_license": "CC0-1.0",
                            "license_evidence": ["https://creativecommons.org/publicdomain/zero/1.0/"],
                            "license_chain_status": "passed",
                            "provenance_evidence": ["https://example.test/provenance"],
                            "provenance_status": "passed",
                            "expected_schema": [
                                "Name",
                                "Brand",
                                "Description",
                                "Notes",
                                "Image URL",
                            ],
                            "expected_row_count": 1,
                            "quality_status": "passed",
                            "quality_risks": [],
                        }
                    }
                ),
                encoding="utf-8",
            )
            report = workspace / "audit-report.json"
            database = workspace / "catalog.sqlite3"

            audit = self.run_catalog(
                "audit",
                "--manifest",
                str(manifest),
                "--source",
                str(source),
                "--report",
                str(report),
            )
            imported = self.run_catalog(
                "import",
                "--audit-report",
                str(report),
                "--source",
                str(source),
                "--database",
                str(database),
            )
            imported_again = self.run_catalog(
                "import",
                "--audit-report",
                str(report),
                "--source",
                str(source),
                "--database",
                str(database),
            )
            inspected = self.run_catalog("inspect", "--database", str(database))

            self.assertEqual(audit.returncode, 0, audit.stderr)
            self.assertEqual(imported.returncode, 0, imported.stderr)
            self.assertEqual(
                json.loads(imported.stdout),
                {
                    "accepted": 1,
                    "rejected": 0,
                    "transformed": 1,
                    "duplicates": 0,
                    "quarantined": 0,
                },
            )
            self.assertEqual(
                json.loads(imported_again.stdout),
                {
                    "accepted": 0,
                    "rejected": 0,
                    "transformed": 0,
                    "duplicates": 1,
                    "quarantined": 0,
                },
            )
            self.assertEqual(inspected.returncode, 0, inspected.stderr)
            self.assertEqual(
                json.loads(inspected.stdout),
                [
                    {
                        "brand": "Example Brand",
                        "fragrance": "Example Scent",
                        "edition": "Example Scent",
                        "concentration": None,
                        "notes": ["bergamot", "cedar"],
                        "source_dataset": "approved-fixture-v1",
                        "source_row": 2,
                        "original_name": "  Example Scent  ",
                    }
                ],
            )

    def test_import_reports_and_quarantines_questionable_rows_deterministically(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            workspace = Path(temporary_directory)
            source = workspace / "mixed.csv"
            source.write_text(
                "Name,Brand,Description,Notes,Image URL\n"
                'Example Scent,Example Brand,Valid,"Cedar, Bergamot",\n'
                'Example Scent,Example Brand,Duplicate,"Cedar, Bergamot",\n'
                ',Example Brand,Missing name,Cedar,\n'
                'Unprofiled,Example Brand,Missing notes,,\n'
                'Malformed,Example Brand,Too many values,Cedar,,unexpected\n',
                encoding="utf-8",
            )
            manifest = workspace / "audit.json"
            manifest.write_text(
                json.dumps(
                    {
                        "dataset": {
                            "id": "mixed-fixture-v1",
                            "download_url": "https://example.test/mixed.csv",
                            "publisher": "Fixture Publisher",
                            "claimed_license": "CC0-1.0",
                            "license_evidence": ["https://creativecommons.org/publicdomain/zero/1.0/"],
                            "license_chain_status": "passed",
                            "provenance_evidence": ["https://example.test/provenance"],
                            "provenance_status": "passed",
                            "expected_schema": [
                                "Name",
                                "Brand",
                                "Description",
                                "Notes",
                                "Image URL",
                            ],
                            "expected_row_count": 5,
                            "quality_status": "passed",
                            "quality_risks": ["Fixture contains known invalid rows."],
                        }
                    }
                ),
                encoding="utf-8",
            )
            report = workspace / "audit-report.json"
            database = workspace / "catalog.sqlite3"
            self.run_catalog(
                "audit",
                "--manifest",
                str(manifest),
                "--source",
                str(source),
                "--report",
                str(report),
            )

            imported = self.run_catalog(
                "import",
                "--audit-report",
                str(report),
                "--source",
                str(source),
                "--database",
                str(database),
            )
            quarantined = self.run_catalog(
                "inspect-quarantine", "--database", str(database)
            )

            self.assertEqual(imported.returncode, 0, imported.stderr)
            self.assertEqual(
                json.loads(imported.stdout),
                {
                    "accepted": 1,
                    "rejected": 2,
                    "transformed": 1,
                    "duplicates": 1,
                    "quarantined": 1,
                },
            )
            self.assertEqual(
                json.loads(quarantined.stdout),
                [
                    {"source_row": 4, "disposition": "rejected", "reason": "missing Name"},
                    {
                        "source_row": 5,
                        "disposition": "quarantined",
                        "reason": "missing Notes",
                    },
                    {
                        "source_row": 6,
                        "disposition": "rejected",
                        "reason": "malformed columns",
                    },
                ],
            )

    def run_catalog(self, *arguments: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, "-m", "noseprint.catalog", *arguments],
            cwd=ROOT,
            text=True,
            capture_output=True,
            check=False,
        )


if __name__ == "__main__":
    unittest.main()
