import csv
import json
import os
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CURATED_REAL_CATALOG_SCHEMA = [
    "fragrance_name",
    "fragrance_edition_name",
    "brand",
    "concentration",
    "main_accords",
    "top_notes",
    "middle_notes",
    "base_notes",
    "scent_family",
    "identity_source_urls",
    "scent_profile_source_urls",
    "curator_review_status",
    "curator_reviewed_on",
    "curation_notes",
]


class CatalogWorkflowTests(unittest.TestCase):
    def test_docs_explain_curated_batch_review_workflow(self) -> None:
        guide = (ROOT / "docs" / "catalog-import.md").read_text(encoding="utf-8")

        expected_guidance = [
            "## Review a Curated Batch before Real Catalog import",
            "Pass 1: collect facts",
            "Pass 2: verify facts",
            "Draft Curated Batch CSV files stay outside the repository",
            "python3 -m noseprint.catalog curated-template",
            "python3 -m noseprint.catalog curated-preview --source",
            "Read the preview from the top down",
            "Batch 1 data is added in a later issue",
        ]
        for guidance in expected_guidance:
            with self.subTest(guidance=guidance):
                self.assertIn(guidance, guide)

        for term in (
            "Fragrance",
            "Fragrance Edition",
            "Scent Profile",
            "Scent Match",
            "Real Catalog",
            "Curated Batch",
        ):
            with self.subTest(term=term):
                self.assertIn(term, guide)

    def test_curator_generates_curated_batch_csv_template(self) -> None:
        generated = self.run_catalog("curated-template")

        self.assertEqual(generated.returncode, 0, generated.stderr)
        rows = list(csv.reader(generated.stdout.splitlines()))
        self.assertEqual(rows, [CURATED_REAL_CATALOG_SCHEMA])
        self.assertIn("drafts until curator_review_status is reviewed", generated.stderr)

    def test_curator_previews_ready_curated_batch(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            source = Path(temporary_directory) / "curated.csv"
            source.write_text(
                "fragrance_name,fragrance_edition_name,brand,concentration,"
                "main_accords,top_notes,middle_notes,base_notes,scent_family,"
                "identity_source_urls,scent_profile_source_urls,"
                "curator_review_status,curator_reviewed_on,curation_notes\n"
                "Sample Rose,Sample Rose EDP,Fixture House,EDP,"
                "floral,bergamot,rose,musk,floral,"
                "https://example.test/identity,https://example.test/profile,"
                "reviewed,2026-06-25,Ready row\n"
                "Quiet Cedar,Quiet Cedar EDT,Fixture House,EDT,"
                "woody,lemon,jasmine,cedar,woody,"
                "https://example.test/cedar-id,https://example.test/cedar-profile,"
                "reviewed,2026-06-25,Ready row\n",
                encoding="utf-8",
            )

            previewed = self.run_catalog("curated-preview", "--source", str(source))

            self.assertEqual(previewed.returncode, 0, previewed.stderr)
            self.assertEqual(
                json.loads(previewed.stdout),
                {
                    "status": "ok",
                    "rows": {
                        "total": 2,
                        "ready": 2,
                        "rejected": 0,
                        "duplicates": 0,
                    },
                    "rejections": [],
                    "duplicates": [],
                    "missing_source_urls": {
                        "identity": [],
                        "scent_profile": [],
                    },
                    "review_status": {
                        "missing": [],
                        "not_reviewed": [],
                    },
                    "batch_1": {
                        "ready": 2,
                        "too_weak": 0,
                        "too_weak_rows": [],
                    },
                    "missing_note_groups": {
                        "top": [],
                        "middle": [],
                        "base": [],
                    },
                    "coverage": {
                        "scent_families": [
                            {"scent_family": "floral", "count": 1, "source_rows": [2]},
                            {"scent_family": "woody", "count": 1, "source_rows": [3]},
                        ],
                        "repeated_brands": [
                            {"brand": "Fixture House", "count": 2, "source_rows": [2, 3]}
                        ],
                        "common_notes": [],
                    },
                },
            )

    def test_curator_previews_curated_batch_note_overlap(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            source = Path(temporary_directory) / "curated-overlap.csv"
            source.write_text(
                "fragrance_name,fragrance_edition_name,brand,concentration,"
                "main_accords,top_notes,middle_notes,base_notes,scent_family,"
                "identity_source_urls,scent_profile_source_urls,"
                "curator_review_status,curator_reviewed_on,curation_notes\n"
                "Rose One,Rose One EDP,Fixture House,EDP,"
                "floral,bergamot,rose,musk,floral,"
                "https://example.test/one-id,https://example.test/one-profile,"
                "reviewed,2026-06-25,Ready row\n"
                "Rose Two,Rose Two EDT,Fixture House,EDT,"
                "floral,bergamot,jasmine,musk,floral,"
                "https://example.test/two-id,https://example.test/two-profile,"
                "reviewed,2026-06-25,Ready row\n"
                "Cedar One,Cedar One EDT,Another House,EDT,"
                "woody,lemon,iris,cedar,woody,"
                "https://example.test/cedar-id,https://example.test/cedar-profile,"
                "reviewed,2026-06-25,Ready row\n",
                encoding="utf-8",
            )

            previewed = self.run_catalog("curated-preview", "--source", str(source))

            self.assertEqual(previewed.returncode, 0, previewed.stderr)
            report = json.loads(previewed.stdout)
            self.assertEqual(
                report["coverage"],
                {
                    "scent_families": [
                        {"scent_family": "floral", "count": 2, "source_rows": [2, 3]},
                        {"scent_family": "woody", "count": 1, "source_rows": [4]},
                    ],
                    "repeated_brands": [
                        {"brand": "Fixture House", "count": 2, "source_rows": [2, 3]}
                    ],
                    "common_notes": [
                        {
                            "note": "bergamot",
                            "count": 2,
                            "source_rows": [2, 3],
                            "note_groups": ["top"],
                        },
                        {
                            "note": "musk",
                            "count": 2,
                            "source_rows": [2, 3],
                            "note_groups": ["base"],
                        },
                    ],
                },
            )

    def test_curator_previews_curated_batch_rejections_and_duplicates(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            source = Path(temporary_directory) / "curated-mixed.csv"
            source.write_text(
                "fragrance_name,fragrance_edition_name,brand,concentration,"
                "main_accords,top_notes,middle_notes,base_notes,scent_family,"
                "identity_source_urls,scent_profile_source_urls,"
                "curator_review_status,curator_reviewed_on,curation_notes\n"
                "Sample Rose,Sample Rose EDP,Fixture House,EDP,"
                "floral,bergamot,rose,musk,floral,"
                "https://example.test/identity,https://example.test/profile,"
                "reviewed,2026-06-25,Ready row\n"
                "Sample Rose,Sample Rose EDP,Fixture House,EDP,"
                "floral,bergamot,rose,musk,floral,"
                "https://example.test/identity,https://example.test/profile,"
                "reviewed,2026-06-25,Duplicate row\n"
                "Nameless,Nameless EDP,,EDP,floral,bergamot,rose,musk,floral,"
                "https://example.test/identity,https://example.test/profile,"
                "reviewed,2026-06-25,Missing brand\n"
                "Unsourced Identity,Unsourced Identity EDP,Fixture House,EDP,"
                "floral,bergamot,rose,musk,floral,"
                ",https://example.test/profile,reviewed,2026-06-25,Missing identity source\n"
                "Unsourced Profile,Unsourced Profile EDP,Fixture House,EDP,"
                "floral,bergamot,rose,musk,floral,"
                "https://example.test/identity,,reviewed,2026-06-25,Missing profile source\n"
                "No Status,No Status EDP,Fixture House,EDP,"
                "floral,bergamot,rose,musk,floral,"
                "https://example.test/identity,https://example.test/profile,,2026-06-25,Missing status\n"
                "Draft Rose,Draft Rose EDP,Fixture House,EDP,"
                "floral,bergamot,rose,musk,floral,"
                "https://example.test/identity,https://example.test/profile,"
                "draft,2026-06-25,Not reviewed\n"
                "Empty Profile,Empty Profile EDP,Fixture House,EDP,,,,,,"
                "https://example.test/identity,https://example.test/profile,"
                "reviewed,2026-06-25,No scent facts\n"
                "Malformed,Malformed EDP,Fixture House,EDP,floral,bergamot,"
                "rose,musk,floral,https://example.test/identity,"
                "https://example.test/profile,reviewed,2026-06-25,Too many,values\n",
                encoding="utf-8",
            )

            previewed = self.run_catalog("curated-preview", "--source", str(source))

            self.assertEqual(previewed.returncode, 0, previewed.stderr)
            self.assertEqual(
                json.loads(previewed.stdout),
                {
                    "status": "ok",
                    "rows": {
                        "total": 9,
                        "ready": 1,
                        "rejected": 7,
                        "duplicates": 1,
                    },
                    "rejections": [
                        {
                            "source_row": 4,
                            "reason": "missing brand",
                        },
                        {
                            "source_row": 5,
                            "reason": "missing identity_source_urls",
                        },
                        {
                            "source_row": 6,
                            "reason": "missing scent_profile_source_urls",
                        },
                        {
                            "source_row": 7,
                            "reason": "missing curator_review_status",
                        },
                        {
                            "source_row": 8,
                            "reason": "curator review status is not reviewed",
                        },
                        {
                            "source_row": 9,
                            "reason": "missing Scent Profile facts",
                        },
                        {
                            "source_row": 10,
                            "reason": "malformed columns",
                        },
                    ],
                    "duplicates": [
                        {
                            "source_row": 3,
                            "fragrance_edition": "Sample Rose EDP",
                            "brand": "Fixture House",
                        }
                    ],
                    "missing_source_urls": {
                        "identity": [5],
                        "scent_profile": [6],
                    },
                    "review_status": {
                        "missing": [7],
                        "not_reviewed": [8],
                    },
                    "batch_1": {
                        "ready": 1,
                        "too_weak": 0,
                        "too_weak_rows": [],
                    },
                    "missing_note_groups": {
                        "top": [],
                        "middle": [],
                        "base": [],
                    },
                    "coverage": {
                        "scent_families": [
                            {"scent_family": "floral", "count": 1, "source_rows": [2]}
                        ],
                        "repeated_brands": [],
                        "common_notes": [],
                    },
                },
            )

    def test_curator_previews_batch_1_quality_for_known_scent_profile_groups(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            source = Path(temporary_directory) / "curated-quality.csv"
            source.write_text(
                "fragrance_name,fragrance_edition_name,brand,concentration,"
                "main_accords,top_notes,middle_notes,base_notes,scent_family,"
                "identity_source_urls,scent_profile_source_urls,"
                "curator_review_status,curator_reviewed_on,curation_notes\n"
                "Zero Groups,Zero Groups EDP,Fixture House,EDP,,,,,,"
                "https://example.test/zero-id,https://example.test/zero-profile,"
                "reviewed,2026-06-25,Rejected by import rules\n"
                "One Group,One Group EDP,Fixture House,EDP,floral,,,,,"
                "https://example.test/one-id,https://example.test/one-profile,"
                "reviewed,2026-06-25,Importable but weak for Batch 1\n"
                "Two Groups,Two Groups EDP,Fixture House,EDP,woody,cedar,,,,"
                "https://example.test/two-id,https://example.test/two-profile,"
                "reviewed,2026-06-25,Batch 1 ready\n"
                "Three Groups,Three Groups EDP,Fixture House,EDP,fresh,lemon,"
                "neroli,musk,citrus,https://example.test/three-id,"
                "https://example.test/three-profile,reviewed,2026-06-25,"
                "Batch 1 ready\n",
                encoding="utf-8",
            )

            previewed = self.run_catalog("curated-preview", "--source", str(source))

            self.assertEqual(previewed.returncode, 0, previewed.stderr)
            report = json.loads(previewed.stdout)
            self.assertEqual(
                report["rows"],
                {
                    "total": 4,
                    "ready": 3,
                    "rejected": 1,
                    "duplicates": 0,
                },
            )
            self.assertEqual(
                report["batch_1"],
                {
                    "ready": 2,
                    "too_weak": 1,
                    "too_weak_rows": [
                        {
                            "source_row": 3,
                            "fragrance_edition": "One Group EDP",
                            "brand": "Fixture House",
                            "known_scent_profile_groups": ["main_accords"],
                            "unknown_scent_profile_groups": [
                                "note_pyramid",
                                "scent_family",
                            ],
                        }
                    ],
                },
            )
            self.assertEqual(
                report["missing_note_groups"],
                {
                    "top": [3],
                    "middle": [3, 4],
                    "base": [3, 4],
                },
            )

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

    def test_cheaper_scent_matches_use_same_size_comparable_price_snapshots(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            database = Path(temporary_directory) / "catalog.sqlite3"
            self.create_browse_fixture(database)
            self.add_comparable_prices(
                database,
                [
                    (
                        1,
                        100,
                        80,
                        "2026-06-01",
                        "Fixture price sheet",
                        "fixture://rose-edt",
                    ),
                    (
                        2,
                        100,
                        110,
                        "2026-06-01",
                        "Fixture price sheet",
                        "fixture://rose-edp",
                    ),
                    (4, 100, None, None, None, None),
                ],
            )

            matched = self.run_catalog(
                "scent-matches",
                "--database",
                str(database),
                "--edition-id",
                "2",
                "--limit",
                "3",
                "--cheaper-only",
            )

            self.assertEqual(matched.returncode, 0, matched.stderr)
            response = json.loads(matched.stdout)
            self.assertEqual(response["status"], "ok")
            self.assertEqual(
                response["reference"]["comparable_price"],
                {
                    "status": "known",
                    "amount_usd": 110.0,
                    "currency": "USD",
                    "market": "US",
                    "bottle_size_ml": 100.0,
                    "price_per_ml_usd": 1.1,
                    "observed_on": "2026-06-01",
                    "source": {
                        "name": "Fixture price sheet",
                        "url": "fixture://rose-edp",
                    },
                    "snapshot_notice": "Dated United States USD Comparable Price snapshot; not a live price or availability promise.",
                },
            )
            self.assertEqual(
                [result["fragrance_edition_id"] for result in response["results"]],
                [1],
            )
            self.assertEqual(
                response["results"][0]["comparable_price"],
                {
                    "status": "known",
                    "amount_usd": 80.0,
                    "currency": "USD",
                    "market": "US",
                    "bottle_size_ml": 100.0,
                    "price_per_ml_usd": 0.8,
                    "observed_on": "2026-06-01",
                    "source": {
                        "name": "Fixture price sheet",
                        "url": "fixture://rose-edt",
                    },
                    "snapshot_notice": "Dated United States USD Comparable Price snapshot; not a live price or availability promise.",
                },
            )
            self.assertEqual(
                response["results"][0]["price_comparison"],
                {
                    "cheaper_filter": "included",
                    "strictly_cheaper": True,
                    "basis": "same_bottle_size",
                    "reference_amount_usd": 110.0,
                    "candidate_amount_usd": 80.0,
                    "reference_bottle_size_ml": 100.0,
                    "candidate_bottle_size_ml": 100.0,
                    "reference_price_per_ml_usd": 1.1,
                    "candidate_price_per_ml_usd": 0.8,
                },
            )
            self.assertEqual(
                response["results"][0]["scent_match"],
                {
                    "method": "exact_cosine",
                    "model_specific_score": 0.5,
                    "score_basis": "Exact cosine over NosePrint Scent Profile embeddings; not a probability or percent-identical claim.",
                    "strength_label": "weak",
                },
            )
            self.assertNotIn("Sample Rose Load Test", matched.stdout)

    def test_priced_scent_matches_show_unequal_and_unknown_prices_without_cheaper_claims(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            database = Path(temporary_directory) / "catalog.sqlite3"
            self.create_browse_fixture(database)
            self.add_comparable_prices(
                database,
                [
                    (
                        1,
                        50,
                        60,
                        "2026-06-01",
                        "Fixture price sheet",
                        "fixture://rose-edt-50",
                    ),
                    (
                        2,
                        100,
                        110,
                        "2026-06-01",
                        "Fixture price sheet",
                        "fixture://rose-edp-100",
                    ),
                    (4, 100, None, None, None, None),
                ],
            )

            unpriced = self.run_catalog(
                "scent-matches",
                "--database",
                str(database),
                "--edition-id",
                "2",
                "--limit",
                "3",
            )
            priced = self.run_catalog(
                "scent-matches",
                "--database",
                str(database),
                "--edition-id",
                "2",
                "--limit",
                "3",
                "--show-prices",
            )

            self.assertEqual(unpriced.returncode, 0, unpriced.stderr)
            self.assertEqual(priced.returncode, 0, priced.stderr)
            unpriced_results = json.loads(unpriced.stdout)["results"]
            priced_results = json.loads(priced.stdout)["results"]
            self.assertEqual(
                [result["fragrance_edition_id"] for result in priced_results],
                [1, 4],
            )
            self.assertEqual(
                [result["scent_match"] for result in priced_results],
                [result["scent_match"] for result in unpriced_results],
            )
            self.assertEqual(
                priced_results[0]["price_comparison"],
                {
                    "cheaper_filter": "excluded",
                    "strictly_cheaper": False,
                    "basis": "different_bottle_size",
                    "reference_amount_usd": 110.0,
                    "candidate_amount_usd": 60.0,
                    "reference_bottle_size_ml": 100.0,
                    "candidate_bottle_size_ml": 50.0,
                    "reference_price_per_ml_usd": 1.1,
                    "candidate_price_per_ml_usd": 1.2,
                },
            )
            self.assertEqual(
                priced_results[1]["comparable_price"],
                {
                    "status": "unknown",
                    "message": "Comparable Price is unknown and has not been guessed.",
                },
            )
            self.assertEqual(
                priced_results[1]["price_comparison"],
                {
                    "cheaper_filter": "excluded",
                    "strictly_cheaper": False,
                    "basis": "unknown_price",
                    "message": "Unknown Comparable Prices cannot support a strict cheaper claim.",
                },
            )

    def test_scent_matches_show_known_and_unknown_wear_profile_facts(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            database = Path(temporary_directory) / "catalog.sqlite3"
            self.create_browse_fixture(database)
            self.add_wear_profiles(
                database,
                [
                    (1, "moderate", "soft"),
                    (2, "long-lasting", "moderate"),
                    (4, None, None),
                ],
            )

            matched = self.run_catalog(
                "scent-matches",
                "--database",
                str(database),
                "--edition-id",
                "2",
                "--limit",
                "3",
                "--show-wear-profiles",
            )

            self.assertEqual(matched.returncode, 0, matched.stderr)
            response = json.loads(matched.stdout)
            self.assertEqual(
                response["reference"]["wear_profile"],
                {
                    "longevity": "long-lasting",
                    "projection": "moderate",
                    "skin_notice": "Wear Profile facts are reported catalog observations, not a guarantee for every person's skin.",
                },
            )
            self.assertEqual(
                [
                    (result["fragrance_edition_id"], result["wear_profile"])
                    for result in response["results"]
                ],
                [
                    (
                        1,
                        {
                            "longevity": "moderate",
                            "projection": "soft",
                            "skin_notice": "Wear Profile facts are reported catalog observations, not a guarantee for every person's skin.",
                        },
                    ),
                    (
                        4,
                        {
                            "longevity": "unknown",
                            "projection": "unknown",
                            "skin_notice": "Wear Profile facts are reported catalog observations, not a guarantee for every person's skin.",
                        },
                    ),
                ],
            )

    def test_wear_profile_filters_keep_scent_match_values_unchanged(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            database = Path(temporary_directory) / "catalog.sqlite3"
            self.create_browse_fixture(database)
            self.add_wear_profiles(
                database,
                [
                    (1, "moderate", "soft"),
                    (2, "long-lasting", "moderate"),
                    (4, None, None),
                ],
            )

            unfiltered = self.run_catalog(
                "scent-matches",
                "--database",
                str(database),
                "--edition-id",
                "2",
                "--show-wear-profiles",
            )
            filtered = self.run_catalog(
                "scent-matches",
                "--database",
                str(database),
                "--edition-id",
                "2",
                "--show-wear-profiles",
                "--wear-longevity",
                "moderate",
                "--wear-projection",
                "soft",
            )

            self.assertEqual(unfiltered.returncode, 0, unfiltered.stderr)
            self.assertEqual(filtered.returncode, 0, filtered.stderr)
            unfiltered_results = json.loads(unfiltered.stdout)["results"]
            filtered_results = json.loads(filtered.stdout)["results"]
            self.assertEqual(
                [result["fragrance_edition_id"] for result in filtered_results],
                [1],
            )
            self.assertEqual(
                filtered_results[0]["scent_match"],
                unfiltered_results[0]["scent_match"],
            )
            self.assertEqual(
                filtered_results[0]["profile_comparison"],
                unfiltered_results[0]["profile_comparison"],
            )

    def test_scent_request_interprets_beginner_traits_before_searching(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            database = Path(temporary_directory) / "catalog.sqlite3"
            self.create_browse_fixture(database)

            interpreted = self.run_catalog(
                "scent-request",
                "--database",
                str(database),
                "--wanted",
                "fresh rose",
                "--unwanted",
                "oud",
            )

            self.assertEqual(interpreted.returncode, 0, interpreted.stderr)
            self.assertEqual(
                json.loads(interpreted.stdout),
                {
                    "status": "needs_confirmation",
                    "scent_request": {
                        "wanted": "fresh rose",
                        "unwanted": "oud",
                    },
                    "interpretation": {
                        "wanted_traits": {
                            "notes": ["rose"],
                            "main_accords": ["fresh"],
                            "scent_family": "unknown",
                        },
                        "unwanted_traits": {
                            "notes": ["oud"],
                            "main_accords": [],
                            "scent_family": "unknown",
                        },
                        "unsupported_terms": [],
                        "ambiguous_terms": {},
                        "interpretation_notice": (
                            "Scent Request interpretation uses known Real Catalog "
                            "Scent Profile vocabulary only; unsupported terms are "
                            "not guessed."
                        ),
                    },
                    "next_actions": {
                        "confirm": "Run again with --confirm to search from this interpreted Scent Request.",
                        "revise": (
                            "Run again with --revise-wanted or --revise-unwanted "
                            "to inspect a revised interpretation before searching."
                        ),
                        "cancel": "Run again with --cancel to stop without searching.",
                    },
                },
            )
            self.assertNotIn("results", json.loads(interpreted.stdout))

    def test_confirmed_scent_request_returns_matches_without_persisting_request(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            database = Path(temporary_directory) / "catalog.sqlite3"
            self.create_browse_fixture(database)
            before_counts = self.catalog_identity_counts(database)

            matched = self.run_catalog(
                "scent-request",
                "--database",
                str(database),
                "--wanted",
                "fresh rose",
                "--unwanted",
                "oud",
                "--confirm",
                "--limit",
                "3",
            )

            self.assertEqual(matched.returncode, 0, matched.stderr)
            response = json.loads(matched.stdout)
            self.assertEqual(response["status"], "ok")
            self.assertEqual(
                response["reference"]["source"], "ephemeral_scent_request"
            )
            self.assertEqual(
                response["unwanted_trait_filter"],
                {
                    "mode": "exclude_known_matches",
                    "excluded_traits": {
                        "notes": ["oud"],
                        "main_accords": [],
                        "scent_family": "unknown",
                    },
                    "excluded_fragrance_edition_ids": [2],
                    "filter_notice": (
                        "Known unwanted Scent Profile traits are filtered from "
                        "results without changing the catalog."
                    ),
                },
            )
            self.assertEqual(
                [result["fragrance_edition_id"] for result in response["results"]],
                [1, 4],
            )
            self.assertEqual(
                response["results"][0]["scent_match"],
                {
                    "method": "exact_cosine",
                    "model_specific_score": 0.5,
                    "score_basis": (
                        "Exact cosine over NosePrint Scent Profile embeddings; "
                        "not a probability or percent-identical claim."
                    ),
                    "strength_label": "incomplete",
                },
            )
            self.assertEqual(
                response["results"][0]["profile_comparison"]["main_accords"],
                {
                    "shared": ["fresh"],
                    "reference_only": [],
                    "candidate_only": ["floral"],
                },
            )
            self.assertNotIn("Sample Rose Load Test", matched.stdout)
            self.assertEqual(self.catalog_identity_counts(database), before_counts)

    def test_scent_request_revision_and_cancel_do_not_search_or_persist(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            database = Path(temporary_directory) / "catalog.sqlite3"
            self.create_browse_fixture(database)
            before_counts = self.catalog_identity_counts(database)

            revised = self.run_catalog(
                "scent-request",
                "--database",
                str(database),
                "--wanted",
                "rose",
                "--revise-wanted",
                "floral iris",
                "--revise-unwanted",
                "fresh",
            )
            canceled = self.run_catalog(
                "scent-request",
                "--database",
                str(database),
                "--wanted",
                "rose",
                "--cancel",
            )

            self.assertEqual(revised.returncode, 0, revised.stderr)
            self.assertEqual(canceled.returncode, 0, canceled.stderr)
            revised_response = json.loads(revised.stdout)
            self.assertEqual(revised_response["status"], "needs_confirmation")
            self.assertEqual(
                revised_response["scent_request"],
                {"wanted": "floral iris", "unwanted": "fresh"},
            )
            self.assertEqual(
                revised_response["interpretation"]["ambiguous_terms"],
                {"floral": ["main_accords", "scent_family"]},
            )
            self.assertNotIn("results", revised_response)
            self.assertEqual(json.loads(canceled.stdout)["status"], "canceled")
            self.assertEqual(self.catalog_identity_counts(database), before_counts)

    def test_scent_request_handles_empty_unsupported_and_no_result_requests(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            database = Path(temporary_directory) / "catalog.sqlite3"
            self.create_browse_fixture(database)

            empty = self.run_catalog(
                "scent-request",
                "--database",
                str(database),
                "--wanted",
                "   ",
                "--confirm",
            )
            unsupported = self.run_catalog(
                "scent-request",
                "--database",
                str(database),
                "--wanted",
                "sparkly dragon",
                "--confirm",
            )
            no_result = self.run_catalog(
                "scent-request",
                "--database",
                str(database),
                "--wanted",
                "rose",
                "--unwanted",
                "rose iris",
                "--confirm",
            )

            self.assertEqual(empty.returncode, 0, empty.stderr)
            self.assertEqual(unsupported.returncode, 0, unsupported.stderr)
            self.assertEqual(no_result.returncode, 0, no_result.stderr)
            self.assertEqual(json.loads(empty.stdout)["status"], "empty")
            unsupported_response = json.loads(unsupported.stdout)
            self.assertEqual(unsupported_response["status"], "unsupported")
            self.assertEqual(
                unsupported_response["interpretation"]["unsupported_terms"],
                ["dragon", "sparkly"],
            )
            no_result_response = json.loads(no_result.stdout)
            self.assertEqual(no_result_response["status"], "no_matches")
            self.assertEqual(no_result_response["results"], [])
            self.assertEqual(
                no_result_response["unwanted_trait_filter"][
                    "excluded_fragrance_edition_ids"
                ],
                [1, 2, 4],
            )

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

    def test_qdrant_index_health_reports_missing_rebuildable_index(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            workspace = Path(temporary_directory)
            database = workspace / "catalog.sqlite3"
            index = workspace / "qdrant-index.json"
            self.create_browse_fixture(database)

            health = self.run_catalog(
                "qdrant-health",
                "--database",
                str(database),
                "--index",
                str(index),
            )

            self.assertEqual(health.returncode, 0, health.stderr)
            self.assertEqual(
                json.loads(health.stdout),
                {
                    "status": "missing",
                    "sqlite_catalog": {
                        "status": "ok",
                        "eligible_real_catalog_records": 3,
                    },
                    "embedding_runtime": {
                        "status": "ok",
                        "model": "noseprint-hash-embedding-384",
                        "model_version": "1",
                        "pipeline_version": "scent-profile-serialization-v1",
                        "dimensions": 384,
                        "runtime_device": "cpu",
                    },
                    "qdrant_index": {
                        "status": "missing",
                        "path": str(index),
                        "message": "Rebuild the Qdrant index from SQLite before serving ANN Scent Matches.",
                    },
                    "rebuild_command": [
                        sys.executable,
                        "-m",
                        "noseprint.catalog",
                        "rebuild-qdrant-index",
                        "--database",
                        str(database),
                        "--index",
                        str(index),
                    ],
                },
            )

    def test_qdrant_health_distinguishes_empty_real_catalog(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            workspace = Path(temporary_directory)
            database = workspace / "catalog.sqlite3"
            index = workspace / "qdrant-index.json"
            self.run_catalog(
                "generate-scale-test-catalog",
                "--database",
                str(database),
                "--records",
                "2",
                "--seed",
                "1234",
            )

            health = self.run_catalog(
                "qdrant-health",
                "--database",
                str(database),
                "--index",
                str(index),
            )

            self.assertEqual(health.returncode, 0, health.stderr)
            response = json.loads(health.stdout)
            self.assertEqual(response["status"], "empty_catalog")
            self.assertEqual(
                response["sqlite_catalog"],
                {
                    "status": "empty",
                    "eligible_real_catalog_records": 0,
                    "message": "Import Real Catalog Scent Profiles into SQLite before serving shopper search.",
                },
            )
            self.assertEqual(response["qdrant_index"]["status"], "not_applicable")
            self.assertIn("import", response["next_actions"][0])

    def test_qdrant_health_reports_clear_cpu_fallback_when_cuda_is_requested(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            workspace = Path(temporary_directory)
            database = workspace / "catalog.sqlite3"
            index = workspace / "qdrant-index.json"
            self.create_browse_fixture(database)

            health = self.run_catalog(
                "qdrant-health",
                "--database",
                str(database),
                "--index",
                str(index),
                extra_env={
                    "NOSEPRINT_EMBEDDING_DEVICE": "cuda",
                    "NOSEPRINT_CUDA_SUPPORTED": "0",
                },
            )

            self.assertEqual(health.returncode, 0, health.stderr)
            self.assertEqual(
                json.loads(health.stdout)["embedding_runtime"],
                {
                    "status": "fallback",
                    "model": "noseprint-hash-embedding-384",
                    "model_version": "1",
                    "pipeline_version": "scent-profile-serialization-v1",
                    "dimensions": 384,
                    "requested_device": "cuda",
                    "runtime_device": "cpu",
                    "message": "CUDA embedding runtime is unavailable; using the practical CPU fallback.",
                },
            )

    def test_qdrant_health_reports_cuda_when_embedding_runtime_supports_it(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            workspace = Path(temporary_directory)
            database = workspace / "catalog.sqlite3"
            index = workspace / "qdrant-index.json"
            self.create_browse_fixture(database)

            health = self.run_catalog(
                "qdrant-health",
                "--database",
                str(database),
                "--index",
                str(index),
                extra_env={
                    "NOSEPRINT_EMBEDDING_DEVICE": "auto",
                    "NOSEPRINT_CUDA_SUPPORTED": "1",
                },
            )

            self.assertEqual(health.returncode, 0, health.stderr)
            self.assertEqual(
                json.loads(health.stdout)["embedding_runtime"],
                {
                    "status": "accelerated",
                    "model": "noseprint-hash-embedding-384",
                    "model_version": "1",
                    "pipeline_version": "scent-profile-serialization-v1",
                    "dimensions": 384,
                    "runtime_device": "cuda",
                    "requested_device": "auto",
                    "message": "CUDA embedding runtime is selected for local Scent Profile embeddings.",
                },
            )

    def test_rebuild_qdrant_index_writes_real_catalog_points_from_sqlite(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            workspace = Path(temporary_directory)
            database = workspace / "catalog.sqlite3"
            index = workspace / "qdrant-index.json"
            self.create_browse_fixture(database)

            rebuilt = self.run_catalog(
                "rebuild-qdrant-index",
                "--database",
                str(database),
                "--index",
                str(index),
            )

            self.assertEqual(rebuilt.returncode, 0, rebuilt.stderr)
            self.assertEqual(
                json.loads(rebuilt.stdout),
                {
                    "status": "rebuilt",
                    "qdrant_index": {
                        "path": str(index),
                        "points": 3,
                        "catalog_fingerprint": "f2c93570c048980cc0174477610a7550284cb8c1dab3f0b723d4acfbb76d5d8a",
                    },
                    "embedding_runtime": {
                        "model": "noseprint-hash-embedding-384",
                        "model_version": "1",
                        "pipeline_version": "scent-profile-serialization-v1",
                        "dimensions": 384,
                        "runtime_device": "cpu",
                    },
                },
            )
            index_document = json.loads(index.read_text(encoding="utf-8"))
            self.assertEqual(index_document["metadata"]["points"], 3)
            self.assertEqual(
                [point["payload"] for point in index_document["points"]],
                [
                    {"fragrance_edition_id": 1, "catalog_kind": "real"},
                    {"fragrance_edition_id": 2, "catalog_kind": "real"},
                    {"fragrance_edition_id": 4, "catalog_kind": "real"},
                ],
            )
            self.assertEqual(len(index_document["points"][0]["vector"]), 384)
            self.assertNotIn("Sample Rose Load Test", index.read_text(encoding="utf-8"))

    def test_qdrant_index_health_reports_fresh_index_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            workspace = Path(temporary_directory)
            database = workspace / "catalog.sqlite3"
            index = workspace / "qdrant-index.json"
            self.create_browse_fixture(database)
            self.run_catalog(
                "rebuild-qdrant-index",
                "--database",
                str(database),
                "--index",
                str(index),
            )

            health = self.run_catalog(
                "qdrant-health",
                "--database",
                str(database),
                "--index",
                str(index),
            )

            self.assertEqual(health.returncode, 0, health.stderr)
            self.assertEqual(
                json.loads(health.stdout)["qdrant_index"],
                {
                    "status": "fresh",
                    "path": str(index),
                    "points": 3,
                    "catalog_fingerprint": "f2c93570c048980cc0174477610a7550284cb8c1dab3f0b723d4acfbb76d5d8a",
                    "index_schema_version": "qdrant-index-v1",
                },
            )

    def test_qdrant_index_health_reports_stale_catalog_fingerprint(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            workspace = Path(temporary_directory)
            database = workspace / "catalog.sqlite3"
            index = workspace / "qdrant-index.json"
            self.create_browse_fixture(database)
            self.run_catalog(
                "rebuild-qdrant-index",
                "--database",
                str(database),
                "--index",
                str(index),
            )
            connection = sqlite3.connect(database)
            try:
                connection.execute(
                    """
                    UPDATE scent_profiles
                    SET middle_notes_json = '["rose", "jasmine"]'
                    WHERE fragrance_edition_id = 1
                    """
                )
                connection.commit()
            finally:
                connection.close()

            health = self.run_catalog(
                "qdrant-health",
                "--database",
                str(database),
                "--index",
                str(index),
            )

            self.assertEqual(health.returncode, 0, health.stderr)
            response = json.loads(health.stdout)
            self.assertEqual(response["status"], "stale")
            self.assertEqual(response["qdrant_index"]["status"], "stale")
            self.assertIn("Rebuild", response["qdrant_index"]["message"])

    def test_scent_matches_use_fresh_qdrant_index_and_hydrate_from_sqlite(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            workspace = Path(temporary_directory)
            database = workspace / "catalog.sqlite3"
            index = workspace / "qdrant-index.json"
            self.create_browse_fixture(database)
            self.run_catalog(
                "rebuild-qdrant-index",
                "--database",
                str(database),
                "--index",
                str(index),
            )

            matched = self.run_catalog(
                "scent-matches",
                "--database",
                str(database),
                "--index",
                str(index),
                "--edition-id",
                "2",
                "--limit",
                "3",
            )

            self.assertEqual(matched.returncode, 0, matched.stderr)
            response = json.loads(matched.stdout)
            self.assertEqual(response["status"], "ok")
            self.assertEqual(
                response["retrieval"],
                {
                    "method": "qdrant_ann",
                    "index_status": "fresh",
                    "exact_baseline_method": "exact_cosine",
                    "recall_at_k": 1.0,
                    "embedding_latency_ms": 0,
                    "retrieval_latency_ms": 0,
                    "hydration_latency_ms": 0,
                },
            )
            self.assertEqual(
                [result["fragrance_edition_id"] for result in response["results"]],
                [1, 4],
            )
            self.assertEqual(
                {result["scent_match"]["method"] for result in response["results"]},
                {"qdrant_ann"},
            )
            self.assertEqual(response["results"][0]["fragrance"], "Sample Rose")
            self.assertNotIn("Sample Rose Load Test", matched.stdout)

    def test_scent_matches_refuse_stale_qdrant_index_until_rebuilt(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            workspace = Path(temporary_directory)
            database = workspace / "catalog.sqlite3"
            index = workspace / "qdrant-index.json"
            self.create_browse_fixture(database)
            self.run_catalog(
                "rebuild-qdrant-index",
                "--database",
                str(database),
                "--index",
                str(index),
            )
            connection = sqlite3.connect(database)
            try:
                connection.execute(
                    """
                    UPDATE scent_profiles
                    SET top_notes_json = '["pink pepper", "lemon"]'
                    WHERE fragrance_edition_id = 2
                    """
                )
                connection.commit()
            finally:
                connection.close()

            matched = self.run_catalog(
                "scent-matches",
                "--database",
                str(database),
                "--index",
                str(index),
                "--edition-id",
                "2",
            )

            self.assertEqual(matched.returncode, 0, matched.stderr)
            self.assertEqual(json.loads(matched.stdout)["status"], "index_unavailable")
            self.assertIn("Rebuild", json.loads(matched.stdout)["message"])
            self.assertNotIn("results", json.loads(matched.stdout))

    def test_scent_matches_explain_missing_qdrant_index(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            workspace = Path(temporary_directory)
            database = workspace / "catalog.sqlite3"
            index = workspace / "missing-qdrant-index.json"
            self.create_browse_fixture(database)

            matched = self.run_catalog(
                "scent-matches",
                "--database",
                str(database),
                "--index",
                str(index),
                "--edition-id",
                "2",
            )

            self.assertEqual(matched.returncode, 0, matched.stderr)
            response = json.loads(matched.stdout)
            self.assertEqual(response["status"], "index_unavailable")
            self.assertEqual(response["qdrant_index"]["status"], "missing")
            self.assertIn("Rebuild", response["message"])

    def test_scent_matches_explain_unreadable_qdrant_index(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            workspace = Path(temporary_directory)
            database = workspace / "catalog.sqlite3"
            index = workspace / "broken-qdrant-index.json"
            self.create_browse_fixture(database)
            index.write_text("{not valid json", encoding="utf-8")

            matched = self.run_catalog(
                "scent-matches",
                "--database",
                str(database),
                "--index",
                str(index),
                "--edition-id",
                "2",
            )

            self.assertEqual(matched.returncode, 0, matched.stderr)
            response = json.loads(matched.stdout)
            self.assertEqual(response["status"], "index_unavailable")
            self.assertEqual(response["qdrant_index"]["status"], "unreadable")
            self.assertIn("Rebuild", response["message"])
            self.assertNotIn("Traceback", matched.stderr)

    def test_reference_match_set_evaluates_exact_and_qdrant_recall(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            workspace = Path(temporary_directory)
            database = workspace / "catalog.sqlite3"
            index = workspace / "qdrant-index.json"
            reference_match_set = workspace / "reference-match-set.json"
            self.create_browse_fixture(database)
            self.add_surprising_match_fixture(database)
            reference_match_set.write_text(
                json.dumps(
                    {
                        "id": "fixture-reference-match-set-v1",
                        "description": "Human-checked fixture alternatives.",
                        "purpose": "evaluation_only",
                        "entries": [
                            {
                                "reference_fragrance_edition_id": 2,
                                "reasonable_alternative_ids": [1, 5, 6],
                                "note": "Rose alternatives checked for evaluation.",
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )
            self.run_catalog(
                "rebuild-qdrant-index",
                "--database",
                str(database),
                "--index",
                str(index),
            )

            evaluated = self.run_catalog(
                "evaluate-reference-matches",
                "--database",
                str(database),
                "--index",
                str(index),
                "--reference-match-set",
                str(reference_match_set),
                "--limit",
                "3",
            )

            self.assertEqual(evaluated.returncode, 0, evaluated.stderr)
            report = json.loads(evaluated.stdout)
            self.assertEqual(report["status"], "ok")
            self.assertEqual(
                report["reference_match_set"],
                {
                    "id": "fixture-reference-match-set-v1",
                    "purpose": "evaluation_only",
                    "entries": 1,
                    "separation_notice": (
                        "Reference Match Set data is evaluation-only; it is not "
                        "training data, user activity, a Real Catalog source, or "
                        "embedding input."
                    ),
                },
            )
            self.assertEqual(report["configuration"]["top_k"], 3)
            self.assertEqual(
                report["configuration"]["embedding"],
                {
                    "model": "noseprint-hash-embedding-384",
                    "model_version": "1",
                    "pipeline_version": "scent-profile-serialization-v1",
                    "dimensions": 384,
                    "runtime_device": "cpu",
                },
            )
            self.assertEqual(report["configuration"]["exact"]["method"], "exact_cosine")
            self.assertEqual(report["configuration"]["qdrant_ann"]["method"], "qdrant_ann")
            self.assertEqual(report["configuration"]["qdrant_ann"]["index_status"], "fresh")
            self.assertEqual(
                report["metrics"],
                {
                    "exact_cosine": {
                        "cases": 1,
                        "expected_alternatives": 3,
                        "retrieved_expected_alternatives": 3,
                        "recall_at_k": 1.0,
                        "latency_ms": 0,
                    },
                    "qdrant_ann": {
                        "cases": 1,
                        "expected_alternatives": 3,
                        "retrieved_expected_alternatives": 3,
                        "recall_at_k": 1.0,
                        "latency_ms": 0,
                    },
                },
            )
            case = report["cases"][0]
            self.assertEqual(case["reference_fragrance_edition_id"], 2)
            self.assertEqual(case["expected_alternative_ids"], [1, 5, 6])
            self.assertEqual(set(case["exact_cosine"]["hit_ids"]), {1, 5, 6})
            self.assertEqual(set(case["qdrant_ann"]["hit_ids"]), {1, 5, 6})
            self.assertIn(
                "profile_comparison",
                case["inspectable_outcomes"][0],
            )
            self.assertIn(
                case["inspectable_outcomes"][0]["scent_match"]["strength_label"],
                {"weak", "surprising"},
            )
            self.assertNotIn("Sample Rose Load Test", evaluated.stdout)

    def test_reference_match_set_does_not_enter_catalog_results(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            workspace = Path(temporary_directory)
            database = workspace / "catalog.sqlite3"
            index = workspace / "qdrant-index.json"
            reference_match_set = workspace / "reference-match-set.json"
            self.create_browse_fixture(database)
            before_counts = self.catalog_identity_counts(database)
            reference_match_set.write_text(
                json.dumps(
                    {
                        "id": "outside-catalog-reference-match-set-v1",
                        "description": "Evaluation data with a non-catalog label.",
                        "purpose": "evaluation_only",
                        "entries": [
                            {
                                "reference_fragrance_edition_id": 2,
                                "reasonable_alternative_ids": [1],
                                "human_checked_label": "Never Catalog Rose",
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )
            self.run_catalog(
                "rebuild-qdrant-index",
                "--database",
                str(database),
                "--index",
                str(index),
            )

            evaluated = self.run_catalog(
                "evaluate-reference-matches",
                "--database",
                str(database),
                "--index",
                str(index),
                "--reference-match-set",
                str(reference_match_set),
                "--limit",
                "1",
            )
            browsed = self.run_catalog(
                "browse",
                "--database",
                str(database),
                "--query",
                "Never Catalog Rose",
            )

            self.assertEqual(evaluated.returncode, 0, evaluated.stderr)
            self.assertEqual(browsed.returncode, 0, browsed.stderr)
            self.assertEqual(self.catalog_identity_counts(database), before_counts)
            browse_response = json.loads(browsed.stdout)
            self.assertEqual(browse_response["status"], "no_matches")
            self.assertEqual(browse_response["results"], [])

    def test_scale_test_catalog_generation_is_deterministic_and_not_shopper_inventory(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            workspace = Path(temporary_directory)
            first_database = workspace / "first.sqlite3"
            second_database = workspace / "second.sqlite3"

            first_generated = self.run_catalog(
                "generate-scale-test-catalog",
                "--database",
                str(first_database),
                "--records",
                "5",
                "--seed",
                "1234",
            )
            second_generated = self.run_catalog(
                "generate-scale-test-catalog",
                "--database",
                str(second_database),
                "--records",
                "5",
                "--seed",
                "1234",
            )
            browsed = self.run_catalog(
                "browse",
                "--database",
                str(first_database),
                "--query",
                "Scale Test",
            )

            self.assertEqual(first_generated.returncode, 0, first_generated.stderr)
            self.assertEqual(second_generated.returncode, 0, second_generated.stderr)
            self.assertEqual(
                json.loads(first_generated.stdout),
                {
                    "status": "generated",
                    "scale_test_catalog": {
                        "dataset_id": "scale-test-catalog-v1",
                        "records": 5,
                        "seed": 1234,
                        "catalog_kind": "scale-test",
                        "separation_notice": (
                            "Scale-Test Catalog records are generated for benchmarks "
                            "only and are not Real Catalog shopping inventory."
                        ),
                    },
                },
            )
            self.assertEqual(
                self.scale_test_catalog_snapshot(first_database),
                self.scale_test_catalog_snapshot(second_database),
            )
            self.assertEqual(
                {
                    row["catalog_kind"]
                    for row in self.scale_test_catalog_snapshot(first_database)
                },
                {"scale-test"},
            )
            self.assertEqual(browsed.returncode, 0, browsed.stderr)
            self.assertEqual(json.loads(browsed.stdout)["status"], "no_matches")
            self.assertEqual(json.loads(browsed.stdout)["results"], [])

    def test_tidy_tuesday_parfumo_csv_imports_as_real_catalog(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            workspace = Path(temporary_directory)
            source = workspace / "parfumo_data_clean.csv"
            database = workspace / "catalog.sqlite3"
            source.write_text(
                "Number,Name,Brand,Release_Year,Concentration,Rating_Value,"
                "Rating_Count,Main_Accords,Top_Notes,Middle_Notes,Base_Notes,"
                "Perfumers,URL\n"
                '0071,Tidal Pool,CB I Hate Perfume,2004,,7.4,19,'
                '"Fresh, Aquatic",Bergamot,French lavender,"Musk, Foulness",'
                "Harry Fremont,https://www.parfumo.com/Perfumes/CB/Tidal_Pool\n"
                '0071,Tidal Pool,CB I Hate Perfume,2004,,7.4,19,'
                '"Fresh, Aquatic",Bergamot,French lavender,"Musk, Foulness",'
                "Harry Fremont,https://www.parfumo.com/Perfumes/CB/Tidal_Pool\n"
                "0162,Wet Stone,CB I Hate Perfume,2006,,,,,,,,,"
                "https://www.parfumo.com/Perfumes/CB/Wet_Stone\n",
                encoding="utf-8",
            )

            imported = self.run_catalog(
                "import-parfumo",
                "--source",
                str(source),
                "--database",
                str(database),
            )
            browsed = self.run_catalog(
                "browse",
                "--database",
                str(database),
                "--query",
                "Tidal Pool",
            )

            self.assertEqual(imported.returncode, 0, imported.stderr)
            self.assertEqual(
                json.loads(imported.stdout),
                {
                    "status": "imported",
                    "real_catalog": {
                        "dataset_id": "tidytuesday-parfumo-2024-12-10",
                        "source": str(source),
                        "catalog_kind": "real",
                        "accepted": 1,
                        "duplicates": 1,
                        "quarantined": 1,
                        "rejected": 0,
                        "catalog_notice": (
                            "TidyTuesday Parfumo records are the NosePrint Real Catalog."
                        ),
                    },
                },
            )
            self.assertEqual(
                self.real_catalog_snapshot(database),
                [
                    {
                        "id": 1,
                        "fragrance": "Tidal Pool",
                        "brand": "CB I Hate Perfume",
                        "edition": "Tidal Pool",
                        "concentration": None,
                        "catalog_kind": "real",
                        "notes_json": json.dumps(
                            ["bergamot", "foulness", "french lavender", "musk"]
                        ),
                        "main_accords_json": json.dumps(["aquatic", "fresh"]),
                        "top_notes_json": json.dumps(["bergamot"]),
                        "middle_notes_json": json.dumps(["french lavender"]),
                        "base_notes_json": json.dumps(["foulness", "musk"]),
                        "scent_family": "fresh",
                    }
                ],
            )
            self.assertEqual(browsed.returncode, 0, browsed.stderr)
            self.assertEqual(json.loads(browsed.stdout)["status"], "ok")
            self.assertEqual(
                json.loads(browsed.stdout)["results"],
                [
                    {
                        "fragrance_edition_id": 1,
                        "fragrance": "Tidal Pool",
                        "edition": "Tidal Pool",
                        "concentration": None,
                    }
                ],
            )

    def test_run_command_prepares_parfumo_real_catalog_and_index(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            workspace = Path(temporary_directory)
            source = workspace / "parfumo_data_clean.csv"
            database = workspace / "catalog.sqlite3"
            index = workspace / "qdrant-index.json"
            source.write_text(
                "Number,Name,Brand,Release_Year,Concentration,Rating_Value,"
                "Rating_Count,Main_Accords,Top_Notes,Middle_Notes,Base_Notes,"
                "Perfumers,URL\n"
                '0001,Tidal Pool,CB I Hate Perfume,2004,,7.4,19,'
                '"Fresh, Aquatic",Bergamot,French lavender,"Musk, Foulness",'
                "Harry Fremont,https://www.parfumo.com/Perfumes/CB/Tidal_Pool\n",
                encoding="utf-8",
            )

            prepared = self.run_catalog(
                "run",
                "--source",
                str(source),
                "--database",
                str(database),
                "--index",
                str(index),
                "--port",
                "0",
                "--prepare-only",
            )

            self.assertEqual(prepared.returncode, 0, prepared.stderr)
            self.assertEqual(
                json.loads(prepared.stdout),
                {
                    "status": "prepared",
                    "app": {
                        "url": "http://127.0.0.1:0/",
                        "database": str(database),
                        "index": str(index),
                        "real_catalog_records": 1,
                    },
                },
            )
            self.assertTrue(index.exists())
            self.assertEqual(
                json.loads(index.read_text(encoding="utf-8"))["metadata"]["points"],
                1,
            )

    def test_scale_test_benchmark_uses_separate_ann_path_and_reports_recall(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            workspace = Path(temporary_directory)
            database = workspace / "catalog.sqlite3"
            index = workspace / "scale-test-qdrant-index.json"
            self.run_catalog(
                "generate-scale-test-catalog",
                "--database",
                str(database),
                "--records",
                "6",
                "--seed",
                "1234",
            )

            benchmarked = self.run_catalog(
                "benchmark-scale-test-catalog",
                "--database",
                str(database),
                "--index",
                str(index),
                "--reference-edition-id",
                "1000001",
                "--limit",
                "3",
            )
            browsed = self.run_catalog(
                "browse",
                "--database",
                str(database),
                "--query",
                "Scale Test",
            )

            self.assertEqual(benchmarked.returncode, 0, benchmarked.stderr)
            report = json.loads(benchmarked.stdout)
            self.assertEqual(report["status"], "ok")
            self.assertEqual(report["catalog"], {"kind": "scale-test", "size": 6})
            self.assertEqual(
                report["configuration"],
                {
                    "top_k": 3,
                    "embedding": {
                        "model": "noseprint-hash-embedding-384",
                        "model_version": "1",
                        "pipeline_version": "scent-profile-serialization-v1",
                        "dimensions": 384,
                        "runtime_device": "cpu",
                    },
                    "exact": {"method": "exact_cosine"},
                    "qdrant_ann": {
                        "method": "qdrant_ann",
                        "index_status": "rebuilt",
                        "index_schema_version": "qdrant-scale-test-index-v1",
                        "index_path": str(index),
                    },
                },
            )
            self.assertEqual(report["metrics"]["recall_at_k"], 1.0)
            self.assertEqual(report["metrics"]["embedding_latency_ms"], 0)
            self.assertEqual(report["metrics"]["retrieval_latency_ms"], 0)
            self.assertEqual(report["metrics"]["hydration_latency_ms"], 0)
            self.assertEqual(len(report["exact_cosine"]["retrieved_ids"]), 3)
            self.assertEqual(
                report["exact_cosine"]["retrieved_ids"],
                report["qdrant_ann"]["retrieved_ids"],
            )
            index_document = json.loads(index.read_text(encoding="utf-8"))
            self.assertEqual(
                index_document["metadata"]["index_schema_version"],
                "qdrant-scale-test-index-v1",
            )
            self.assertEqual(
                {
                    point["payload"]["catalog_kind"]
                    for point in index_document["points"]
                },
                {"scale-test"},
            )
            self.assertEqual(browsed.returncode, 0, browsed.stderr)
            self.assertEqual(json.loads(browsed.stdout)["results"], [])

    def test_shopper_ann_search_ignores_malformed_scale_test_payloads(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            workspace = Path(temporary_directory)
            database = workspace / "catalog.sqlite3"
            real_index = workspace / "qdrant-index.json"
            scale_index = workspace / "scale-test-qdrant-index.json"
            self.create_browse_fixture(database)
            self.run_catalog(
                "rebuild-qdrant-index",
                "--database",
                str(database),
                "--index",
                str(real_index),
            )
            self.run_catalog(
                "benchmark-scale-test-catalog",
                "--database",
                str(database),
                "--index",
                str(scale_index),
                "--reference-edition-id",
                "3",
                "--limit",
                "1",
            )
            real_index_document = json.loads(real_index.read_text(encoding="utf-8"))
            scale_point = json.loads(scale_index.read_text(encoding="utf-8"))[
                "points"
            ][0]
            forged_point = {
                "id": scale_point["id"],
                "vector": scale_point["vector"],
                "payload": {
                    "fragrance_edition_id": 1,
                    "catalog_kind": "real",
                },
            }
            real_index_document["points"].append(forged_point)
            real_index.write_text(
                json.dumps(real_index_document, indent=2) + "\n",
                encoding="utf-8",
            )

            matched = self.run_catalog(
                "scent-matches",
                "--database",
                str(database),
                "--index",
                str(real_index),
                "--edition-id",
                "2",
                "--limit",
                "3",
            )

            self.assertEqual(matched.returncode, 0, matched.stderr)
            response = json.loads(matched.stdout)
            self.assertEqual(
                [result["fragrance_edition_id"] for result in response["results"]],
                [1, 4],
            )
            self.assertNotIn("Sample Rose Load Test", matched.stdout)

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
                            "id": "blocked-real-catalog-candidate-v1",
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

    def test_passing_audit_imports_reviewed_curated_real_catalog_profile(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            workspace = Path(temporary_directory)
            source = workspace / "curated.csv"
            source.write_text(
                "fragrance_name,fragrance_edition_name,brand,concentration,"
                "main_accords,top_notes,middle_notes,base_notes,scent_family,"
                "identity_source_urls,scent_profile_source_urls,"
                "curator_review_status,curator_reviewed_on,curation_notes\n"
                "Sample Rose,Sample Rose EDP,Fixture House,EDP,"
                '"floral, fresh","bergamot, pink pepper",rose,musk,floral,'
                "https://example.test/identity,https://example.test/profile,"
                "reviewed,2026-06-25,Manual fixture review\n",
                encoding="utf-8",
            )
            manifest = workspace / "audit.json"
            manifest.write_text(
                json.dumps(
                    {
                        "dataset": {
                            "id": "curated-seed-fixture-v1",
                            "download_url": "file:///tmp/curated.csv",
                            "publisher": "NosePrint maintainers",
                            "claimed_license": "manual-review",
                            "license_evidence": ["https://example.test/source-policy"],
                            "license_chain_status": "passed",
                            "provenance_evidence": ["https://example.test/provenance"],
                            "provenance_status": "passed",
                            "expected_schema": CURATED_REAL_CATALOG_SCHEMA,
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
            browsed = self.run_catalog(
                "browse", "--database", str(database), "--query", "sample rose"
            )
            inspected = self.run_catalog("inspect", "--database", str(database))
            inspected_profile = self.run_catalog(
                "scent-profile",
                "--database",
                str(database),
                "--edition-id",
                "1",
            )

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
                json.loads(browsed.stdout),
                {
                    "status": "ok",
                    "query": "sample rose",
                    "results": [
                        {
                            "fragrance_edition_id": 1,
                            "fragrance": "Sample Rose",
                            "edition": "Sample Rose EDP",
                            "concentration": "EDP",
                        }
                    ],
                },
            )
            self.assertEqual(
                json.loads(inspected.stdout),
                [
                    {
                        "brand": "Fixture House",
                        "fragrance": "Sample Rose",
                        "edition": "Sample Rose EDP",
                        "concentration": "EDP",
                        "notes": ["bergamot", "musk", "pink pepper", "rose"],
                        "source_dataset": "curated-seed-fixture-v1",
                        "source_row": 2,
                        "original_name": "Sample Rose",
                        "source_urls": {
                            "identity": ["https://example.test/identity"],
                            "scent_profile": ["https://example.test/profile"],
                        },
                    }
                ],
            )
            self.assertEqual(
                json.loads(inspected_profile.stdout),
                {
                    "status": "ok",
                    "fragrance_edition_id": 1,
                    "fragrance": "Sample Rose",
                    "edition": "Sample Rose EDP",
                    "concentration": "EDP",
                    "scent_profile": {
                        "main_accords": ["floral", "fresh"],
                        "note_pyramid": {
                            "top": ["bergamot", "pink pepper"],
                            "middle": ["rose"],
                            "base": ["musk"],
                        },
                        "scent_family": "floral",
                    },
                },
            )

    def test_curated_import_reports_duplicates_rejections_and_quarantines(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            workspace = Path(temporary_directory)
            source = workspace / "curated-mixed.csv"
            source.write_text(
                "fragrance_name,fragrance_edition_name,brand,concentration,"
                "main_accords,top_notes,middle_notes,base_notes,scent_family,"
                "identity_source_urls,scent_profile_source_urls,"
                "curator_review_status,curator_reviewed_on,curation_notes\n"
                "Sample Rose,Sample Rose EDP,Fixture House,EDP,"
                "floral,bergamot,rose,musk,floral,"
                "https://example.test/identity,https://example.test/profile,"
                "reviewed,2026-06-25,Valid row\n"
                "Sample Rose,Sample Rose EDP,Fixture House,EDP,"
                "floral,bergamot,rose,musk,floral,"
                "https://example.test/identity,https://example.test/profile,"
                "reviewed,2026-06-25,Duplicate row\n"
                "Nameless,Nameless EDP,,EDP,floral,bergamot,rose,musk,floral,"
                "https://example.test/identity,https://example.test/profile,"
                "reviewed,2026-06-25,Missing brand\n"
                "Draft Rose,Draft Rose EDP,Fixture House,EDP,"
                "floral,bergamot,rose,musk,floral,"
                "https://example.test/identity,https://example.test/profile,"
                "draft,2026-06-25,Not reviewed\n"
                "Unsourced Rose,Unsourced Rose EDP,Fixture House,EDP,"
                "floral,bergamot,rose,musk,floral,"
                "https://example.test/identity,,reviewed,2026-06-25,Missing source\n"
                "Empty Profile,Empty Profile EDP,Fixture House,EDP,,,,,,"
                "https://example.test/identity,https://example.test/profile,"
                "reviewed,2026-06-25,No scent facts\n"
                "Malformed,Malformed EDP,Fixture House,EDP,floral,bergamot,"
                "rose,musk,floral,https://example.test/identity,"
                "https://example.test/profile,reviewed,2026-06-25,Too many,values\n",
                encoding="utf-8",
            )
            manifest = workspace / "audit.json"
            manifest.write_text(
                json.dumps(
                    {
                        "dataset": {
                            "id": "curated-mixed-fixture-v1",
                            "download_url": "file:///tmp/curated-mixed.csv",
                            "publisher": "NosePrint maintainers",
                            "claimed_license": "manual-review",
                            "license_evidence": ["https://example.test/source-policy"],
                            "license_chain_status": "passed",
                            "provenance_evidence": ["https://example.test/provenance"],
                            "provenance_status": "passed",
                            "expected_schema": CURATED_REAL_CATALOG_SCHEMA,
                            "expected_row_count": 7,
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
                    "rejected": 4,
                    "transformed": 1,
                    "duplicates": 1,
                    "quarantined": 1,
                },
            )
            self.assertEqual(
                json.loads(quarantined.stdout),
                [
                    {"source_row": 4, "disposition": "rejected", "reason": "missing brand"},
                    {
                        "source_row": 5,
                        "disposition": "rejected",
                        "reason": "curator review status is not reviewed",
                    },
                    {
                        "source_row": 6,
                        "disposition": "rejected",
                        "reason": "missing scent_profile_source_urls",
                    },
                    {
                        "source_row": 7,
                        "disposition": "quarantined",
                        "reason": "missing Scent Profile facts",
                    },
                    {
                        "source_row": 8,
                        "disposition": "rejected",
                        "reason": "malformed columns",
                    },
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

    def run_catalog(
        self,
        *arguments: str,
        extra_env: dict[str, str] | None = None,
    ) -> subprocess.CompletedProcess[str]:
        env = os.environ.copy()
        if extra_env:
            env.update(extra_env)
        return subprocess.run(
            [sys.executable, "-m", "noseprint.catalog", *arguments],
            cwd=ROOT,
            env=env,
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

    def add_comparable_prices(
        self,
        database: Path,
        rows: list[tuple[int, float, float | None, str | None, str | None, str | None]],
    ) -> None:
        connection = sqlite3.connect(database)
        try:
            connection.execute(
                """
                CREATE TABLE comparable_prices (
                    fragrance_edition_id INTEGER PRIMARY KEY REFERENCES fragrance_editions(id),
                    amount_usd REAL,
                    currency TEXT NOT NULL DEFAULT 'USD',
                    market TEXT NOT NULL DEFAULT 'US',
                    bottle_size_ml REAL NOT NULL,
                    observed_on TEXT,
                    source_name TEXT,
                    source_url TEXT
                )
                """
            )
            connection.executemany(
                """
                INSERT INTO comparable_prices
                    (fragrance_edition_id, bottle_size_ml, amount_usd,
                     observed_on, source_name, source_url)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                rows,
            )
            connection.commit()
        finally:
            connection.close()

    def add_wear_profiles(
        self,
        database: Path,
        rows: list[tuple[int, str | None, str | None]],
    ) -> None:
        connection = sqlite3.connect(database)
        try:
            connection.execute(
                """
                CREATE TABLE wear_profiles (
                    fragrance_edition_id INTEGER PRIMARY KEY REFERENCES fragrance_editions(id),
                    longevity TEXT,
                    projection TEXT
                )
                """
            )
            connection.executemany(
                """
                INSERT INTO wear_profiles
                    (fragrance_edition_id, longevity, projection)
                VALUES (?, ?, ?)
                """,
                rows,
            )
            connection.commit()
        finally:
            connection.close()

    def add_surprising_match_fixture(self, database: Path) -> None:
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

    def catalog_identity_counts(self, database: Path) -> dict[str, int]:
        connection = sqlite3.connect(database)
        try:
            return {
                "fragrances": connection.execute(
                    "SELECT COUNT(*) FROM fragrances"
                ).fetchone()[0],
                "fragrance_editions": connection.execute(
                    "SELECT COUNT(*) FROM fragrance_editions"
                ).fetchone()[0],
            }
        finally:
            connection.close()

    def scale_test_catalog_snapshot(self, database: Path) -> list[dict[str, object]]:
        connection = sqlite3.connect(database)
        connection.row_factory = sqlite3.Row
        try:
            rows = connection.execute(
                """
                SELECT fe.id, f.name AS fragrance, f.brand, fe.name AS edition,
                       fe.concentration, fe.catalog_kind, sp.notes_json,
                       sp.main_accords_json, sp.top_notes_json, sp.middle_notes_json,
                       sp.base_notes_json, sp.scent_family
                FROM fragrance_editions AS fe
                JOIN fragrances AS f ON f.id = fe.fragrance_id
                JOIN scent_profiles AS sp ON sp.fragrance_edition_id = fe.id
                ORDER BY fe.id
                """
            ).fetchall()
            return [dict(row) for row in rows]
        finally:
            connection.close()

    def real_catalog_snapshot(self, database: Path) -> list[dict[str, object]]:
        return [
            row
            for row in self.scale_test_catalog_snapshot(database)
            if row["catalog_kind"] == "real"
        ]


if __name__ == "__main__":
    unittest.main()
