import json
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class CatalogWorkflowTests(unittest.TestCase):
    def test_shopper_searches_real_catalog_and_sees_distinct_fragrance_editions(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            database = Path(temporary_directory) / "catalog.sqlite3"
            self.create_browse_fixture(database)

            searched = self.run_catalog(
                "browse",
                "--database",
                str(database),
                "--query",
                "sample rose",
            )

            self.assertEqual(searched.returncode, 0, searched.stderr)
            self.assertEqual(
                json.loads(searched.stdout),
                {
                    "status": "ok",
                    "query": "sample rose",
                    "results": [
                        {
                            "fragrance_edition_id": 1,
                            "fragrance": "Sample Rose",
                            "edition": "Sample Rose EDT",
                            "concentration": "EDT",
                        },
                        {
                            "fragrance_edition_id": 2,
                            "fragrance": "Sample Rose",
                            "edition": "Sample Rose EDP",
                            "concentration": "EDP",
                        },
                    ],
                },
            )

    def test_shopper_selects_fragrance_edition_and_inspects_scent_profile(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            database = Path(temporary_directory) / "catalog.sqlite3"
            self.create_browse_fixture(database)

            inspected = self.run_catalog(
                "scent-profile",
                "--database",
                str(database),
                "--edition-id",
                "2",
            )

            self.assertEqual(inspected.returncode, 0, inspected.stderr)
            profile = json.loads(inspected.stdout)
            self.assertEqual(
                profile,
                {
                    "status": "ok",
                    "fragrance_edition_id": 2,
                    "fragrance": "Sample Rose",
                    "edition": "Sample Rose EDP",
                    "concentration": "EDP",
                    "scent_profile": {
                        "main_accords": ["floral", "woody"],
                        "note_pyramid": {
                            "top": ["pink pepper"],
                            "middle": ["rose"],
                            "base": ["oud"],
                        },
                        "scent_family": "floral",
                    },
                },
            )
            self.assertNotIn("brand", profile["scent_profile"])
            self.assertNotIn("description", json.dumps(profile).casefold())
            self.assertNotIn("price", json.dumps(profile).casefold())
            self.assertNotIn("bottle", json.dumps(profile).casefold())

    def test_selected_fragrance_edition_returns_ranked_exact_scent_matches(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            database = Path(temporary_directory) / "catalog.sqlite3"
            self.create_browse_fixture(database)

            matched = self.run_catalog(
                "scent-matches",
                "--database",
                str(database),
                "--edition-id",
                "2",
                "--limit",
                "3",
            )

            self.assertEqual(matched.returncode, 0, matched.stderr)
            response = json.loads(matched.stdout)
            self.assertEqual(response["status"], "ok")
            self.assertEqual(response["reference"]["fragrance_edition_id"], 2)
            self.assertEqual(
                response["embedding"],
                {
                    "model": "noseprint-hash-embedding-384",
                    "model_version": "1",
                    "pipeline_version": "scent-profile-serialization-v1",
                    "dimensions": 384,
                    "runtime_device": "cpu",
                },
            )
            self.assertEqual(
                response["results"],
                [
                    {
                        "fragrance_edition_id": 1,
                        "fragrance": "Sample Rose",
                        "edition": "Sample Rose EDT",
                        "concentration": "EDT",
                        "catalog_kind": "real",
                        "scent_match": {
                            "method": "exact_cosine",
                            "model_specific_score": 0.5,
                            "score_basis": "Exact cosine over NosePrint Scent Profile embeddings; not a probability or percent-identical claim.",
                            "strength_label": "weak",
                        },
                        "profile_comparison": {
                            "main_accords": {
                                "shared": ["floral"],
                                "reference_only": ["woody"],
                                "candidate_only": ["fresh"],
                            },
                            "note_pyramid": {
                                "top": {
                                    "shared": [],
                                    "reference_only": ["pink pepper"],
                                    "candidate_only": ["bergamot"],
                                },
                                "middle": {
                                    "shared": ["rose"],
                                    "reference_only": [],
                                    "candidate_only": [],
                                },
                                "base": {
                                    "shared": [],
                                    "reference_only": ["oud"],
                                    "candidate_only": ["musk"],
                                },
                            },
                            "scent_family": {
                                "shared": "floral",
                                "reference_only": "unknown",
                                "candidate_only": "unknown",
                            },
                        },
                    },
                    {
                        "fragrance_edition_id": 4,
                        "fragrance": "Bare Iris",
                        "edition": "Bare Iris EDP",
                        "concentration": "EDP",
                        "catalog_kind": "real",
                        "scent_match": {
                            "method": "exact_cosine",
                            "model_specific_score": 0.0,
                            "score_basis": "Exact cosine over NosePrint Scent Profile embeddings; not a probability or percent-identical claim.",
                            "strength_label": "incomplete",
                        },
                        "profile_comparison": {
                            "main_accords": {
                                "shared": "unknown",
                                "reference_only": "unknown",
                                "candidate_only": "unknown",
                            },
                            "note_pyramid": {
                                "top": {
                                    "shared": "unknown",
                                    "reference_only": "unknown",
                                    "candidate_only": "unknown",
                                },
                                "middle": {
                                    "shared": "unknown",
                                    "reference_only": "unknown",
                                    "candidate_only": "unknown",
                                },
                                "base": {
                                    "shared": "unknown",
                                    "reference_only": "unknown",
                                    "candidate_only": "unknown",
                                },
                            },
                            "scent_family": {
                                "shared": "unknown",
                                "reference_only": "unknown",
                                "candidate_only": "unknown",
                            },
                        },
                    },
                ],
            )
            self.assertNotIn("Sample Rose Load Test", matched.stdout)

    def test_exact_scent_match_records_versioned_384_number_embeddings(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            database = Path(temporary_directory) / "catalog.sqlite3"
            self.create_browse_fixture(database)

            matched = self.run_catalog(
                "scent-matches",
                "--database",
                str(database),
                "--edition-id",
                "2",
            )

            self.assertEqual(matched.returncode, 0, matched.stderr)
            connection = sqlite3.connect(database)
            connection.row_factory = sqlite3.Row
            try:
                rows = connection.execute(
                    """
                    SELECT fragrance_edition_id, model, model_version,
                           pipeline_version, dimensions, vector_json
                    FROM scent_profile_embeddings
                    ORDER BY fragrance_edition_id
                    """
                ).fetchall()
            finally:
                connection.close()
            self.assertEqual([row["fragrance_edition_id"] for row in rows], [1, 2, 4])
            for row in rows:
                self.assertEqual(row["model"], "noseprint-hash-embedding-384")
                self.assertEqual(row["model_version"], "1")
                self.assertEqual(
                    row["pipeline_version"], "scent-profile-serialization-v1"
                )
                self.assertEqual(row["dimensions"], 384)
                self.assertEqual(len(json.loads(row["vector_json"])), 384)

    def test_scent_match_labels_strong_and_surprising_profile_comparisons(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            database = Path(temporary_directory) / "catalog.sqlite3"
            self.create_browse_fixture(database)
            connection = sqlite3.connect(database)
            try:
                connection.executescript(
                    """
                    INSERT INTO fragrances (id, name, brand)
                    VALUES
                        (3, 'Twin Rose', 'Fixture House'),
                        (4, 'Shadow Rose', 'Fixture House');
                    INSERT INTO fragrance_editions
                        (id, fragrance_id, name, concentration, catalog_kind)
                    VALUES
                        (5, 3, 'Twin Rose EDP', 'EDP', 'real'),
                        (6, 4, 'Shadow Rose EDP', 'EDP', 'real');
                    INSERT INTO scent_profiles
                        (fragrance_edition_id, notes_json, main_accords_json,
                         top_notes_json, middle_notes_json, base_notes_json, scent_family)
                    VALUES
                        (5, '["rose", "oud"]', '["floral", "woody"]',
                         '["pink pepper"]', '["rose"]', '["oud"]', 'floral'),
                        (6, '["rose", "oud"]', '["floral", "woody"]',
                         '["pink pepper"]', '["rose"]', '["oud"]', 'amber');
                    INSERT INTO source_records
                        (dataset_id, source_row, fragrance_edition_id, original_values_json)
                    VALUES
                        ('fixture-real-v1', 5, 5, '{"Brand": "Fixture House"}'),
                        ('fixture-real-v1', 6, 6, '{"Brand": "Fixture House"}');
                    """
                )
                connection.commit()
            finally:
                connection.close()

            matched = self.run_catalog(
                "scent-matches",
                "--database",
                str(database),
                "--edition-id",
                "2",
                "--limit",
                "2",
            )

            self.assertEqual(matched.returncode, 0, matched.stderr)
            results = json.loads(matched.stdout)["results"]
            self.assertEqual(
                [
                    (
                        result["fragrance"],
                        result["scent_match"]["strength_label"],
                        result["profile_comparison"]["scent_family"],
                    )
                    for result in results
                ],
                [
                    (
                        "Twin Rose",
                        "strong",
                        {
                            "shared": "floral",
                            "reference_only": "unknown",
                            "candidate_only": "unknown",
                        },
                    ),
                    (
                        "Shadow Rose",
                        "surprising",
                        {
                            "shared": "unknown",
                            "reference_only": "floral",
                            "candidate_only": "amber",
                        },
                    ),
                ],
            )

    def test_non_scent_catalog_facts_do_not_change_exact_scent_match_order(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            database = Path(temporary_directory) / "catalog.sqlite3"
            self.create_browse_fixture(database)

            before = self.run_catalog(
                "scent-matches",
                "--database",
                str(database),
                "--edition-id",
                "2",
            )
            connection = sqlite3.connect(database)
            try:
                connection.execute(
                    """
                    UPDATE source_records
                    SET original_values_json = ?
                    WHERE fragrance_edition_id = 1
                    """,
                    (
                        json.dumps(
                            {
                                "Brand": "Fixture House",
                                "Description": "New expensive marketing copy.",
                                "Price": "$999",
                                "Bottle Size": "10 ml",
                            }
                        ),
                    ),
                )
                connection.commit()
            finally:
                connection.close()
            after = self.run_catalog(
                "scent-matches",
                "--database",
                str(database),
                "--edition-id",
                "2",
            )

            self.assertEqual(before.returncode, 0, before.stderr)
            self.assertEqual(after.returncode, 0, after.stderr)
            self.assertEqual(
                json.loads(before.stdout)["results"],
                json.loads(after.stdout)["results"],
            )

    def test_exact_scent_match_has_clear_empty_state_without_alternatives(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            database = Path(temporary_directory) / "catalog.sqlite3"
            self.create_browse_fixture(database)
            connection = sqlite3.connect(database)
            try:
                connection.execute(
                    "DELETE FROM scent_profiles WHERE fragrance_edition_id <> 2"
                )
                connection.execute(
                    "DELETE FROM source_records WHERE fragrance_edition_id <> 2"
                )
                connection.execute("DELETE FROM fragrance_editions WHERE id <> 2")
                connection.commit()
            finally:
                connection.close()

            matched = self.run_catalog(
                "scent-matches",
                "--database",
                str(database),
                "--edition-id",
                "2",
            )

            self.assertEqual(matched.returncode, 0, matched.stderr)
            self.assertEqual(
                json.loads(matched.stdout),
                {
                    "status": "no_matches",
                    "reference": {
                        "fragrance_edition_id": 2,
                        "fragrance": "Sample Rose",
                        "edition": "Sample Rose EDP",
                        "concentration": "EDP",
                    },
                    "embedding": {
                        "model": "noseprint-hash-embedding-384",
                        "model_version": "1",
                        "pipeline_version": "scent-profile-serialization-v1",
                        "dimensions": 384,
                        "runtime_device": "cpu",
                    },
                    "results": [],
                    "message": "No other Real Catalog Fragrance Editions are available for exact cosine Scent Matches.",
                },
            )

    def test_shopper_search_gets_clear_empty_state_when_no_fragrance_matches(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            database = Path(temporary_directory) / "catalog.sqlite3"
            self.create_browse_fixture(database)

            searched = self.run_catalog(
                "browse",
                "--database",
                str(database),
                "--query",
                "missing amber",
            )

            self.assertEqual(searched.returncode, 0, searched.stderr)
            self.assertEqual(
                json.loads(searched.stdout),
                {
                    "status": "no_matches",
                    "query": "missing amber",
                    "message": "No Real Catalog Fragrance Editions matched that Fragrance name.",
                    "results": [],
                },
            )

    def test_shopper_sees_unknown_for_missing_scent_profile_facts(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            database = Path(temporary_directory) / "catalog.sqlite3"
            self.create_browse_fixture(database)

            inspected = self.run_catalog(
                "scent-profile",
                "--database",
                str(database),
                "--edition-id",
                "4",
            )

            self.assertEqual(inspected.returncode, 0, inspected.stderr)
            self.assertEqual(
                json.loads(inspected.stdout)["scent_profile"],
                {
                    "main_accords": "unknown",
                    "note_pyramid": {
                        "top": "unknown",
                        "middle": "unknown",
                        "base": "unknown",
                    },
                    "scent_family": "unknown",
                },
            )

    def test_shopper_gets_clear_unavailable_state_before_catalog_is_imported(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            database = Path(temporary_directory) / "missing.sqlite3"

            searched = self.run_catalog(
                "browse",
                "--database",
                str(database),
                "--query",
                "rose",
            )

            self.assertEqual(searched.returncode, 2)
            self.assertIn("Catalog unavailable", searched.stderr)
            self.assertIn("import", searched.stderr)
            self.assertNotIn("no such table", searched.stderr)
            self.assertNotIn("Traceback", searched.stderr)

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

    def test_owner_can_explicitly_accept_inconclusive_risk_without_tampering_audit(
        self,
    ) -> None:
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
                            "id": "owner-accepted-candidate-v1",
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
                            "quality_status": "inconclusive",
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
                "--accept-owner-risk",
                "--risk-note",
                "Personal side project: accept inconclusive CC0/provenance risk.",
            )
            inspected = self.run_catalog("inspect", "--database", str(database))

            self.assertEqual(audit.returncode, 2, audit.stderr)
            self.assertEqual(json.loads(report.read_text())["verdict"], "inconclusive")
            self.assertEqual(imported.returncode, 0, imported.stderr)
            self.assertEqual(json.loads(imported.stdout)["accepted"], 1)
            self.assertEqual(inspected.returncode, 0, inspected.stderr)
            self.assertEqual(
                json.loads(inspected.stdout)[0]["source_dataset"],
                "owner-accepted-candidate-v1",
            )

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

    def create_browse_fixture(self, database: Path) -> None:
        connection = sqlite3.connect(database)
        try:
            connection.executescript(
                """
                PRAGMA foreign_keys = ON;
                CREATE TABLE catalog_sources (
                    dataset_id TEXT PRIMARY KEY,
                    download_url TEXT NOT NULL,
                    publisher TEXT NOT NULL,
                    claimed_license TEXT NOT NULL,
                    audit_report_json TEXT NOT NULL,
                    owner_accepted_risk INTEGER NOT NULL DEFAULT 0,
                    risk_acceptance_note TEXT
                );
                CREATE TABLE fragrances (
                    id INTEGER PRIMARY KEY,
                    name TEXT NOT NULL,
                    brand TEXT NOT NULL,
                    UNIQUE (name, brand)
                );
                CREATE TABLE fragrance_editions (
                    id INTEGER PRIMARY KEY,
                    fragrance_id INTEGER NOT NULL REFERENCES fragrances(id),
                    name TEXT NOT NULL,
                    concentration TEXT,
                    catalog_kind TEXT NOT NULL CHECK (catalog_kind IN ('real', 'scale-test')),
                    UNIQUE (fragrance_id, name)
                );
                CREATE TABLE scent_profiles (
                    fragrance_edition_id INTEGER PRIMARY KEY REFERENCES fragrance_editions(id),
                    notes_json TEXT NOT NULL,
                    main_accords_json TEXT,
                    top_notes_json TEXT,
                    middle_notes_json TEXT,
                    base_notes_json TEXT,
                    scent_family TEXT
                );
                CREATE TABLE source_records (
                    dataset_id TEXT NOT NULL REFERENCES catalog_sources(dataset_id),
                    source_row INTEGER NOT NULL,
                    fragrance_edition_id INTEGER NOT NULL REFERENCES fragrance_editions(id),
                    original_values_json TEXT NOT NULL,
                    PRIMARY KEY (dataset_id, source_row)
                );
                INSERT INTO catalog_sources
                    (dataset_id, download_url, publisher, claimed_license, audit_report_json)
                VALUES
                    ('fixture-real-v1', 'https://example.test/real.csv',
                     'Fixture Publisher', 'CC0-1.0', '{}');
                INSERT INTO fragrances (id, name, brand)
                VALUES
                    (1, 'Sample Rose', 'Fixture House'),
                    (2, 'Bare Iris', 'Fixture House');
                INSERT INTO fragrance_editions
                    (id, fragrance_id, name, concentration, catalog_kind)
                VALUES
                    (1, 1, 'Sample Rose EDT', 'EDT', 'real'),
                    (2, 1, 'Sample Rose EDP', 'EDP', 'real'),
                    (3, 1, 'Sample Rose Load Test', 'EDP', 'scale-test'),
                    (4, 2, 'Bare Iris EDP', 'EDP', 'real');
                INSERT INTO scent_profiles
                    (fragrance_edition_id, notes_json, main_accords_json,
                     top_notes_json, middle_notes_json, base_notes_json, scent_family)
                VALUES
                    (1, '["rose", "bergamot"]', '["floral", "fresh"]',
                     '["bergamot"]', '["rose"]', '["musk"]', 'floral'),
                    (2, '["rose", "oud"]', '["floral", "woody"]',
                     '["pink pepper"]', '["rose"]', '["oud"]', 'floral'),
                    (3, '["metal"]', '["synthetic"]',
                     '["metal"]', '["metal"]', '["metal"]', 'synthetic'),
                    (4, '["iris"]', NULL, NULL, NULL, NULL, NULL);
                INSERT INTO source_records
                    (dataset_id, source_row, fragrance_edition_id, original_values_json)
                VALUES
                    ('fixture-real-v1', 2, 1,
                     '{"Brand": "Fixture House", "Description": "A bright rose story.", "Price": "$80", "Bottle Size": "50 ml"}'),
                    ('fixture-real-v1', 3, 2,
                     '{"Brand": "Fixture House", "Description": "A deeper rose story.", "Price": "$110", "Bottle Size": "100 ml"}'),
                    ('fixture-real-v1', 4, 4,
                     '{"Brand": "Fixture House", "Description": "Sparse iris copy.", "Price": "", "Bottle Size": ""}');
                """
            )
            connection.commit()
        finally:
            connection.close()


if __name__ == "__main__":
    unittest.main()
