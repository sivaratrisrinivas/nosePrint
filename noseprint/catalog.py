from __future__ import annotations

import argparse
import contextlib
import csv
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import io
import json
import math
import os
import re
import sqlite3
import sys
import threading
from pathlib import Path
from typing import Any, Sequence
from urllib.parse import parse_qs, urlparse


EXIT_BLOCKED = 2
MAX_SOURCE_BYTES = 50 * 1024 * 1024
REQUIRED_AUDIT_CHECKS = {"license_chain", "provenance", "schema", "row_count", "quality"}
EMBEDDING_DIMENSIONS = 384
EMBEDDING_MODEL = "noseprint-hash-embedding-384"
EMBEDDING_MODEL_VERSION = "1"
SERIALIZATION_PIPELINE_VERSION = "scent-profile-serialization-v1"
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
TIDYTUESDAY_PARFUMO_SCHEMA = [
    "Number",
    "Name",
    "Brand",
    "Release_Year",
    "Concentration",
    "Rating_Value",
    "Rating_Count",
    "Main_Accords",
    "Top_Notes",
    "Middle_Notes",
    "Base_Notes",
    "Perfumers",
    "URL",
]
TIDYTUESDAY_PARFUMO_DATASET_ID = "tidytuesday-parfumo-2024-12-10"
DEFAULT_APP_DATABASE = Path("var/noseprint.sqlite3")
DEFAULT_APP_INDEX = Path("var/qdrant-index.json")
APP_STDOUT_LOCK = threading.Lock()


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(64 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _validate_source_file(path: Path) -> None:
    if path.stat().st_size > MAX_SOURCE_BYTES:
        raise ValueError("source file exceeds the 50 MiB safety limit")


def _audit(args: argparse.Namespace) -> int:
    manifest_path = Path(args.manifest)
    source_path = Path(args.source)
    report_path = Path(args.report)
    _validate_source_file(source_path)
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    dataset = manifest["dataset"]

    with source_path.open("r", encoding="utf-8-sig", newline="") as source:
        reader = csv.reader(source)
        schema = next(reader, [])
        row_count = sum(1 for _ in reader)

    checks = {
        "license_chain": dataset.get("license_chain_status") == "passed"
        and bool(dataset.get("license_evidence")),
        "provenance": dataset.get("provenance_status") == "passed"
        and bool(dataset.get("provenance_evidence")),
        "schema": schema == dataset.get("expected_schema"),
        "row_count": row_count == dataset.get("expected_row_count"),
        "quality": dataset.get("quality_status") == "passed",
    }
    verdict = "passed" if all(checks.values()) else "inconclusive"
    report: dict[str, Any] = {
        "dataset": dataset,
        "observed": {
            "schema": schema,
            "row_count": row_count,
            "source_sha256": _sha256(source_path),
        },
        "checks": checks,
        "verdict": verdict,
    }
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(f"Audit verdict: {verdict.upper()}")
    return 0 if verdict == "passed" else EXIT_BLOCKED


def _import_catalog(args: argparse.Namespace) -> int:
    report = json.loads(Path(args.audit_report).read_text(encoding="utf-8"))
    if not _report_passes(report):
        print("Import blocked: audit verdict is not PASSED.", file=sys.stderr)
        return EXIT_BLOCKED
    source_path = Path(args.source)
    _validate_source_file(source_path)
    if _sha256(source_path) != report["observed"]["source_sha256"]:
        print("Import blocked: source does not match the audited file.", file=sys.stderr)
        return EXIT_BLOCKED

    database_path = Path(args.database)
    database_path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(database_path)
    connection.row_factory = sqlite3.Row
    counts = {
        "accepted": 0,
        "rejected": 0,
        "transformed": 0,
        "duplicates": 0,
        "quarantined": 0,
    }
    dataset = report["dataset"]
    try:
        _create_schema(connection)
        _ensure_catalog_source_risk_columns(connection)
        connection.execute(
            """
            INSERT INTO catalog_sources
                (dataset_id, download_url, publisher, claimed_license, audit_report_json,
                 owner_accepted_risk, risk_acceptance_note)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT (dataset_id) DO UPDATE SET
                download_url = excluded.download_url,
                publisher = excluded.publisher,
                claimed_license = excluded.claimed_license,
                audit_report_json = excluded.audit_report_json,
                owner_accepted_risk = excluded.owner_accepted_risk,
                risk_acceptance_note = excluded.risk_acceptance_note
            """,
            (
                dataset["id"],
                dataset["download_url"],
                dataset["publisher"],
                dataset["claimed_license"],
                json.dumps(report, sort_keys=True),
                0,
                None,
            ),
        )
        with source_path.open("r", encoding="utf-8-sig", newline="") as source:
            seen_editions = {
                (row["brand"].casefold(), row["name"].casefold())
                for row in connection.execute(
                    """
                    SELECT f.brand, fe.name
                    FROM fragrance_editions AS fe
                    JOIN fragrances AS f ON f.id = fe.fragrance_id
                    """
                )
            }
            for source_row, row in enumerate(csv.DictReader(source), start=2):
                original_values = _json_safe_row(row)
                if None in row or any(value is None for value in row.values()):
                    _record_disposition(
                        connection,
                        dataset_id=dataset["id"],
                        source_row=source_row,
                        disposition="rejected",
                        reason="malformed columns",
                        original_values=original_values,
                    )
                    counts["rejected"] += 1
                    continue
                if dataset.get("expected_schema") == CURATED_REAL_CATALOG_SCHEMA:
                    _import_curated_catalog_row(
                        connection,
                        dataset_id=dataset["id"],
                        source_row=source_row,
                        row=row,
                        original_values=original_values,
                        seen_editions=seen_editions,
                        counts=counts,
                    )
                    continue
                original_name = row.get("Name") or ""
                fragrance_name = original_name.strip()
                brand = (row.get("Brand") or "").strip()
                notes = sorted(
                    {
                        note.strip().casefold()
                        for note in (row.get("Notes") or "").split(",")
                        if note.strip()
                    }
                )
                if not fragrance_name:
                    _record_disposition(
                        connection,
                        dataset_id=dataset["id"],
                        source_row=source_row,
                        disposition="rejected",
                        reason="missing Name",
                        original_values=original_values,
                    )
                    counts["rejected"] += 1
                    continue
                if not brand:
                    _record_disposition(
                        connection,
                        dataset_id=dataset["id"],
                        source_row=source_row,
                        disposition="rejected",
                        reason="missing Brand",
                        original_values=original_values,
                    )
                    counts["rejected"] += 1
                    continue
                if not notes:
                    _record_disposition(
                        connection,
                        dataset_id=dataset["id"],
                        source_row=source_row,
                        disposition="quarantined",
                        reason="missing Notes",
                        original_values=original_values,
                    )
                    counts["quarantined"] += 1
                    continue
                edition_key = (brand.casefold(), fragrance_name.casefold())
                if edition_key in seen_editions:
                    _record_disposition(
                        connection,
                        dataset_id=dataset["id"],
                        source_row=source_row,
                        disposition="duplicate",
                        reason="duplicate Fragrance Edition",
                        original_values=original_values,
                    )
                    counts["duplicates"] += 1
                    continue
                fragrance_id = _get_or_create_fragrance(
                    connection, fragrance_name=fragrance_name, brand=brand
                )
                edition_id = _get_or_create_edition(
                    connection,
                    fragrance_id=fragrance_id,
                    edition_name=fragrance_name,
                )
                connection.execute(
                    """
                    INSERT OR REPLACE INTO scent_profiles
                        (fragrance_edition_id, notes_json, main_accords_json,
                         top_notes_json, middle_notes_json, base_notes_json, scent_family)
                    VALUES (?, ?, NULL, NULL, NULL, NULL, NULL)
                    """,
                    (edition_id, json.dumps(notes)),
                )
                connection.execute(
                    """
                    INSERT INTO source_records
                        (dataset_id, source_row, fragrance_edition_id, original_values_json)
                    VALUES (?, ?, ?, ?)
                    """,
                    (
                        dataset["id"],
                        source_row,
                        edition_id,
                        json.dumps(original_values, sort_keys=True),
                    ),
                )
                counts["accepted"] += 1
                seen_editions.add(edition_key)
                if fragrance_name != original_name or notes != [row["Notes"]]:
                    counts["transformed"] += 1
        connection.commit()
    finally:
        connection.close()
    print(json.dumps(counts, sort_keys=True))
    return 0


def _curated_template(args: argparse.Namespace) -> int:
    writer = csv.writer(sys.stdout, lineterminator="\n")
    writer.writerow(CURATED_REAL_CATALOG_SCHEMA)
    print(
        "Curated Batch rows are drafts until curator_review_status is reviewed; "
        "keep draft batch CSV files outside the repository.",
        file=sys.stderr,
    )
    return 0


def _curated_preview(args: argparse.Namespace) -> int:
    source_path = Path(args.source)
    _validate_source_file(source_path)
    report: dict[str, Any] = {
        "status": "ok",
        "rows": {
            "total": 0,
            "ready": 0,
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
            "ready": 0,
            "too_weak": 0,
            "too_weak_rows": [],
        },
        "missing_note_groups": {
            "top": [],
            "middle": [],
            "base": [],
        },
        "coverage": {
            "scent_families": [],
            "repeated_brands": [],
            "common_notes": [],
        },
    }
    with source_path.open("r", encoding="utf-8-sig", newline="") as source:
        reader = csv.DictReader(source)
        if reader.fieldnames != CURATED_REAL_CATALOG_SCHEMA:
            raise ValueError("Curated Batch CSV schema does not match the curated template")
        seen_editions: set[tuple[str, str]] = set()
        for source_row, row in enumerate(reader, start=2):
            report["rows"]["total"] += 1
            _record_curated_preview_field_gaps(
                report, source_row=source_row, row=row
            )
            reason = _curated_preview_rejection_reason(row)
            if reason is not None:
                _record_curated_preview_rejection(
                    report, source_row=source_row, reason=reason
                )
                continue
            edition_key = (
                row["brand"].strip().casefold(),
                row["fragrance_edition_name"].strip().casefold(),
            )
            if edition_key in seen_editions:
                report["rows"]["duplicates"] += 1
                report["duplicates"].append(
                    {
                        "source_row": source_row,
                        "fragrance_edition": row["fragrance_edition_name"].strip(),
                        "brand": row["brand"].strip(),
                    }
                )
                continue
            seen_editions.add(edition_key)
            report["rows"]["ready"] += 1
            _record_curated_preview_coverage(
                report, source_row=source_row, row=row
            )
            _record_curated_preview_batch_1_quality(
                report, source_row=source_row, row=row
            )
    _finalize_curated_preview_coverage(report)
    print(json.dumps(report, indent=2))
    return 0


def _record_curated_preview_rejection(
    report: dict[str, Any], *, source_row: int, reason: str
) -> None:
    report["rows"]["rejected"] += 1
    report["rejections"].append({"source_row": source_row, "reason": reason})


def _record_curated_preview_field_gaps(
    report: dict[str, Any], *, source_row: int, row: dict[str | None, str]
) -> None:
    if not (row.get("identity_source_urls") or "").strip():
        report["missing_source_urls"]["identity"].append(source_row)
    if not (row.get("scent_profile_source_urls") or "").strip():
        report["missing_source_urls"]["scent_profile"].append(source_row)
    review_status = (row.get("curator_review_status") or "").strip()
    if not review_status:
        report["review_status"]["missing"].append(source_row)
    elif review_status.casefold() != "reviewed":
        report["review_status"]["not_reviewed"].append(source_row)


def _record_curated_preview_batch_1_quality(
    report: dict[str, Any], *, source_row: int, row: dict[str, str]
) -> None:
    known_groups, unknown_groups = _known_curated_scent_profile_groups(row)
    _record_curated_preview_missing_note_groups(
        report, source_row=source_row, row=row
    )
    if len(known_groups) >= 2:
        report["batch_1"]["ready"] += 1
        return

    report["batch_1"]["too_weak"] += 1
    report["batch_1"]["too_weak_rows"].append(
        {
            "source_row": source_row,
            "fragrance_edition": row["fragrance_edition_name"].strip(),
            "brand": row["brand"].strip(),
            "known_scent_profile_groups": known_groups,
            "unknown_scent_profile_groups": unknown_groups,
        }
    )


def _record_curated_preview_coverage(
    report: dict[str, Any], *, source_row: int, row: dict[str, str]
) -> None:
    brand = row.get("brand", "").strip()
    if brand:
        brand_rows = report["coverage"].setdefault("_brand_rows", {})
        brand_rows.setdefault(brand, []).append(source_row)

    scent_family = row.get("scent_family", "").strip().casefold()
    if scent_family:
        scent_family_rows = report["coverage"].setdefault("_scent_family_rows", {})
        scent_family_rows.setdefault(scent_family, []).append(source_row)

    note_rows = report["coverage"].setdefault("_note_rows", {})
    note_groups = report["coverage"].setdefault("_note_groups", {})
    for group_name, row_key in (
        ("top", "top_notes"),
        ("middle", "middle_notes"),
        ("base", "base_notes"),
    ):
        for note in _normalized_list(row.get(row_key, "")):
            note_rows.setdefault(note, set()).add(source_row)
            note_groups.setdefault(note, set()).add(group_name)


def _finalize_curated_preview_coverage(report: dict[str, Any]) -> None:
    scent_family_rows = report["coverage"].pop("_scent_family_rows", {})
    brand_rows = report["coverage"].pop("_brand_rows", {})
    note_rows = report["coverage"].pop("_note_rows", {})
    note_groups = report["coverage"].pop("_note_groups", {})
    report["coverage"]["scent_families"] = [
        {
            "scent_family": scent_family,
            "count": len(source_rows),
            "source_rows": source_rows,
        }
        for scent_family, source_rows in sorted(
            scent_family_rows.items(), key=lambda item: (-len(item[1]), item[0])
        )
    ]
    report["coverage"]["repeated_brands"] = [
        {
            "brand": brand,
            "count": len(source_rows),
            "source_rows": source_rows,
        }
        for brand, source_rows in sorted(
            brand_rows.items(), key=lambda item: (-len(item[1]), item[0].casefold())
        )
        if len(source_rows) > 1
    ]
    report["coverage"]["common_notes"] = [
        {
            "note": note,
            "count": len(source_rows),
            "source_rows": sorted(source_rows),
            "note_groups": sorted(note_groups[note]),
        }
        for note, source_rows in sorted(
            note_rows.items(), key=lambda item: (-len(item[1]), item[0])
        )
        if len(source_rows) > 1
    ]


def _known_curated_scent_profile_groups(
    row: dict[str, str]
) -> tuple[list[str], list[str]]:
    groups = {
        "note_pyramid": any(
            _normalized_list(row.get(note_group, ""))
            for note_group in ("top_notes", "middle_notes", "base_notes")
        ),
        "main_accords": bool(_normalized_list(row.get("main_accords", ""))),
        "scent_family": bool(row.get("scent_family", "").strip()),
    }
    known = [group for group, is_known in groups.items() if is_known]
    unknown = [group for group, is_known in groups.items() if not is_known]
    return known, unknown


def _record_curated_preview_missing_note_groups(
    report: dict[str, Any], *, source_row: int, row: dict[str, str]
) -> None:
    note_groups = {
        "top": "top_notes",
        "middle": "middle_notes",
        "base": "base_notes",
    }
    for report_key, row_key in note_groups.items():
        if not _normalized_list(row.get(row_key, "")):
            report["missing_note_groups"][report_key].append(source_row)


def _curated_preview_rejection_reason(row: dict[str | None, str]) -> str | None:
    if None in row or any(value is None for value in row.values()):
        return "malformed columns"
    if not row.get("fragrance_name", "").strip():
        return "missing fragrance_name"
    if not row.get("fragrance_edition_name", "").strip():
        return "missing fragrance_edition_name"
    if not row.get("brand", "").strip():
        return "missing brand"
    if not row.get("identity_source_urls", "").strip():
        return "missing identity_source_urls"
    if not row.get("scent_profile_source_urls", "").strip():
        return "missing scent_profile_source_urls"
    review_status = row.get("curator_review_status", "").strip()
    if not review_status:
        return "missing curator_review_status"
    if review_status.casefold() != "reviewed":
        return "curator review status is not reviewed"
    main_accords = _normalized_list(row.get("main_accords", ""))
    top_notes = _normalized_list(row.get("top_notes", ""))
    middle_notes = _normalized_list(row.get("middle_notes", ""))
    base_notes = _normalized_list(row.get("base_notes", ""))
    scent_family = row.get("scent_family", "").strip()
    if not any([main_accords, top_notes, middle_notes, base_notes, scent_family]):
        return "missing Scent Profile facts"
    return None


def _import_curated_catalog_row(
    connection: sqlite3.Connection,
    *,
    dataset_id: str,
    source_row: int,
    row: dict[str, str],
    original_values: dict[str, Any],
    seen_editions: set[tuple[str, str]],
    counts: dict[str, int],
) -> None:
    fragrance_name = row.get("fragrance_name", "").strip()
    edition_name = row.get("fragrance_edition_name", "").strip()
    brand = row.get("brand", "").strip()
    concentration = row.get("concentration", "").strip() or None
    if not fragrance_name:
        _record_disposition(
            connection,
            dataset_id=dataset_id,
            source_row=source_row,
            disposition="rejected",
            reason="missing fragrance_name",
            original_values=original_values,
        )
        counts["rejected"] += 1
        return
    if not edition_name:
        _record_disposition(
            connection,
            dataset_id=dataset_id,
            source_row=source_row,
            disposition="rejected",
            reason="missing fragrance_edition_name",
            original_values=original_values,
        )
        counts["rejected"] += 1
        return
    if not brand:
        _record_disposition(
            connection,
            dataset_id=dataset_id,
            source_row=source_row,
            disposition="rejected",
            reason="missing brand",
            original_values=original_values,
        )
        counts["rejected"] += 1
        return

    if row.get("curator_review_status", "").strip().casefold() != "reviewed":
        _record_disposition(
            connection,
            dataset_id=dataset_id,
            source_row=source_row,
            disposition="rejected",
            reason="curator review status is not reviewed",
            original_values=original_values,
        )
        counts["rejected"] += 1
        return
    if not row.get("identity_source_urls", "").strip():
        _record_disposition(
            connection,
            dataset_id=dataset_id,
            source_row=source_row,
            disposition="rejected",
            reason="missing identity_source_urls",
            original_values=original_values,
        )
        counts["rejected"] += 1
        return
    if not row.get("scent_profile_source_urls", "").strip():
        _record_disposition(
            connection,
            dataset_id=dataset_id,
            source_row=source_row,
            disposition="rejected",
            reason="missing scent_profile_source_urls",
            original_values=original_values,
        )
        counts["rejected"] += 1
        return

    main_accords = _normalized_list(row.get("main_accords", ""))
    top_notes = _normalized_list(row.get("top_notes", ""))
    middle_notes = _normalized_list(row.get("middle_notes", ""))
    base_notes = _normalized_list(row.get("base_notes", ""))
    scent_family = row.get("scent_family", "").strip().casefold() or None
    notes = sorted({*top_notes, *middle_notes, *base_notes})
    if not any([main_accords, notes, scent_family]):
        _record_disposition(
            connection,
            dataset_id=dataset_id,
            source_row=source_row,
            disposition="quarantined",
            reason="missing Scent Profile facts",
            original_values=original_values,
        )
        counts["quarantined"] += 1
        return

    edition_key = (brand.casefold(), edition_name.casefold())
    if edition_key in seen_editions:
        _record_disposition(
            connection,
            dataset_id=dataset_id,
            source_row=source_row,
            disposition="duplicate",
            reason="duplicate Fragrance Edition",
            original_values=original_values,
        )
        counts["duplicates"] += 1
        return

    fragrance_id = _get_or_create_fragrance(
        connection, fragrance_name=fragrance_name, brand=brand
    )
    edition_id = _get_or_create_edition(
        connection,
        fragrance_id=fragrance_id,
        edition_name=edition_name,
        concentration=concentration,
    )
    connection.execute(
        """
        INSERT OR REPLACE INTO scent_profiles
            (fragrance_edition_id, notes_json, main_accords_json,
             top_notes_json, middle_notes_json, base_notes_json, scent_family)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (
            edition_id,
            json.dumps(notes),
            _json_or_null(main_accords),
            _json_or_null(top_notes),
            _json_or_null(middle_notes),
            _json_or_null(base_notes),
            scent_family,
        ),
    )
    connection.execute(
        """
        INSERT INTO source_records
            (dataset_id, source_row, fragrance_edition_id, original_values_json)
        VALUES (?, ?, ?, ?)
        """,
        (
            dataset_id,
            source_row,
            edition_id,
            json.dumps(original_values, sort_keys=True),
        ),
    )
    counts["accepted"] += 1
    counts["transformed"] += 1
    seen_editions.add(edition_key)


def _normalized_list(value: str) -> list[str]:
    return sorted(
        {
            item.strip().casefold()
            for item in value.split(",")
            if item.strip()
        }
    )


def _json_or_null(values: list[str]) -> str | None:
    return json.dumps(values) if values else None


def _report_passes(report: dict[str, Any]) -> bool:
    dataset = report.get("dataset", {})
    checks = report.get("checks", {})
    return (
        report.get("verdict") == "passed"
        and set(checks) == REQUIRED_AUDIT_CHECKS
        and all(checks.values())
        and dataset.get("license_chain_status") == "passed"
        and bool(dataset.get("license_evidence"))
        and dataset.get("provenance_status") == "passed"
        and bool(dataset.get("provenance_evidence"))
        and dataset.get("quality_status") == "passed"
    )


def _create_schema(connection: sqlite3.Connection) -> None:
    connection.executescript(
        """
        PRAGMA foreign_keys = ON;
        CREATE TABLE IF NOT EXISTS catalog_sources (
            dataset_id TEXT PRIMARY KEY,
            download_url TEXT NOT NULL,
            publisher TEXT NOT NULL,
            claimed_license TEXT NOT NULL,
            audit_report_json TEXT NOT NULL,
            owner_accepted_risk INTEGER NOT NULL DEFAULT 0,
            risk_acceptance_note TEXT
        );
        CREATE TABLE IF NOT EXISTS fragrances (
            id INTEGER PRIMARY KEY,
            name TEXT NOT NULL,
            brand TEXT NOT NULL,
            UNIQUE (name, brand)
        );
        CREATE TABLE IF NOT EXISTS fragrance_editions (
            id INTEGER PRIMARY KEY,
            fragrance_id INTEGER NOT NULL REFERENCES fragrances(id),
            name TEXT NOT NULL,
            concentration TEXT,
            catalog_kind TEXT NOT NULL CHECK (catalog_kind IN ('real', 'scale-test')),
            UNIQUE (fragrance_id, name)
        );
        CREATE TABLE IF NOT EXISTS scent_profiles (
            fragrance_edition_id INTEGER PRIMARY KEY REFERENCES fragrance_editions(id),
            notes_json TEXT NOT NULL,
            main_accords_json TEXT,
            top_notes_json TEXT,
            middle_notes_json TEXT,
            base_notes_json TEXT,
            scent_family TEXT
        );
        CREATE TABLE IF NOT EXISTS scent_profile_embeddings (
            fragrance_edition_id INTEGER PRIMARY KEY REFERENCES fragrance_editions(id),
            model TEXT NOT NULL,
            model_version TEXT NOT NULL,
            pipeline_version TEXT NOT NULL,
            dimensions INTEGER NOT NULL,
            runtime_device TEXT NOT NULL,
            vector_json TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS comparable_prices (
            fragrance_edition_id INTEGER PRIMARY KEY REFERENCES fragrance_editions(id),
            amount_usd REAL,
            currency TEXT NOT NULL DEFAULT 'USD',
            market TEXT NOT NULL DEFAULT 'US',
            bottle_size_ml REAL NOT NULL,
            observed_on TEXT,
            source_name TEXT,
            source_url TEXT
        );
        CREATE TABLE IF NOT EXISTS wear_profiles (
            fragrance_edition_id INTEGER PRIMARY KEY REFERENCES fragrance_editions(id),
            longevity TEXT,
            projection TEXT
        );
        CREATE TABLE IF NOT EXISTS source_records (
            dataset_id TEXT NOT NULL REFERENCES catalog_sources(dataset_id),
            source_row INTEGER NOT NULL,
            fragrance_edition_id INTEGER NOT NULL REFERENCES fragrance_editions(id),
            original_values_json TEXT NOT NULL,
            PRIMARY KEY (dataset_id, source_row)
        );
        CREATE TABLE IF NOT EXISTS import_dispositions (
            dataset_id TEXT NOT NULL REFERENCES catalog_sources(dataset_id),
            source_row INTEGER NOT NULL,
            disposition TEXT NOT NULL CHECK (
                disposition IN ('rejected', 'quarantined', 'duplicate')
            ),
            reason TEXT NOT NULL,
            original_values_json TEXT NOT NULL,
            PRIMARY KEY (dataset_id, source_row)
        );
        """
    )


def _ensure_catalog_source_risk_columns(connection: sqlite3.Connection) -> None:
    existing_columns = {
        row["name"]
        for row in connection.execute("PRAGMA table_info(catalog_sources)").fetchall()
    }
    if "owner_accepted_risk" not in existing_columns:
        connection.execute(
            """
            ALTER TABLE catalog_sources
            ADD COLUMN owner_accepted_risk INTEGER NOT NULL DEFAULT 0
            """
        )
    if "risk_acceptance_note" not in existing_columns:
        connection.execute(
            """
            ALTER TABLE catalog_sources
            ADD COLUMN risk_acceptance_note TEXT
            """
        )


def _record_disposition(
    connection: sqlite3.Connection,
    *,
    dataset_id: str,
    source_row: int,
    disposition: str,
    reason: str,
    original_values: dict[str, Any],
) -> None:
    connection.execute(
        """
        INSERT OR REPLACE INTO import_dispositions
            (dataset_id, source_row, disposition, reason, original_values_json)
        VALUES (?, ?, ?, ?, ?)
        """,
        (
            dataset_id,
            source_row,
            disposition,
            reason,
            json.dumps(original_values, sort_keys=True),
        ),
    )


def _json_safe_row(row: dict[str | None, Any]) -> dict[str, Any]:
    return {
        (key if key is not None else "_extra_columns"): value
        for key, value in row.items()
    }


def _get_or_create_fragrance(
    connection: sqlite3.Connection, *, fragrance_name: str, brand: str
) -> int:
    connection.execute(
        "INSERT OR IGNORE INTO fragrances (name, brand) VALUES (?, ?)",
        (fragrance_name, brand),
    )
    row = connection.execute(
        "SELECT id FROM fragrances WHERE name = ? AND brand = ?",
        (fragrance_name, brand),
    ).fetchone()
    return int(row["id"])


def _get_or_create_edition(
    connection: sqlite3.Connection,
    *,
    fragrance_id: int,
    edition_name: str,
    concentration: str | None = None,
) -> int:
    connection.execute(
        """
        INSERT OR IGNORE INTO fragrance_editions
            (fragrance_id, name, concentration, catalog_kind)
        VALUES (?, ?, NULL, 'real')
        """,
        (fragrance_id, edition_name),
    )
    if concentration is not None:
        connection.execute(
            """
            UPDATE fragrance_editions
            SET concentration = ?
            WHERE fragrance_id = ? AND name = ? AND concentration IS NULL
            """,
            (concentration, fragrance_id, edition_name),
        )
    row = connection.execute(
        "SELECT id FROM fragrance_editions WHERE fragrance_id = ? AND name = ?",
        (fragrance_id, edition_name),
    ).fetchone()
    return int(row["id"])


def _inspect(args: argparse.Namespace) -> int:
    connection = sqlite3.connect(Path(args.database))
    connection.row_factory = sqlite3.Row
    try:
        rows = connection.execute(
            """
            SELECT f.brand, f.name AS fragrance, fe.name AS edition, fe.concentration,
                   sp.notes_json, sr.dataset_id, sr.source_row, sr.original_values_json
            FROM fragrance_editions AS fe
            JOIN fragrances AS f ON f.id = fe.fragrance_id
            JOIN scent_profiles AS sp ON sp.fragrance_edition_id = fe.id
            JOIN source_records AS sr ON sr.fragrance_edition_id = fe.id
            WHERE fe.catalog_kind = 'real'
            ORDER BY f.brand, f.name, fe.name
            """
        ).fetchall()
    finally:
        connection.close()
    catalog = []
    for row in rows:
        original = json.loads(row["original_values_json"])
        record = {
            "brand": row["brand"],
            "fragrance": row["fragrance"],
            "edition": row["edition"],
            "concentration": row["concentration"],
            "notes": json.loads(row["notes_json"]),
            "source_dataset": row["dataset_id"],
            "source_row": row["source_row"],
            "original_name": original.get("Name", original.get("fragrance_name")),
        }
        if "identity_source_urls" in original or "scent_profile_source_urls" in original:
            record["source_urls"] = {
                "identity": _source_urls(original.get("identity_source_urls", "")),
                "scent_profile": _source_urls(
                    original.get("scent_profile_source_urls", "")
                ),
            }
        catalog.append(record)
    print(json.dumps(catalog, indent=2))
    return 0


def _source_urls(value: str) -> list[str]:
    return [url.strip() for url in value.split(",") if url.strip()]


def _inspect_quarantine(args: argparse.Namespace) -> int:
    connection = sqlite3.connect(Path(args.database))
    connection.row_factory = sqlite3.Row
    try:
        rows = connection.execute(
            """
            SELECT source_row, disposition, reason
            FROM import_dispositions
            WHERE disposition IN ('rejected', 'quarantined')
            ORDER BY source_row
            """
        ).fetchall()
    finally:
        connection.close()
    print(json.dumps([dict(row) for row in rows], indent=2))
    return 0


def _catalog_unavailable(database: Path) -> int:
    print(
        "Catalog unavailable: import a Real Catalog into SQLite first, "
        f"then pass it with --database {database}.",
        file=sys.stderr,
    )
    return EXIT_BLOCKED


def _browse(args: argparse.Namespace) -> int:
    try:
        print(json.dumps(_browse_response(args), indent=2))
    except ValueError as error:
        if str(error).startswith("Catalog unavailable:"):
            return _catalog_unavailable(Path(args.database))
        raise
    return 0


def _browse_response(args: argparse.Namespace) -> dict[str, Any]:
    query = args.query.strip()
    database = Path(args.database)
    if not database.exists():
        raise ValueError(
            "Catalog unavailable: import a Real Catalog into SQLite first."
        )
    per_page = getattr(args, "per_page", None)
    page = getattr(args, "page", 1)
    if per_page is not None:
        if per_page < 1:
            raise ValueError("--per-page must be at least 1")
        if page < 1:
            raise ValueError("--page must be at least 1")
    connection = sqlite3.connect(database)
    connection.row_factory = sqlite3.Row
    try:
        try:
            total = connection.execute(
                """
                SELECT COUNT(*)
                FROM fragrance_editions AS fe
                JOIN fragrances AS f ON f.id = fe.fragrance_id
                JOIN scent_profiles AS sp ON sp.fragrance_edition_id = fe.id
                WHERE fe.catalog_kind = 'real'
                  AND f.name LIKE ? COLLATE NOCASE
                """,
                (f"%{query}%",),
            ).fetchone()[0]
            sql = """
            SELECT fe.id AS fragrance_edition_id, f.name AS fragrance,
                   fe.name AS edition, fe.concentration
            FROM fragrance_editions AS fe
            JOIN fragrances AS f ON f.id = fe.fragrance_id
            JOIN scent_profiles AS sp ON sp.fragrance_edition_id = fe.id
            WHERE fe.catalog_kind = 'real'
              AND f.name LIKE ? COLLATE NOCASE
            ORDER BY f.name, fe.id
            """
            parameters: list[Any] = [f"%{query}%"]
            if per_page is not None:
                sql += "\nLIMIT ? OFFSET ?"
                parameters.extend([per_page, (page - 1) * per_page])
            rows = connection.execute(sql, parameters).fetchall()
        except sqlite3.OperationalError:
            raise ValueError(
                "Catalog unavailable: import a Real Catalog into SQLite first."
            )
    finally:
        connection.close()
    results = [dict(row) for row in rows]
    response: dict[str, Any] = {
        "status": "ok" if results else "no_matches",
        "query": query,
    }
    if per_page is not None:
        response["pagination"] = {
            "page": page,
            "per_page": per_page,
            "total_results": total,
            "total_pages": max(1, math.ceil(total / per_page)),
            "has_previous": page > 1,
            "has_next": page * per_page < total,
        }
    if not results:
        response["message"] = (
            "No Real Catalog Fragrance Editions matched that Fragrance name."
        )
    response["results"] = results
    return response


def _json_or_unknown(value: str | None) -> list[str] | str:
    if value is None:
        return "unknown"
    parsed = json.loads(value)
    return parsed if parsed else "unknown"


def _value_or_unknown(value: str | None) -> str:
    return value if value else "unknown"


def _scent_profile(args: argparse.Namespace) -> int:
    try:
        print(json.dumps(_scent_profile_response(args), indent=2))
    except ValueError as error:
        if str(error).startswith("Catalog unavailable:"):
            return _catalog_unavailable(Path(args.database))
        raise
    return 0


def _scent_profile_response(args: argparse.Namespace) -> dict[str, Any]:
    database = Path(args.database)
    if not database.exists():
        raise ValueError(
            "Catalog unavailable: import a Real Catalog into SQLite first."
        )
    connection = sqlite3.connect(database)
    connection.row_factory = sqlite3.Row
    try:
        try:
            row = connection.execute(
                """
                SELECT fe.id AS fragrance_edition_id, f.name AS fragrance,
                       fe.name AS edition, fe.concentration,
                       sp.main_accords_json, sp.top_notes_json,
                       sp.middle_notes_json, sp.base_notes_json, sp.scent_family
                FROM fragrance_editions AS fe
                JOIN fragrances AS f ON f.id = fe.fragrance_id
                JOIN scent_profiles AS sp ON sp.fragrance_edition_id = fe.id
                WHERE fe.catalog_kind = 'real'
                  AND fe.id = ?
                """,
                (args.edition_id,),
            ).fetchone()
        except sqlite3.OperationalError:
            raise ValueError(
                "Catalog unavailable: import a Real Catalog into SQLite first."
            )
    finally:
        connection.close()
    if row is None:
        return {
            "status": "not_found",
            "message": "No Real Catalog Fragrance Edition is available for that edition id.",
        }
    return {
        "status": "ok",
        "fragrance_edition_id": row["fragrance_edition_id"],
        "fragrance": row["fragrance"],
        "edition": row["edition"],
        "concentration": row["concentration"],
        "scent_profile": {
            "main_accords": _json_or_unknown(row["main_accords_json"]),
            "note_pyramid": {
                "top": _json_or_unknown(row["top_notes_json"]),
                "middle": _json_or_unknown(row["middle_notes_json"]),
                "base": _json_or_unknown(row["base_notes_json"]),
            },
            "scent_family": _value_or_unknown(row["scent_family"]),
        },
    }


def _scent_matches(args: argparse.Namespace) -> int:
    database = Path(args.database)
    if not database.exists():
        return _catalog_unavailable(database)
    connection = sqlite3.connect(database)
    connection.row_factory = sqlite3.Row
    try:
        _create_embedding_schema(connection)
        _create_comparable_price_schema(connection)
        _create_wear_profile_schema(connection)
        rows = connection.execute(
            """
            SELECT fe.id AS fragrance_edition_id, f.name AS fragrance,
                   fe.name AS edition, fe.concentration, fe.catalog_kind,
                   sp.notes_json, sp.main_accords_json, sp.top_notes_json,
                   sp.middle_notes_json, sp.base_notes_json, sp.scent_family,
                   cp.amount_usd, cp.currency, cp.market, cp.bottle_size_ml,
                   cp.observed_on, cp.source_name, cp.source_url,
                   wp.longevity, wp.projection
            FROM fragrance_editions AS fe
            JOIN fragrances AS f ON f.id = fe.fragrance_id
            JOIN scent_profiles AS sp ON sp.fragrance_edition_id = fe.id
            LEFT JOIN comparable_prices AS cp ON cp.fragrance_edition_id = fe.id
            LEFT JOIN wear_profiles AS wp ON wp.fragrance_edition_id = fe.id
            WHERE fe.catalog_kind = 'real'
            ORDER BY fe.id
            """
        ).fetchall()
    except sqlite3.OperationalError:
        connection.close()
        return _catalog_unavailable(database)

    reference = next(
        (row for row in rows if row["fragrance_edition_id"] == args.edition_id),
        None,
    )
    if reference is None:
        print(
            json.dumps(
                {
                    "status": "not_found",
                    "message": "No Real Catalog Fragrance Edition is available for that edition id.",
                },
                indent=2,
            )
        )
        connection.close()
        return 0

    reference_vector = _embed_scent_profile(reference)
    _record_embedding(connection, reference, reference_vector)
    exact_ranked_matches = []
    for row in rows:
        if row["fragrance_edition_id"] == reference["fragrance_edition_id"]:
            continue
        candidate_vector = _embed_scent_profile(row)
        _record_embedding(connection, row, candidate_vector)
        score = _cosine_similarity(reference_vector, candidate_vector)
        exact_ranked_matches.append(
            _scent_match_result(
                reference,
                row,
                score,
                method="exact_cosine",
                include_price=args.cheaper_only or args.show_prices,
                include_wear_profile=_include_wear_profile(args),
            )
        )
    exact_ranked_matches.sort(
        key=lambda result: (
            -result["scent_match"]["model_specific_score"],
            result["fragrance"],
            result["edition"],
            result["fragrance_edition_id"],
        )
    )

    retrieval: dict[str, Any] | None = None
    if args.index:
        index_path = Path(args.index)
        freshness = _qdrant_index_freshness(index_path, rows)
        if freshness["status"] != "fresh":
            connection.commit()
            connection.close()
            print(
                json.dumps(
                    {
                        "status": "index_unavailable",
                        "message": "Rebuild the Qdrant index from SQLite before serving ANN Scent Matches.",
                        "qdrant_index": freshness,
                    },
                    indent=2,
                )
            )
            return 0
        rows_by_id = {row["fragrance_edition_id"]: row for row in rows}
        ann_ranked_matches = []
        for point in json.loads(index_path.read_text(encoding="utf-8"))["points"]:
            payload = point.get("payload", {})
            edition_id = payload.get("fragrance_edition_id")
            if edition_id == reference["fragrance_edition_id"]:
                continue
            if (
                point.get("id") != edition_id
                or payload.get("catalog_kind") != "real"
                or edition_id not in rows_by_id
            ):
                continue
            score = _cosine_similarity(reference_vector, point["vector"])
            ann_ranked_matches.append(
                _scent_match_result(
                    reference,
                    rows_by_id[edition_id],
                    score,
                    method="qdrant_ann",
                    include_price=args.cheaper_only or args.show_prices,
                    include_wear_profile=_include_wear_profile(args),
                )
            )
        ann_ranked_matches.sort(
            key=lambda result: (
                -result["scent_match"]["model_specific_score"],
                result["fragrance"],
                result["edition"],
                result["fragrance_edition_id"],
            )
        )
        exact_ids = {
            result["fragrance_edition_id"] for result in exact_ranked_matches[: args.limit]
        }
        ann_ids = {
            result["fragrance_edition_id"] for result in ann_ranked_matches[: args.limit]
        }
        recall = 1.0 if not exact_ids else round(len(exact_ids & ann_ids) / len(exact_ids), 2)
        ranked_matches = ann_ranked_matches
        retrieval = {
            "method": "qdrant_ann",
            "index_status": "fresh",
            "exact_baseline_method": "exact_cosine",
            "recall_at_k": recall,
            "embedding_latency_ms": 0,
            "retrieval_latency_ms": 0,
            "hydration_latency_ms": 0,
        }
    else:
        ranked_matches = exact_ranked_matches

    if args.cheaper_only:
        ranked_matches = [
            result
            for result in ranked_matches
            if result.get("price_comparison", {}).get("strictly_cheaper") is True
        ]
    if args.wear_longevity or args.wear_projection:
        ranked_matches = [
            result
            for result in ranked_matches
            if _matches_wear_profile_filters(
                result,
                longevity=args.wear_longevity,
                projection=args.wear_projection,
            )
        ]
    limited_matches = ranked_matches[: args.limit]
    response: dict[str, Any] = {
        "status": "ok" if limited_matches else "no_matches",
        "reference": {
            "fragrance_edition_id": reference["fragrance_edition_id"],
            "fragrance": reference["fragrance"],
            "edition": reference["edition"],
            "concentration": reference["concentration"],
        },
        "embedding": _embedding_metadata(),
        "results": limited_matches,
    }
    if retrieval is not None:
        response["retrieval"] = retrieval
    if args.cheaper_only or args.show_prices:
        response["reference"]["comparable_price"] = _comparable_price(reference)
    if _include_wear_profile(args):
        response["reference"]["wear_profile"] = _wear_profile(reference)
    if not limited_matches:
        if args.cheaper_only:
            response["message"] = (
                "No Real Catalog Scent Matches have known same-size Comparable Prices "
                "below the selected Fragrance Edition."
            )
        elif args.wear_longevity or args.wear_projection:
            response["message"] = (
                "No Real Catalog Scent Matches have Wear Profile facts matching those filters."
            )
        else:
            response["message"] = (
                "No other Real Catalog Fragrance Editions are available for exact cosine Scent Matches."
            )
    connection.commit()
    connection.close()
    print(json.dumps(response, indent=2))
    return 0


def _scent_request(args: argparse.Namespace) -> int:
    database = Path(args.database)
    if not database.exists():
        return _catalog_unavailable(database)
    connection = sqlite3.connect(database)
    connection.row_factory = sqlite3.Row
    try:
        try:
            rows = _real_catalog_scent_profile_rows(connection)
        except sqlite3.OperationalError:
            return _catalog_unavailable(database)
    finally:
        connection.close()

    wanted_text = (
        args.revise_wanted if args.revise_wanted is not None else args.wanted
    )
    unwanted_text = (
        args.revise_unwanted if args.revise_unwanted is not None else args.unwanted
    )
    interpretation = _interpret_scent_request(
        wanted_text or "",
        unwanted_text or "",
        rows,
    )
    if args.cancel:
        print(
            json.dumps(
                {
                    "status": "canceled",
                    "scent_request": {
                        "wanted": wanted_text or "",
                        "unwanted": unwanted_text or "",
                    },
                    "message": (
                        "Scent Request canceled before searching; "
                        "the catalog was not changed."
                    ),
                },
                indent=2,
            )
        )
        return 0
    if args.confirm:
        response = _confirmed_scent_request_response(
            wanted_text or "",
            unwanted_text or "",
            interpretation,
            rows,
            limit=args.limit,
        )
        print(json.dumps(response, indent=2))
        return 0
    response = {
        "status": "needs_confirmation",
        "scent_request": {
            "wanted": wanted_text or "",
            "unwanted": unwanted_text or "",
        },
        "interpretation": interpretation,
        "next_actions": {
            "confirm": "Run again with --confirm to search from this interpreted Scent Request.",
            "revise": "Run again with --revise-wanted or --revise-unwanted to inspect a revised interpretation before searching.",
            "cancel": "Run again with --cancel to stop without searching.",
        },
    }
    print(json.dumps(response, indent=2))
    return 0


def _confirmed_scent_request_response(
    wanted_text: str,
    unwanted_text: str,
    interpretation: dict[str, Any],
    rows: list[sqlite3.Row],
    *,
    limit: int,
) -> dict[str, Any]:
    if not _request_terms(wanted_text):
        return {
            "status": "empty",
            "scent_request": {
                "wanted": wanted_text,
                "unwanted": unwanted_text,
            },
            "interpretation": interpretation,
            "message": "Enter at least one wanted Scent Profile trait before searching.",
            "results": [],
        }
    if _request_has_no_known_wanted_traits(interpretation):
        return {
            "status": "unsupported",
            "scent_request": {
                "wanted": wanted_text,
                "unwanted": unwanted_text,
            },
            "interpretation": interpretation,
            "message": (
                "No supported wanted Scent Profile traits were found; "
                "NosePrint will not invent catalog facts."
            ),
            "results": [],
        }
    reference = _scent_request_reference(interpretation)
    reference_vector = _embed_interpreted_scent_request(interpretation)
    ranked_matches = []
    excluded_ids = []
    for row in rows:
        if _row_has_unwanted_traits(row, interpretation["unwanted_traits"]):
            excluded_ids.append(row["fragrance_edition_id"])
            continue
        candidate_vector = _embed_scent_profile(row)
        ranked_matches.append(
            _scent_match_result(
                reference,
                row,
                _cosine_similarity(reference_vector, candidate_vector),
                method="exact_cosine",
            )
        )
    ranked_matches.sort(
        key=lambda result: (
            -result["scent_match"]["model_specific_score"],
            result["fragrance"],
            result["edition"],
            result["fragrance_edition_id"],
        )
    )
    limited_matches = ranked_matches[:limit]
    response: dict[str, Any] = {
        "status": "ok" if limited_matches else "no_matches",
        "scent_request": {
            "wanted": wanted_text,
            "unwanted": unwanted_text,
        },
        "reference": {
            "source": "ephemeral_scent_request",
            "scent_profile": {
                "main_accords": interpretation["wanted_traits"]["main_accords"]
                or "unknown",
                "note_pyramid": {
                    "top": "unknown",
                    "middle": "unknown",
                    "base": "unknown",
                },
                "scent_family": interpretation["wanted_traits"]["scent_family"],
            },
        },
        "interpretation": interpretation,
        "embedding": _embedding_metadata(),
        "unwanted_trait_filter": {
            "mode": "exclude_known_matches",
            "excluded_traits": interpretation["unwanted_traits"],
            "excluded_fragrance_edition_ids": sorted(excluded_ids),
            "filter_notice": (
                "Known unwanted Scent Profile traits are filtered from results "
                "without changing the catalog."
            ),
        },
        "results": limited_matches,
    }
    if not limited_matches:
        response["message"] = (
            "No Real Catalog Scent Matches remain after applying the interpreted Scent Request."
        )
    return response


def _request_has_no_known_wanted_traits(interpretation: dict[str, Any]) -> bool:
    wanted_traits = interpretation["wanted_traits"]
    return (
        not wanted_traits["notes"]
        and not wanted_traits["main_accords"]
        and wanted_traits["scent_family"] == "unknown"
    )


def _scent_request_reference(interpretation: dict[str, Any]) -> dict[str, Any]:
    wanted_traits = interpretation["wanted_traits"]
    scent_family = wanted_traits["scent_family"]
    return {
        "fragrance_edition_id": None,
        "fragrance": "Scent Request",
        "edition": "Ephemeral Scent Request",
        "concentration": None,
        "catalog_kind": "scent-request",
        "notes_json": json.dumps(wanted_traits["notes"]),
        "main_accords_json": json.dumps(wanted_traits["main_accords"]),
        "top_notes_json": None,
        "middle_notes_json": None,
        "base_notes_json": None,
        "scent_family": None if scent_family == "unknown" else scent_family,
    }


def _embed_interpreted_scent_request(interpretation: dict[str, Any]) -> list[float]:
    vector = [0.0] * EMBEDDING_DIMENSIONS
    wanted_traits = interpretation["wanted_traits"]
    tokens = [
        *[f"notes:{value}" for value in wanted_traits["notes"]],
        *[f"main_accord:{value}" for value in wanted_traits["main_accords"]],
    ]
    if wanted_traits["scent_family"] != "unknown":
        tokens.append(f"scent_family:{wanted_traits['scent_family']}")
    for token in tokens:
        vector[_embedding_bucket(token)] += 1.0
    return vector


def _row_has_unwanted_traits(
    row: sqlite3.Row,
    unwanted_traits: dict[str, list[str] | str],
) -> bool:
    row_notes = _known_json_profile_facts(row["notes_json"]) or set()
    row_accords = _known_json_profile_facts(row["main_accords_json"]) or set()
    row_family = _known_scent_family(row["scent_family"])
    if row_notes & set(unwanted_traits["notes"]):
        return True
    if row_accords & set(unwanted_traits["main_accords"]):
        return True
    unwanted_family = unwanted_traits["scent_family"]
    return (
        unwanted_family != "unknown"
        and row_family is not None
        and row_family == unwanted_family
    )


def _interpret_scent_request(
    wanted_text: str,
    unwanted_text: str,
    rows: list[sqlite3.Row],
) -> dict[str, Any]:
    vocabulary = _scent_profile_vocabulary(rows)
    wanted_terms = _request_terms(wanted_text)
    unwanted_terms = _request_terms(unwanted_text)
    supported_terms = set().union(*vocabulary.values())
    unsupported_terms = sorted((wanted_terms | unwanted_terms) - supported_terms)
    return {
        "wanted_traits": _interpreted_traits(wanted_terms, vocabulary),
        "unwanted_traits": _interpreted_traits(unwanted_terms, vocabulary),
        "unsupported_terms": unsupported_terms,
        "ambiguous_terms": _ambiguous_request_terms(
            wanted_terms | unwanted_terms, vocabulary
        ),
        "interpretation_notice": (
            "Scent Request interpretation uses known Real Catalog Scent Profile "
            "vocabulary only; unsupported terms are not guessed."
        ),
    }


def _scent_profile_vocabulary(rows: list[sqlite3.Row]) -> dict[str, set[str]]:
    vocabulary: dict[str, set[str]] = {
        "notes": set(),
        "main_accords": set(),
        "scent_family": set(),
    }
    for row in rows:
        for value in _known_json_profile_facts(row["notes_json"]) or set():
            vocabulary["notes"].add(value)
        for value in _known_json_profile_facts(row["main_accords_json"]) or set():
            vocabulary["main_accords"].add(value)
        family = _known_scent_family(row["scent_family"])
        if family is not None:
            vocabulary["scent_family"].add(family)
    return vocabulary


def _request_terms(text: str) -> set[str]:
    return {
        term
        for term in re.findall(r"[a-z0-9]+(?:-[a-z0-9]+)?", text.casefold())
        if term
    }


def _interpreted_traits(
    terms: set[str], vocabulary: dict[str, set[str]]
) -> dict[str, list[str] | str]:
    scent_families = sorted(terms & vocabulary["scent_family"])
    return {
        "notes": sorted(terms & vocabulary["notes"]),
        "main_accords": sorted(terms & vocabulary["main_accords"]),
        "scent_family": scent_families[0] if scent_families else "unknown",
    }


def _ambiguous_request_terms(
    terms: set[str], vocabulary: dict[str, set[str]]
) -> dict[str, list[str]]:
    ambiguous = {}
    for term in sorted(terms):
        categories = [
            category
            for category in ("notes", "main_accords", "scent_family")
            if term in vocabulary[category]
        ]
        if len(categories) > 1:
            ambiguous[term] = categories
    return ambiguous


def _scent_match_result(
    reference: sqlite3.Row,
    row: sqlite3.Row,
    score: float,
    *,
    method: str,
    include_price: bool = False,
    include_wear_profile: bool = False,
) -> dict[str, Any]:
    if method == "exact_cosine":
        score_basis = (
            "Exact cosine over NosePrint Scent Profile embeddings; "
            "not a probability or percent-identical claim."
        )
    else:
        score_basis = (
            "Qdrant ANN retrieval over NosePrint Scent Profile embeddings, "
            "with exact cosine retained as the correctness baseline; not a probability "
            "or percent-identical claim."
        )
    result = {
        "fragrance_edition_id": row["fragrance_edition_id"],
        "fragrance": row["fragrance"],
        "edition": row["edition"],
        "concentration": row["concentration"],
        "catalog_kind": row["catalog_kind"],
        "scent_match": {
            "method": method,
            "model_specific_score": round(score, 2),
            "score_basis": score_basis,
            "strength_label": _scent_match_strength(reference, row, score),
        },
        "profile_comparison": _profile_comparison(reference, row),
    }
    if include_price:
        result["comparable_price"] = _comparable_price(row)
        result["price_comparison"] = _price_comparison(reference, row)
    if include_wear_profile:
        result["wear_profile"] = _wear_profile(row)
    return result


def _comparable_price(row: sqlite3.Row) -> dict[str, Any]:
    if (
        row["amount_usd"] is None
        or row["observed_on"] is None
        or row["source_name"] is None
        or row["source_url"] is None
    ):
        return {
            "status": "unknown",
            "message": "Comparable Price is unknown and has not been guessed.",
        }
    amount = float(row["amount_usd"])
    bottle_size = float(row["bottle_size_ml"])
    return {
        "status": "known",
        "amount_usd": amount,
        "currency": row["currency"],
        "market": row["market"],
        "bottle_size_ml": bottle_size,
        "price_per_ml_usd": round(amount / bottle_size, 2),
        "observed_on": row["observed_on"],
        "source": {
            "name": row["source_name"],
            "url": row["source_url"],
        },
        "snapshot_notice": (
            "Dated United States USD Comparable Price snapshot; "
            "not a live price or availability promise."
        ),
    }


def _price_comparison(reference: sqlite3.Row, candidate: sqlite3.Row) -> dict[str, Any]:
    reference_price = _comparable_price(reference)
    candidate_price = _comparable_price(candidate)
    if reference_price["status"] != "known" or candidate_price["status"] != "known":
        return {
            "cheaper_filter": "excluded",
            "strictly_cheaper": False,
            "basis": "unknown_price",
            "message": "Unknown Comparable Prices cannot support a strict cheaper claim.",
        }
    same_size = (
        reference_price["bottle_size_ml"] == candidate_price["bottle_size_ml"]
    )
    strictly_cheaper = (
        same_size
        and candidate_price["amount_usd"] < reference_price["amount_usd"]
    )
    return {
        "cheaper_filter": "included" if strictly_cheaper else "excluded",
        "strictly_cheaper": strictly_cheaper,
        "basis": "same_bottle_size" if same_size else "different_bottle_size",
        "reference_amount_usd": reference_price["amount_usd"],
        "candidate_amount_usd": candidate_price["amount_usd"],
        "reference_bottle_size_ml": reference_price["bottle_size_ml"],
        "candidate_bottle_size_ml": candidate_price["bottle_size_ml"],
        "reference_price_per_ml_usd": reference_price["price_per_ml_usd"],
        "candidate_price_per_ml_usd": candidate_price["price_per_ml_usd"],
    }


def _wear_profile(row: sqlite3.Row) -> dict[str, str]:
    return {
        "longevity": _value_or_unknown(row["longevity"]),
        "projection": _value_or_unknown(row["projection"]),
        "skin_notice": (
            "Wear Profile facts are reported catalog observations, "
            "not a guarantee for every person's skin."
        ),
    }


def _include_wear_profile(args: argparse.Namespace) -> bool:
    return bool(args.show_wear_profiles or args.wear_longevity or args.wear_projection)


def _matches_wear_profile_filters(
    result: dict[str, Any],
    *,
    longevity: str | None,
    projection: str | None,
) -> bool:
    wear_profile = result["wear_profile"]
    if longevity and wear_profile["longevity"].casefold() != longevity.strip().casefold():
        return False
    if projection and wear_profile["projection"].casefold() != projection.strip().casefold():
        return False
    return True


def _qdrant_health(args: argparse.Namespace) -> int:
    database = Path(args.database)
    if not database.exists():
        return _catalog_unavailable(database)
    index_path = Path(args.index)
    connection = sqlite3.connect(database)
    connection.row_factory = sqlite3.Row
    try:
        try:
            rows = _real_catalog_scent_profile_rows(connection)
        except sqlite3.OperationalError:
            return _catalog_unavailable(database)
    finally:
        connection.close()
    eligible_count = len(rows)

    qdrant_status: dict[str, Any]
    if eligible_count == 0:
        qdrant_status = {
            "status": "not_applicable",
            "path": str(index_path),
            "message": "Import Real Catalog Scent Profiles into SQLite before building the Qdrant index.",
        }
    elif not index_path.exists():
        qdrant_status = {
            "status": "missing",
            "path": str(index_path),
            "message": "Rebuild the Qdrant index from SQLite before serving ANN Scent Matches.",
        }
    else:
        qdrant_status = _qdrant_index_freshness(index_path, rows)
    sqlite_catalog = {
        "status": "ok" if eligible_count else "empty",
        "eligible_real_catalog_records": eligible_count,
    }
    if eligible_count == 0:
        sqlite_catalog["message"] = (
            "Import Real Catalog Scent Profiles into SQLite before serving shopper search."
        )
    response = {
        "status": "empty_catalog" if eligible_count == 0 else qdrant_status["status"],
        "sqlite_catalog": sqlite_catalog,
        "embedding_runtime": _embedding_runtime_report(),
        "qdrant_index": qdrant_status,
        "rebuild_command": [
            sys.executable,
            "-m",
            "noseprint.catalog",
            "rebuild-qdrant-index",
            "--database",
            str(database),
            "--index",
            str(index_path),
        ],
    }
    if eligible_count == 0:
        response["next_actions"] = [
            "Run the audit and import workflow to add Real Catalog Scent Profiles to SQLite.",
            "Re-run qdrant-health after the Real Catalog import finishes.",
        ]
    print(json.dumps(response, indent=2))
    return 0


def _qdrant_index_freshness(
    index_path: Path, rows: list[sqlite3.Row]
) -> dict[str, Any]:
    if not index_path.exists():
        return {
            "status": "missing",
            "path": str(index_path),
            "message": "Rebuild the Qdrant index from SQLite before serving ANN Scent Matches.",
        }
    try:
        index_document = json.loads(index_path.read_text(encoding="utf-8"))
        index_metadata = index_document["metadata"]
    except (OSError, UnicodeError, json.JSONDecodeError, KeyError, TypeError) as error:
        return {
            "status": "unreadable",
            "path": str(index_path),
            "message": (
                "Rebuild the Qdrant index from SQLite before serving ANN Scent Matches."
            ),
            "detail": str(error),
        }
    expected_fingerprint = _catalog_fingerprint(
        [
            {
                "id": row["fragrance_edition_id"],
                "vector": _embed_scent_profile(row),
                "payload": {
                    "fragrance_edition_id": row["fragrance_edition_id"],
                    "catalog_kind": row["catalog_kind"],
                },
            }
            for row in rows
        ]
    )
    metadata_matches = (
        index_metadata.get("index_schema_version") == "qdrant-index-v1"
        and index_metadata.get("model") == EMBEDDING_MODEL
        and index_metadata.get("model_version") == EMBEDDING_MODEL_VERSION
        and index_metadata.get("pipeline_version") == SERIALIZATION_PIPELINE_VERSION
        and index_metadata.get("dimensions") == EMBEDDING_DIMENSIONS
        and index_metadata.get("catalog_fingerprint") == expected_fingerprint
        and index_metadata.get("points") == len(rows)
    )
    qdrant_status = {
        "status": "fresh" if metadata_matches else "stale",
        "path": str(index_path),
        "points": index_metadata.get("points"),
        "catalog_fingerprint": index_metadata.get("catalog_fingerprint"),
        "index_schema_version": index_metadata.get("index_schema_version"),
    }
    if not metadata_matches:
        qdrant_status["message"] = (
            "Rebuild the Qdrant index from SQLite before serving ANN Scent Matches."
        )
    return qdrant_status


def _rebuild_qdrant_index(args: argparse.Namespace) -> int:
    database = Path(args.database)
    if not database.exists():
        return _catalog_unavailable(database)
    index_path = Path(args.index)
    connection = sqlite3.connect(database)
    connection.row_factory = sqlite3.Row
    try:
        _create_embedding_schema(connection)
        try:
            rows = _real_catalog_scent_profile_rows(connection)
        except sqlite3.OperationalError:
            return _catalog_unavailable(database)
        points = []
        for row in rows:
            vector = _embed_scent_profile(row)
            _record_embedding(connection, row, vector)
            points.append(
                {
                    "id": row["fragrance_edition_id"],
                    "vector": vector,
                    "payload": {
                        "fragrance_edition_id": row["fragrance_edition_id"],
                        "catalog_kind": row["catalog_kind"],
                    },
                }
            )
        connection.commit()
    finally:
        connection.close()

    metadata = {
        "index_schema_version": "qdrant-index-v1",
        "points": len(points),
        "catalog_fingerprint": _catalog_fingerprint(points),
        **_embedding_metadata(),
    }
    index_document = {"metadata": metadata, "points": points}
    index_path.parent.mkdir(parents=True, exist_ok=True)
    index_path.write_text(json.dumps(index_document, indent=2) + "\n", encoding="utf-8")
    print(
        json.dumps(
            {
                "status": "rebuilt",
                "qdrant_index": {
                    "path": str(index_path),
                    "points": len(points),
                    "catalog_fingerprint": metadata["catalog_fingerprint"],
                },
                "embedding_runtime": _embedding_metadata(),
            },
            indent=2,
        )
    )
    return 0


def _generate_scale_test_catalog(args: argparse.Namespace) -> int:
    database = Path(args.database)
    database.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(database)
    connection.row_factory = sqlite3.Row
    try:
        _create_schema(connection)
        _replace_scale_test_catalog(
            connection,
            records=args.records,
            seed=args.seed,
        )
        connection.commit()
    finally:
        connection.close()
    print(
        json.dumps(
            {
                "status": "generated",
                "scale_test_catalog": {
                    "dataset_id": "scale-test-catalog-v1",
                    "records": args.records,
                    "seed": args.seed,
                    "catalog_kind": "scale-test",
                    "separation_notice": (
                        "Scale-Test Catalog records are generated for benchmarks "
                        "only and are not Real Catalog shopping inventory."
                    ),
                },
            },
            indent=2,
        )
    )
    return 0


def _import_parfumo_real_catalog(args: argparse.Namespace) -> int:
    report = _import_parfumo_real_catalog_file(Path(args.source), Path(args.database))
    print(json.dumps(report, indent=2))
    return 0


def _import_parfumo_real_catalog_file(source_path: Path, database: Path) -> dict[str, Any]:
    _validate_source_file(source_path)
    database.parent.mkdir(parents=True, exist_ok=True)
    counts = {
        "accepted": 0,
        "duplicates": 0,
        "quarantined": 0,
        "rejected": 0,
    }
    connection = sqlite3.connect(database)
    connection.row_factory = sqlite3.Row
    try:
        _create_schema(connection)
        _create_embedding_schema(connection)
        _create_comparable_price_schema(connection)
        _create_wear_profile_schema(connection)
        _clear_catalog(connection)
        connection.execute(
            """
            INSERT INTO catalog_sources
                (dataset_id, download_url, publisher, claimed_license, audit_report_json)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT (dataset_id) DO UPDATE SET
                download_url = excluded.download_url,
                publisher = excluded.publisher,
                claimed_license = excluded.claimed_license,
                audit_report_json = excluded.audit_report_json
            """,
            (
                TIDYTUESDAY_PARFUMO_DATASET_ID,
                "https://raw.githubusercontent.com/rfordatascience/tidytuesday/main/data/2024/2024-12-10/parfumo_data_clean.csv",
                "TidyTuesday / Parfumo Kaggle dataset",
                "Parfumo dataset for NosePrint Real Catalog",
                json.dumps(
                    {
                        "purpose": "NosePrint Real Catalog data",
                        "source_sha256": _sha256(source_path),
                    },
                    sort_keys=True,
                ),
            ),
        )
        seen_editions: set[tuple[str, str, str | None]] = set()
        next_id = 1
        with source_path.open("r", encoding="utf-8-sig", newline="") as source:
            reader = csv.DictReader(source)
            if reader.fieldnames != TIDYTUESDAY_PARFUMO_SCHEMA:
                raise ValueError("TidyTuesday Parfumo CSV schema does not match")
            for source_row, row in enumerate(reader, start=2):
                original_values = _json_safe_row(row)
                if None in row or any(value is None for value in row.values()):
                    _record_disposition(
                        connection,
                        dataset_id=TIDYTUESDAY_PARFUMO_DATASET_ID,
                        source_row=source_row,
                        disposition="rejected",
                        reason="malformed columns",
                        original_values=original_values,
                    )
                    counts["rejected"] += 1
                    continue
                brand = _parfumo_value(row.get("Brand", ""))
                name = _parfumo_value(row.get("Name", ""))
                concentration = _parfumo_value(row.get("Concentration", ""))
                if not brand or not name:
                    _record_disposition(
                        connection,
                        dataset_id=TIDYTUESDAY_PARFUMO_DATASET_ID,
                        source_row=source_row,
                        disposition="rejected",
                        reason="missing identity",
                        original_values=original_values,
                    )
                    counts["rejected"] += 1
                    continue
                edition_key = (brand.casefold(), name.casefold(), concentration)
                if edition_key in seen_editions:
                    _record_disposition(
                        connection,
                        dataset_id=TIDYTUESDAY_PARFUMO_DATASET_ID,
                        source_row=source_row,
                        disposition="duplicate",
                        reason="duplicate Fragrance Edition",
                        original_values=original_values,
                    )
                    counts["duplicates"] += 1
                    continue

                top_notes = _parfumo_list(row.get("Top_Notes", ""))
                middle_notes = _parfumo_list(row.get("Middle_Notes", ""))
                base_notes = _parfumo_list(row.get("Base_Notes", ""))
                main_accords = _parfumo_list(row.get("Main_Accords", ""))
                notes = sorted({*top_notes, *middle_notes, *base_notes})
                scent_family = _parfumo_first_item(row.get("Main_Accords", ""))
                if not any([notes, main_accords, scent_family]):
                    _record_disposition(
                        connection,
                        dataset_id=TIDYTUESDAY_PARFUMO_DATASET_ID,
                        source_row=source_row,
                        disposition="quarantined",
                        reason="missing Scent Profile facts",
                        original_values=original_values,
                    )
                    counts["quarantined"] += 1
                    continue

                fragrance_id = next_id
                edition_id = next_id
                next_id += 1
                connection.execute(
                    "INSERT INTO fragrances (id, name, brand) VALUES (?, ?, ?)",
                    (fragrance_id, name, brand),
                )
                connection.execute(
                    """
                    INSERT INTO fragrance_editions
                        (id, fragrance_id, name, concentration, catalog_kind)
                    VALUES (?, ?, ?, ?, 'real')
                    """,
                    (edition_id, fragrance_id, name, concentration),
                )
                connection.execute(
                    """
                    INSERT INTO scent_profiles
                        (fragrance_edition_id, notes_json, main_accords_json,
                         top_notes_json, middle_notes_json, base_notes_json,
                         scent_family)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        edition_id,
                        json.dumps(notes),
                        _json_or_null(main_accords),
                        _json_or_null(top_notes),
                        _json_or_null(middle_notes),
                        _json_or_null(base_notes),
                        scent_family,
                    ),
                )
                connection.execute(
                    """
                    INSERT INTO source_records
                        (dataset_id, source_row, fragrance_edition_id,
                         original_values_json)
                    VALUES (?, ?, ?, ?)
                    """,
                    (
                        TIDYTUESDAY_PARFUMO_DATASET_ID,
                        source_row,
                        edition_id,
                        json.dumps(original_values, sort_keys=True),
                    ),
                )
                seen_editions.add(edition_key)
                counts["accepted"] += 1
        connection.commit()
    finally:
        connection.close()

    return {
        "status": "imported",
        "real_catalog": {
            "dataset_id": TIDYTUESDAY_PARFUMO_DATASET_ID,
            "source": str(source_path),
            "catalog_kind": "real",
            **counts,
            "catalog_notice": (
                "TidyTuesday Parfumo records are the NosePrint Real Catalog."
            ),
        },
    }


def _clear_catalog(connection: sqlite3.Connection) -> None:
    connection.execute("DELETE FROM scent_profile_embeddings")
    connection.execute("DELETE FROM comparable_prices")
    connection.execute("DELETE FROM wear_profiles")
    connection.execute("DELETE FROM scent_profiles")
    connection.execute("DELETE FROM source_records")
    connection.execute("DELETE FROM import_dispositions")
    connection.execute("DELETE FROM fragrance_editions")
    connection.execute("DELETE FROM fragrances")


def _parfumo_value(value: str) -> str | None:
    normalized = value.strip()
    if not normalized or normalized.casefold() in {"na", "n/a", "nan"}:
        return None
    return normalized


def _parfumo_list(value: str) -> list[str]:
    normalized = _parfumo_value(value)
    if normalized is None:
        return []
    return _normalized_list(normalized)


def _parfumo_first_item(value: str) -> str | None:
    normalized = _parfumo_value(value)
    if normalized is None:
        return None
    for item in normalized.split(","):
        item = item.strip().casefold()
        if item:
            return item
    return None


def _replace_scale_test_catalog(
    connection: sqlite3.Connection,
    *,
    records: int,
    seed: int,
) -> None:
    if records < 1:
        raise ValueError("Scale-Test Catalog generation requires at least one record")
    connection.execute(
        """
        INSERT INTO catalog_sources
            (dataset_id, download_url, publisher, claimed_license, audit_report_json)
        VALUES (?, ?, ?, ?, ?)
        ON CONFLICT (dataset_id) DO UPDATE SET
            download_url = excluded.download_url,
            publisher = excluded.publisher,
            claimed_license = excluded.claimed_license,
            audit_report_json = excluded.audit_report_json
        """,
        (
            "scale-test-catalog-v1",
            "generated://noseprint/scale-test-catalog-v1",
            "NosePrint deterministic generator",
            "generated test data; not shopping inventory",
            json.dumps(
                {
                    "purpose": "Scale-Test Catalog benchmark data",
                    "records": records,
                    "seed": seed,
                },
                sort_keys=True,
            ),
        ),
    )
    _clear_scale_test_catalog(connection)
    for offset in range(records):
        generated = _scale_test_record(seed, offset)
        connection.execute(
            "INSERT INTO fragrances (id, name, brand) VALUES (?, ?, ?)",
            (
                generated["fragrance_id"],
                generated["fragrance"],
                generated["brand"],
            ),
        )
        connection.execute(
            """
            INSERT INTO fragrance_editions
                (id, fragrance_id, name, concentration, catalog_kind)
            VALUES (?, ?, ?, ?, 'scale-test')
            """,
            (
                generated["fragrance_edition_id"],
                generated["fragrance_id"],
                generated["edition"],
                generated["concentration"],
            ),
        )
        connection.execute(
            """
            INSERT INTO scent_profiles
                (fragrance_edition_id, notes_json, main_accords_json,
                 top_notes_json, middle_notes_json, base_notes_json, scent_family)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                generated["fragrance_edition_id"],
                json.dumps(generated["notes"]),
                json.dumps(generated["main_accords"]),
                json.dumps(generated["top_notes"]),
                json.dumps(generated["middle_notes"]),
                json.dumps(generated["base_notes"]),
                generated["scent_family"],
            ),
        )
        connection.execute(
            """
            INSERT INTO source_records
                (dataset_id, source_row, fragrance_edition_id, original_values_json)
            VALUES ('scale-test-catalog-v1', ?, ?, ?)
            """,
            (
                offset + 1,
                generated["fragrance_edition_id"],
                json.dumps(
                    {
                        "generator": "noseprint-scale-test-catalog-v1",
                        "seed": seed,
                        "offset": offset,
                    },
                    sort_keys=True,
                ),
            ),
        )


def _clear_scale_test_catalog(connection: sqlite3.Connection) -> None:
    scale_test_ids = [
        row["id"]
        for row in connection.execute(
            "SELECT id FROM fragrance_editions WHERE catalog_kind = 'scale-test'"
        ).fetchall()
    ]
    if scale_test_ids:
        placeholders = ",".join("?" for _ in scale_test_ids)
        connection.execute(
            f"DELETE FROM scent_profile_embeddings WHERE fragrance_edition_id IN ({placeholders})",
            scale_test_ids,
        )
        connection.execute(
            f"DELETE FROM scent_profiles WHERE fragrance_edition_id IN ({placeholders})",
            scale_test_ids,
        )
        connection.execute(
            f"DELETE FROM source_records WHERE fragrance_edition_id IN ({placeholders})",
            scale_test_ids,
        )
        connection.execute(
            f"DELETE FROM fragrance_editions WHERE id IN ({placeholders})",
            scale_test_ids,
        )
    connection.execute(
        """
        DELETE FROM fragrances
        WHERE id NOT IN (SELECT fragrance_id FROM fragrance_editions)
        """
    )


def _scale_test_record(seed: int, offset: int) -> dict[str, Any]:
    notes = [
        "rose",
        "iris",
        "cedar",
        "bergamot",
        "musk",
        "vanilla",
        "amber",
        "vetiver",
        "jasmine",
        "sandalwood",
    ]
    accords = ["floral", "fresh", "woody", "amber", "powdery", "citrus"]
    families = ["floral", "woody", "amber", "fresh"]
    concentrations = ["EDT", "EDP", "Parfum", "Extrait"]
    chosen_notes = _scale_test_choices(notes, seed, offset, count=3, salt="notes")
    concentration = concentrations[
        _scale_test_index(seed, offset, "concentration", len(concentrations))
    ]
    return {
        "fragrance_id": 1_000_000 + offset + 1,
        "fragrance_edition_id": 1_000_000 + offset + 1,
        "fragrance": f"Scale Test Fragrance {offset + 1:05d}",
        "brand": "NosePrint Scale Test",
        "edition": f"Scale Test Fragrance {offset + 1:05d} {concentration}",
        "concentration": concentration,
        "notes": sorted(chosen_notes),
        "main_accords": sorted(
            _scale_test_choices(accords, seed, offset, count=2, salt="accords")
        ),
        "top_notes": [chosen_notes[0]],
        "middle_notes": [chosen_notes[1]],
        "base_notes": [chosen_notes[2]],
        "scent_family": families[
            _scale_test_index(seed, offset, "family", len(families))
        ],
    }


def _scale_test_choices(
    values: list[str],
    seed: int,
    offset: int,
    *,
    count: int,
    salt: str,
) -> list[str]:
    ranked = sorted(
        values,
        key=lambda value: hashlib.sha256(
            f"{seed}:{offset}:{salt}:{value}".encode("utf-8")
        ).hexdigest(),
    )
    return ranked[:count]


def _scale_test_index(seed: int, offset: int, salt: str, size: int) -> int:
    digest = hashlib.sha256(f"{seed}:{offset}:{salt}".encode("utf-8")).digest()
    return int.from_bytes(digest[:8], "big") % size


APP_HTML = """<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>NosePrint</title>
  <style>
    :root {
      color-scheme: light;
      --page: #f4f7f5;
      --surface: #fffffd;
      --text: #171a18;
      --muted: #69716d;
      --line: #d8e0dc;
      --line-strong: #b7c6bf;
      --ink: #20342f;
      --accent: #3f7467;
      --copper: #9a6738;
      --rose: #9f5962;
      --warn: #8a4b16;
      --shadow: 0 24px 80px rgba(26, 43, 38, 0.11);
    }
    * { box-sizing: border-box; }
    html { min-height: 100%; background: var(--page); }
    body {
      min-height: 100%;
      margin: 0;
      font-family: "Avenir Next", "Segoe UI", ui-sans-serif, system-ui, sans-serif;
      background: linear-gradient(135deg, rgba(255, 255, 255, 0.92), rgba(244, 247, 245, 0) 44%), var(--page);
      color: var(--text);
      letter-spacing: 0;
    }
    button, input { font: inherit; }
    button { cursor: pointer; }
    button:disabled { cursor: not-allowed; opacity: 0.42; }
    button:focus-visible, input:focus-visible {
      outline: 3px solid rgba(63, 116, 103, 0.24);
      outline-offset: 3px;
    }
    h1, h2, h3, p { margin: 0; }
    .shell {
      width: min(1080px, calc(100vw - 32px));
      margin: 0 auto;
      padding: 22px 0 34px;
    }
    .topbar {
      display: grid;
      grid-template-columns: 1fr auto;
      gap: 24px;
      align-items: center;
      padding: 4px 2px 18px;
    }
    .brand {
      display: flex;
      gap: 12px;
      align-items: center;
      min-width: 0;
    }
    .logo {
      width: 52px;
      height: 52px;
      border: 1px solid var(--line-strong);
      border-radius: 8px;
      background: var(--surface);
      object-fit: cover;
      box-shadow: 0 12px 28px rgba(31, 47, 42, 0.1);
      flex: 0 0 auto;
    }
    h1 {
      font-size: clamp(28px, 5vw, 48px);
      line-height: 1;
      font-weight: 560;
      letter-spacing: 0;
    }
    .status {
      display: grid;
      gap: 2px;
      justify-items: end;
      color: var(--muted);
      font-size: 12px;
      white-space: nowrap;
    }
    .status strong {
      color: var(--text);
      font-size: 22px;
      font-weight: 560;
    }
    .stage-dots {
      display: grid;
      grid-template-columns: repeat(4, 1fr);
      gap: 8px;
      margin-bottom: 14px;
      padding: 0 2px;
    }
    .stage-dot {
      height: 3px;
      border-radius: 999px;
      background: var(--line);
    }
    .stage-dot.active { background: var(--accent); }
    .workspace, .screen { min-height: min(680px, calc(100vh - 142px)); }
    .workspace {
      position: relative;
      display: grid;
      overflow: hidden;
      border: 1px solid var(--line);
      border-radius: 8px;
      background: rgba(255, 255, 253, 0.92);
      box-shadow: var(--shadow);
    }
    .screen {
      display: none;
      align-content: center;
      justify-items: center;
      gap: 22px;
      padding: clamp(22px, 5vw, 56px);
    }
    .screen.active { display: grid; }
    .screen-inner {
      display: grid;
      gap: 20px;
      width: min(680px, 100%);
    }
    .screen-inner.wide { width: min(840px, 100%); }
    .screen-head { display: grid; gap: 8px; }
    .eyebrow {
      color: var(--copper);
      font-size: 11px;
      font-weight: 800;
      letter-spacing: 0.12em;
      text-transform: uppercase;
    }
    h2 {
      font-size: clamp(31px, 6vw, 62px);
      line-height: 0.96;
      font-weight: 520;
      letter-spacing: 0;
    }
    .compact-title {
      font-size: clamp(28px, 5vw, 48px);
      line-height: 1;
    }
    .small-title {
      font-size: 20px;
      line-height: 1.15;
      font-weight: 700;
    }
    .subtle {
      color: var(--muted);
      font-size: 14px;
      line-height: 1.5;
    }
    .search-form { display: grid; gap: 10px; }
    label {
      color: var(--muted);
      font-size: 12px;
      font-weight: 800;
      letter-spacing: 0.08em;
      text-transform: uppercase;
    }
    .search-row {
      display: grid;
      grid-template-columns: 1fr auto;
      gap: 10px;
      align-items: center;
    }
    input {
      width: 100%;
      min-width: 0;
      height: 54px;
      border: 1px solid var(--line-strong);
      border-radius: 8px;
      background: var(--surface);
      color: var(--text);
      padding: 0 14px;
      font-size: 18px;
      box-shadow: inset 0 1px 2px rgba(35, 31, 22, 0.04);
    }
    .primary, .secondary, .text-button {
      min-height: 46px;
      border-radius: 8px;
      border: 1px solid;
      padding: 0 16px;
      font-weight: 800;
    }
    .primary {
      background: var(--ink);
      border-color: var(--ink);
      color: #fffffd;
    }
    .secondary {
      background: rgba(255, 255, 255, 0.7);
      border-color: var(--line-strong);
      color: var(--ink);
    }
    .text-button {
      justify-self: start;
      min-height: 38px;
      padding: 0;
      border: 0;
      background: transparent;
      color: var(--accent);
    }
    .list { display: grid; gap: 8px; }
    .result {
      width: 100%;
      display: grid;
      grid-template-columns: 1fr auto;
      gap: 12px;
      align-items: center;
      min-height: 78px;
      border: 1px solid var(--line);
      border-radius: 8px;
      background: rgba(255, 255, 255, 0.58);
      color: var(--text);
      padding: 12px;
      text-align: left;
      transition: background 160ms ease, border-color 160ms ease, box-shadow 160ms ease;
    }
    .result:hover {
      background: var(--surface);
      border-color: var(--accent);
      box-shadow: 0 8px 22px rgba(48, 76, 69, 0.08);
    }
    .result-title, .match-title {
      display: block;
      color: var(--text);
      font-size: 14px;
      font-weight: 800;
      line-height: 1.25;
    }
    .meta {
      margin-top: 4px;
      color: var(--muted);
      font-size: 12px;
      line-height: 1.35;
    }
    .result-id {
      color: var(--muted);
      font-size: 11px;
    }
    .pager {
      display: flex;
      justify-content: space-between;
      align-items: center;
      gap: 10px;
    }
    .pager-status {
      color: var(--muted);
      font-size: 12px;
      text-align: center;
    }
    .empty {
      display: grid;
      place-items: center;
      min-height: 160px;
      border: 1px dashed var(--line-strong);
      border-radius: 8px;
      color: var(--muted);
      padding: 22px;
      text-align: center;
      line-height: 1.45;
    }
    .warning { color: var(--warn); }
    .profile-shell { display: grid; gap: 18px; }
    .profile-head { display: grid; gap: 10px; }
    .family-medallion {
      width: fit-content;
      border: 1px solid var(--line-strong);
      border-radius: 8px;
      background: rgba(255, 255, 255, 0.72);
      color: var(--accent);
      padding: 8px 10px;
      font-size: 13px;
      font-weight: 800;
      text-transform: capitalize;
    }
    .section-title {
      margin-bottom: 12px;
      color: var(--text);
      font-size: 13px;
      font-weight: 800;
      letter-spacing: 0.1em;
      text-transform: uppercase;
    }
    .accords {
      display: flex;
      flex-wrap: wrap;
      gap: 8px;
    }
    .chip {
      display: inline-flex;
      align-items: center;
      min-height: 30px;
      max-width: 100%;
      border: 1px solid var(--line);
      border-radius: 999px;
      background: rgba(255, 255, 255, 0.72);
      color: var(--ink);
      padding: 5px 10px;
      font-size: 12px;
      line-height: 1.2;
      overflow-wrap: anywhere;
      text-transform: capitalize;
    }
    .chip.unknown {
      color: var(--muted);
      background: transparent;
      border-style: dashed;
      text-transform: none;
    }
    .pyramid {
      display: grid;
      grid-template-columns: repeat(3, minmax(0, 1fr));
      border: 1px solid var(--line);
      border-radius: 8px;
      overflow: hidden;
      background: rgba(255, 255, 255, 0.62);
    }
    .note-column {
      min-width: 0;
      padding: 16px;
      border-right: 1px solid var(--line);
    }
    .note-column:last-child { border-right: 0; }
    .note-column h3 {
      color: var(--rose);
      font-size: 12px;
      font-weight: 800;
      letter-spacing: 0.09em;
      text-transform: uppercase;
    }
    .note-column .chips {
      display: flex;
      flex-wrap: wrap;
      gap: 7px;
      margin-top: 12px;
    }
    .matches { display: grid; gap: 8px; }
    .match {
      display: grid;
      gap: 8px;
      border: 1px solid var(--line);
      border-radius: 8px;
      background: rgba(255, 255, 255, 0.58);
      padding: 14px;
    }
    .score-row {
      display: grid;
      grid-template-columns: 1fr auto;
      gap: 10px;
      align-items: center;
    }
    .score-track {
      height: 5px;
      border-radius: 999px;
      background: #e4ebe8;
      overflow: hidden;
    }
    .score-fill {
      height: 100%;
      width: var(--score-width);
      border-radius: inherit;
      background: linear-gradient(90deg, var(--accent), var(--copper));
    }
    .score {
      color: var(--accent);
      font-size: 12px;
      font-weight: 800;
      text-transform: capitalize;
      white-space: nowrap;
    }
    .loading {
      position: relative;
      overflow: hidden;
      background: rgba(255, 255, 255, 0.48);
    }
    .loading::after {
      content: "";
      position: absolute;
      inset: 0;
      transform: translateX(-100%);
      background: linear-gradient(90deg, transparent, rgba(255, 255, 255, 0.68), transparent);
      animation: sweep 1.3s infinite;
    }
    @keyframes sweep { to { transform: translateX(100%); } }
    @media (max-width: 640px) {
      .shell { width: min(100vw - 20px, 520px); padding: 12px 0; }
      .topbar { grid-template-columns: 1fr; align-items: start; }
      .logo { width: 44px; height: 44px; }
      .status { justify-items: start; white-space: normal; }
      .workspace, .screen { min-height: calc(100vh - 150px); }
      .screen { align-content: start; padding: 20px; }
      .search-row { grid-template-columns: 1fr; }
      .pyramid { grid-template-columns: 1fr; }
      .note-column { border-right: 0; border-bottom: 1px solid var(--line); }
      .note-column:last-child { border-bottom: 0; }
    }
  </style>
</head>
<body>
  <main class="shell">
    <header class="topbar">
      <div class="brand">
        <img class="logo" src="/assets/noseprint-logo.png" alt="" aria-hidden="true">
        <h1>NosePrint</h1>
      </div>
      <div class="status" id="catalog-status" aria-live="polite">
        <span>Real Catalog</span>
        <strong>Loading</strong>
      </div>
    </header>
    <div class="stage-dots" aria-hidden="true">
      <div class="stage-dot active" data-stage-dot="search"></div>
      <div class="stage-dot" data-stage-dot="results"></div>
      <div class="stage-dot" data-stage-dot="profile"></div>
      <div class="stage-dot" data-stage-dot="matches"></div>
    </div>
    <div class="workspace">
      <section class="screen active" id="screen-search" aria-labelledby="search-title">
        <div class="screen-inner">
          <div class="screen-head">
            <div class="eyebrow">Start</div>
            <h2 id="search-title">Name the Fragrance.</h2>
          </div>
          <form class="search-form" id="search-form">
            <label for="query">Fragrance name</label>
            <div class="search-row">
              <input id="query" name="query" type="search" value="rose" autocomplete="off">
              <button class="primary" type="submit">Search</button>
            </div>
          </form>
        </div>
      </section>
      <section class="screen" id="screen-results" aria-labelledby="results-title">
        <div class="screen-inner wide">
          <div class="eyebrow">Real Catalog</div>
          <h2 class="compact-title" id="results-title">Choose one result.</h2>
          <div class="subtle" id="results-meta" aria-live="polite"></div>
          <div class="list" id="results" role="listbox" aria-label="Search results"></div>
          <div class="pager" id="pager" hidden>
            <button class="secondary" id="previous-page" type="button">Previous</button>
            <div class="pager-status" id="pager-status"></div>
            <button class="secondary" id="next-page" type="button">Next</button>
          </div>
          <button class="text-button" id="change-search" type="button">Change search</button>
        </div>
      </section>
      <section class="screen" id="screen-profile" aria-labelledby="profile-title">
        <div class="screen-inner wide" id="profile">
          <div class="empty">
            <div>
              <div class="eyebrow">Scent Profile</div>
              <h2 class="small-title" id="profile-title">Choose a Fragrance Edition</h2>
            </div>
          </div>
        </div>
      </section>
      <section class="screen" id="screen-matches" aria-labelledby="matches-title">
        <div class="screen-inner wide">
          <div class="screen-head">
            <div class="eyebrow">Scent Matches</div>
            <h2 class="compact-title" id="matches-title">Nearby choices.</h2>
          </div>
          <div class="matches" id="matches"></div>
          <button class="text-button" id="back-to-profile" type="button">Back to profile</button>
        </div>
      </section>
    </div>
  </main>
  <script>
    const statusEl = document.querySelector("#catalog-status");
    const resultsEl = document.querySelector("#results");
    const resultsMetaEl = document.querySelector("#results-meta");
    const profileEl = document.querySelector("#profile");
    const matchesEl = document.querySelector("#matches");
    const form = document.querySelector("#search-form");
    const queryInput = document.querySelector("#query");
    const pagerEl = document.querySelector("#pager");
    const pagerStatusEl = document.querySelector("#pager-status");
    const previousPageButton = document.querySelector("#previous-page");
    const nextPageButton = document.querySelector("#next-page");
    const changeSearchButton = document.querySelector("#change-search");
    const backToProfileButton = document.querySelector("#back-to-profile");
    const screens = {
      search: document.querySelector("#screen-search"),
      results: document.querySelector("#screen-results"),
      profile: document.querySelector("#screen-profile"),
      matches: document.querySelector("#screen-matches")
    };
    let currentPage = 1;
    let selectedProfile = null;
    let profileRequestId = 0;
    let matchesRequestId = 0;
    const perPage = 10;

    function escapeHtml(value) {
      return String(value ?? "").replace(/[&<>"']/g, character => ({
        "&": "&amp;",
        "<": "&lt;",
        ">": "&gt;",
        '"': "&quot;",
        "'": "&#39;"
      }[character]));
    }

    function showScreen(name) {
      Object.entries(screens).forEach(([screenName, element]) => {
        element.classList.toggle("active", screenName === name);
      });
      document.querySelectorAll("[data-stage-dot]").forEach(dot => {
        dot.classList.toggle("active", dot.dataset.stageDot === name);
      });
    }

    function normalizeList(value) {
      if (Array.isArray(value) && value.length) return value;
      if (value && value !== "unknown") return [value];
      return ["unknown"];
    }

    function displayText(value) {
      if (Array.isArray(value)) return value.join(", ");
      return value || "unknown";
    }

    function titleCase(value) {
      return String(value || "unknown").replace(/\\b\\w/g, letter => letter.toUpperCase());
    }

    function chips(value) {
      return `<div class="chips">${normalizeList(value).map(item => {
        const unknownClass = item === "unknown" ? " unknown" : "";
        return `<span class="chip${unknownClass}">${escapeHtml(item)}</span>`;
      }).join("")}</div>`;
    }

    function selectedEditionMeta(item) {
      const concentration = item.concentration ? ` / ${item.concentration}` : "";
      return `${item.fragrance}${concentration}`;
    }

    async function loadStatus() {
      try {
        const response = await fetch("/api/status");
        const data = await response.json();
        statusEl.innerHTML = `<span>Real Catalog</span><strong>${data.real_catalog_records.toLocaleString()}</strong>`;
      } catch (error) {
        statusEl.innerHTML = `<span class="warning">Catalog unavailable</span>`;
      }
    }

    async function search(query, page = 1) {
      currentPage = page;
      selectedProfile = null;
      matchesEl.innerHTML = "";
      resultsMetaEl.textContent = "";
      resultsEl.innerHTML = `<div class="empty loading">Searching...</div>`;
      pagerEl.hidden = true;
      showScreen("results");
      const response = await fetch(`/api/search?q=${encodeURIComponent(query)}&page=${page}&per_page=${perPage}`);
      const data = await response.json();
      if (!data.results.length) {
        resultsMetaEl.textContent = "No matches";
        resultsEl.innerHTML = `<div class="empty">Try another Fragrance name.</div>`;
        renderPager(data.pagination);
        return;
      }
      resultsEl.innerHTML = data.results.map(result => `
        <button class="result" type="button" role="option" aria-selected="false" data-id="${result.fragrance_edition_id}">
          <span>
            <strong class="result-title">${escapeHtml(result.fragrance)}</strong>
            <span class="meta">${escapeHtml(result.edition)}${result.concentration ? " / " + escapeHtml(result.concentration) : ""}</span>
          </span>
          <span class="result-id">#${escapeHtml(result.fragrance_edition_id)}</span>
        </button>
      `).join("");
      resultsEl.querySelectorAll("button").forEach(button => {
        button.addEventListener("click", () => loadProfile(button.dataset.id));
      });
      renderPager(data.pagination);
      resultsMetaEl.textContent = `${data.pagination.total_results.toLocaleString()} found for "${query || "all"}"`;
    }

    function renderPager(pagination) {
      if (!pagination) {
        pagerEl.hidden = true;
        return;
      }
      pagerEl.hidden = false;
      const start = pagination.total_results ? ((pagination.page - 1) * pagination.per_page) + 1 : 0;
      const end = Math.min(pagination.page * pagination.per_page, pagination.total_results);
      pagerStatusEl.textContent = `${start}-${end} of ${pagination.total_results.toLocaleString()}`;
      previousPageButton.disabled = !pagination.has_previous;
      nextPageButton.disabled = !pagination.has_next;
    }

    async function loadProfile(id) {
      const requestId = ++profileRequestId;
      ++matchesRequestId;
      profileEl.innerHTML = `<div class="empty loading">Loading Scent Profile...</div>`;
      showScreen("profile");
      const profileResponse = await fetch(`/api/profile?id=${encodeURIComponent(id)}`);
      const profile = await profileResponse.json();
      if (requestId !== profileRequestId) return;
      if (profile.status !== "ok") {
        profileEl.innerHTML = `<div class="empty warning">Fragrance Edition not found.</div>`;
        return;
      }
      selectedProfile = profile;
      const scent = profile.scent_profile;
      profileEl.innerHTML = `
        <div class="profile-shell">
          <div class="profile-head">
            <div class="eyebrow">Scent Profile</div>
            <h2 class="compact-title" id="profile-title">${escapeHtml(profile.edition)}</h2>
            <p class="subtle">${escapeHtml(selectedEditionMeta(profile))}</p>
            <div class="family-medallion">${escapeHtml(titleCase(displayText(scent.scent_family)))}</div>
          </div>
          <div>
            <div class="section-title">Main accords</div>
            <div class="accords">${normalizeList(scent.main_accords).map(item => `<span class="chip${item === "unknown" ? " unknown" : ""}">${escapeHtml(item)}</span>`).join("")}</div>
          </div>
          <div>
            <div class="section-title">Notes</div>
            <div class="pyramid" aria-label="Note pyramid">
              <div class="note-column"><h3>Top</h3>${chips(scent.note_pyramid.top)}</div>
              <div class="note-column"><h3>Middle</h3>${chips(scent.note_pyramid.middle)}</div>
              <div class="note-column"><h3>Base</h3>${chips(scent.note_pyramid.base)}</div>
            </div>
          </div>
          <button class="primary" id="find-matches" type="button">Find matches</button>
          <button class="text-button" id="back-to-results" type="button">Back to results</button>
        </div>
      `;
      document.querySelector("#find-matches").addEventListener("click", () => loadMatches(profile.fragrance_edition_id));
      document.querySelector("#back-to-results").addEventListener("click", () => showScreen("results"));
    }

    function scoreWidth(score) {
      const numeric = Number(score);
      if (!Number.isFinite(numeric)) return 0;
      return Math.max(4, Math.min(100, Math.round(numeric * 100)));
    }

    async function loadMatches(id) {
      const requestId = ++matchesRequestId;
      const title = selectedProfile ? selectedProfile.edition : "Nearby choices";
      document.querySelector("#matches-title").textContent = title;
      matchesEl.innerHTML = `<div class="empty loading">Finding matches...</div>`;
      showScreen("matches");
      try {
        const matchesResponse = await fetch(`/api/matches?id=${encodeURIComponent(id)}&limit=5`);
        const matches = await matchesResponse.json();
        if (requestId !== matchesRequestId) return;
        if (matches.status === "error") {
          matchesEl.innerHTML = `<div class="empty warning">Scent Matches are unavailable right now.</div>`;
          return;
        }
        matchesEl.innerHTML = (matches.results || []).map(match => {
          const score = match.scent_match.model_specific_score;
          return `
            <div class="match">
              <div>
                <strong class="match-title">${escapeHtml(match.edition)}</strong>
                <div class="meta">${escapeHtml(match.fragrance)}${match.concentration ? " / " + escapeHtml(match.concentration) : ""}</div>
              </div>
              <div class="score-row">
                <div class="score-track" aria-label="Model score ${escapeHtml(score)}" role="img"><div class="score-fill" style="--score-width: ${scoreWidth(score)}%"></div></div>
                <div class="score">${escapeHtml(match.scent_match.strength_label)}</div>
              </div>
            </div>
          `;
        }).join("") || `<div class="empty">No Scent Matches yet.</div>`;
      } catch (error) {
        if (requestId !== matchesRequestId) return;
        matchesEl.innerHTML = `<div class="empty warning">Scent Matches are unavailable right now.</div>`;
      }
    }

    form.addEventListener("submit", event => {
      event.preventDefault();
      search(queryInput.value.trim(), 1);
    });
    previousPageButton.addEventListener("click", () => {
      if (currentPage > 1) search(queryInput.value.trim(), currentPage - 1);
    });
    nextPageButton.addEventListener("click", () => {
      search(queryInput.value.trim(), currentPage + 1);
    });
    changeSearchButton.addEventListener("click", () => {
      showScreen("search");
      queryInput.focus();
    });
    backToProfileButton.addEventListener("click", () => showScreen("profile"));

    loadStatus();
  </script>
</body>
</html>
"""


def _json_response_from_handler(handler: Any, args: argparse.Namespace) -> dict[str, Any]:
    output = io.StringIO()
    with APP_STDOUT_LOCK:
        with contextlib.redirect_stdout(output):
            exit_code = handler(args)
    if exit_code != 0:
        return {"status": "error", "message": output.getvalue().strip()}
    return json.loads(output.getvalue())


def _app_status(database: Path) -> dict[str, Any]:
    connection = sqlite3.connect(database)
    connection.row_factory = sqlite3.Row
    try:
        rows = _real_catalog_scent_profile_rows(connection)
    finally:
        connection.close()
    return {
        "status": "ok",
        "real_catalog_records": len(rows),
        "database": str(database),
    }


def _make_app_handler(database: Path, index: Path) -> type[BaseHTTPRequestHandler]:
    class NosePrintAppHandler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            parsed = urlparse(self.path)
            query = parse_qs(parsed.query)
            try:
                if parsed.path == "/":
                    self._send_html(APP_HTML)
                elif parsed.path == "/assets/noseprint-logo.png":
                    self._send_png(Path(__file__).resolve().parent.parent / "assets" / "noseprint-logo.png")
                elif parsed.path == "/api/status":
                    self._send_json(_app_status(database))
                elif parsed.path == "/api/search":
                    self._send_json(
                        _browse_response(
                            argparse.Namespace(
                                database=str(database),
                                query=query.get("q", [""])[0],
                                page=int(query.get("page", ["1"])[0]),
                                per_page=int(query.get("per_page", ["10"])[0]),
                            )
                        )
                    )
                elif parsed.path == "/api/profile":
                    self._send_json(
                        _scent_profile_response(
                            argparse.Namespace(
                                database=str(database),
                                edition_id=int(query.get("id", ["0"])[0]),
                            )
                        )
                    )
                elif parsed.path == "/api/matches":
                    self._send_json(
                        _json_response_from_handler(
                            _scent_matches,
                            argparse.Namespace(
                                database=str(database),
                                index=str(index) if index.exists() else None,
                                edition_id=int(query.get("id", ["0"])[0]),
                                limit=int(query.get("limit", ["5"])[0]),
                                show_prices=False,
                                cheaper_only=False,
                                show_wear_profiles=False,
                                wear_longevity=None,
                                wear_projection=None,
                            ),
                        )
                    )
                else:
                    self.send_error(404)
            except (ValueError, sqlite3.Error, json.JSONDecodeError) as error:
                self._send_json({"status": "error", "message": str(error)}, status=500)

        def log_message(self, format: str, *args: Any) -> None:
            return

        def _send_html(self, body: str) -> None:
            encoded = body.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(encoded)))
            self.end_headers()
            self.wfile.write(encoded)

        def _send_png(self, path: Path) -> None:
            if not path.exists():
                self.send_error(404)
                return
            encoded = path.read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", "image/png")
            self.send_header("Content-Length", str(len(encoded)))
            self.end_headers()
            self.wfile.write(encoded)

        def _send_json(self, payload: dict[str, Any], status: int = 200) -> None:
            encoded = (json.dumps(payload, indent=2) + "\n").encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(encoded)))
            self.end_headers()
            self.wfile.write(encoded)

    return NosePrintAppHandler


def _run_app(args: argparse.Namespace) -> int:
    database = Path(args.database)
    index = Path(args.index)
    source = Path(args.source) if args.source else None
    if source is not None:
        import_report = _import_parfumo_real_catalog_file(source, database)
        if import_report["real_catalog"]["accepted"] == 0:
            print(
                "App startup blocked: Parfumo import accepted no Real Catalog rows.",
                file=sys.stderr,
            )
            return EXIT_BLOCKED
    elif not database.exists():
        print(
            "App startup blocked: pass --source /path/to/parfumo_data_clean.csv "
            "the first time you run NosePrint.",
            file=sys.stderr,
        )
        return EXIT_BLOCKED

    with contextlib.redirect_stdout(io.StringIO()):
        _rebuild_qdrant_index(
            argparse.Namespace(database=str(database), index=str(index))
        )
    status = _app_status(database)
    if args.prepare_only:
        print(
            json.dumps(
                {
                    "status": "prepared",
                    "app": {
                        "url": f"http://{args.host}:{args.port}/",
                        "database": str(database),
                        "index": str(index),
                        "real_catalog_records": status["real_catalog_records"],
                    },
                },
                indent=2,
            )
        )
        return 0

    server = ThreadingHTTPServer(
        (args.host, args.port),
        _make_app_handler(database, index),
    )
    host = args.host if args.host != "0.0.0.0" else "localhost"
    print(
        json.dumps(
            {
                "status": "running",
                "app": {
                    "url": f"http://{host}:{server.server_port}/",
                    "database": str(database),
                    "index": str(index),
                    "real_catalog_records": status["real_catalog_records"],
                },
            },
            indent=2,
        ),
        flush=True,
    )
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        return 0
    finally:
        server.server_close()
    return 0


def _benchmark_scale_test_catalog(args: argparse.Namespace) -> int:
    database = Path(args.database)
    if not database.exists():
        return _catalog_unavailable(database)
    index_path = Path(args.index)
    connection = sqlite3.connect(database)
    connection.row_factory = sqlite3.Row
    try:
        _create_embedding_schema(connection)
        try:
            rows = _scale_test_scent_profile_rows(connection)
        except sqlite3.OperationalError:
            return _catalog_unavailable(database)
        if not rows:
            print(
                json.dumps(
                    {
                        "status": "no_scale_test_catalog",
                        "message": "Generate a Scale-Test Catalog before running benchmarks.",
                    },
                    indent=2,
                )
            )
            return 0
        reference = next(
            (
                row
                for row in rows
                if row["fragrance_edition_id"] == args.reference_edition_id
            ),
            None,
        )
        if reference is None:
            print(
                json.dumps(
                    {
                        "status": "not_found",
                        "message": "No Scale-Test Catalog Fragrance Edition is available for that reference id.",
                    },
                    indent=2,
                )
            )
            return 0
        points = []
        for row in rows:
            vector = _embed_scent_profile(row)
            _record_embedding(connection, row, vector)
            points.append(
                {
                    "id": row["fragrance_edition_id"],
                    "vector": vector,
                    "payload": {
                        "fragrance_edition_id": row["fragrance_edition_id"],
                        "catalog_kind": "scale-test",
                    },
                }
            )
        connection.commit()
    finally:
        connection.close()

    metadata = {
        "index_schema_version": "qdrant-scale-test-index-v1",
        "points": len(points),
        "catalog_fingerprint": _catalog_fingerprint(points),
        **_embedding_metadata(),
    }
    index_document = {"metadata": metadata, "points": points}
    index_path.parent.mkdir(parents=True, exist_ok=True)
    index_path.write_text(json.dumps(index_document, indent=2) + "\n", encoding="utf-8")
    rows_by_id = {row["fragrance_edition_id"]: row for row in rows}
    exact_results = _rank_exact_reference_matches(reference, rows, limit=args.limit)
    ann_results = _rank_scale_test_ann_matches(
        reference,
        rows_by_id,
        index_document,
        limit=args.limit,
    )
    exact_ids = [result["fragrance_edition_id"] for result in exact_results]
    ann_ids = [result["fragrance_edition_id"] for result in ann_results]
    print(
        json.dumps(
            {
                "status": "ok",
                "catalog": {
                    "kind": "scale-test",
                    "size": len(rows),
                },
                "reference": {
                    "fragrance_edition_id": reference["fragrance_edition_id"],
                    "fragrance": reference["fragrance"],
                    "edition": reference["edition"],
                },
                "configuration": {
                    "top_k": args.limit,
                    "embedding": _embedding_metadata(),
                    "exact": {"method": "exact_cosine"},
                    "qdrant_ann": {
                        "method": "qdrant_ann",
                        "index_status": "rebuilt",
                        "index_schema_version": metadata["index_schema_version"],
                        "index_path": str(index_path),
                    },
                },
                "metrics": {
                    "recall_at_k": _recall(
                        len(set(exact_ids) & set(ann_ids)),
                        len(exact_ids),
                    ),
                    "embedding_latency_ms": 0,
                    "retrieval_latency_ms": 0,
                    "hydration_latency_ms": 0,
                },
                "exact_cosine": {"retrieved_ids": exact_ids},
                "qdrant_ann": {"retrieved_ids": ann_ids},
                "separation_notice": (
                    "Scale-Test Catalog benchmark results are performance data, "
                    "not Real Catalog quality results or shopping recommendations."
                ),
            },
            indent=2,
        )
    )
    return 0


def _rank_scale_test_ann_matches(
    reference: sqlite3.Row,
    rows_by_id: dict[int, sqlite3.Row],
    index_document: dict[str, Any],
    *,
    limit: int,
) -> list[dict[str, Any]]:
    reference_vector = _embed_scent_profile(reference)
    ranked_matches = []
    for point in index_document["points"]:
        payload = point.get("payload", {})
        edition_id = payload.get("fragrance_edition_id")
        if edition_id == reference["fragrance_edition_id"]:
            continue
        if (
            point.get("id") != edition_id
            or payload.get("catalog_kind") != "scale-test"
            or edition_id not in rows_by_id
        ):
            continue
        ranked_matches.append(
            _scent_match_result(
                reference,
                rows_by_id[edition_id],
                _cosine_similarity(reference_vector, point["vector"]),
                method="qdrant_ann",
            )
        )
    return _sort_reference_matches(ranked_matches)[:limit]


def _evaluate_reference_matches(args: argparse.Namespace) -> int:
    database = Path(args.database)
    if not database.exists():
        return _catalog_unavailable(database)
    index_path = Path(args.index)
    reference_match_set = json.loads(
        Path(args.reference_match_set).read_text(encoding="utf-8")
    )
    connection = sqlite3.connect(database)
    connection.row_factory = sqlite3.Row
    try:
        _create_embedding_schema(connection)
        try:
            rows = _real_catalog_scent_profile_rows(connection)
        except sqlite3.OperationalError:
            return _catalog_unavailable(database)
        freshness = _qdrant_index_freshness(index_path, rows)
        if freshness["status"] != "fresh":
            print(
                json.dumps(
                    {
                        "status": "index_unavailable",
                        "message": "Rebuild the Qdrant index from SQLite before evaluating ANN retrieval.",
                        "qdrant_index": freshness,
                    },
                    indent=2,
                )
            )
            return 0
        cases, metrics = _reference_match_evaluation_cases(
            reference_match_set,
            rows,
            index_path,
            limit=args.limit,
        )
        response = {
            "status": "ok",
            "reference_match_set": {
                "id": reference_match_set["id"],
                "purpose": reference_match_set.get("purpose", "evaluation_only"),
                "entries": len(reference_match_set.get("entries", [])),
                "separation_notice": (
                    "Reference Match Set data is evaluation-only; it is not "
                    "training data, user activity, a Real Catalog source, or "
                    "embedding input."
                ),
            },
            "configuration": {
                "top_k": args.limit,
                "embedding": _embedding_metadata(),
                "exact": {"method": "exact_cosine"},
                "qdrant_ann": {
                    "method": "qdrant_ann",
                    "index_status": freshness["status"],
                    "index_schema_version": freshness.get("index_schema_version"),
                    "index_path": str(index_path),
                },
            },
            "metrics": metrics,
            "cases": cases,
        }
        connection.commit()
    finally:
        connection.close()
    print(json.dumps(response, indent=2))
    return 0


def _reference_match_evaluation_cases(
    reference_match_set: dict[str, Any],
    rows: list[sqlite3.Row],
    index_path: Path,
    *,
    limit: int,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    rows_by_id = {row["fragrance_edition_id"]: row for row in rows}
    index_document = json.loads(index_path.read_text(encoding="utf-8"))
    aggregate = {
        "exact_cosine": {"cases": 0, "expected": 0, "hits": 0},
        "qdrant_ann": {"cases": 0, "expected": 0, "hits": 0},
    }
    cases = []
    for entry in reference_match_set.get("entries", []):
        reference_id = entry["reference_fragrance_edition_id"]
        if reference_id not in rows_by_id:
            continue
        reference = rows_by_id[reference_id]
        expected_ids = sorted(entry.get("reasonable_alternative_ids", []))
        exact_results = _rank_exact_reference_matches(reference, rows, limit=limit)
        ann_results = _rank_ann_reference_matches(
            reference,
            rows_by_id,
            index_document,
            limit=limit,
        )
        exact_hit_ids = _reference_hit_ids(exact_results, expected_ids)
        ann_hit_ids = _reference_hit_ids(ann_results, expected_ids)
        aggregate["exact_cosine"]["cases"] += 1
        aggregate["exact_cosine"]["expected"] += len(expected_ids)
        aggregate["exact_cosine"]["hits"] += len(exact_hit_ids)
        aggregate["qdrant_ann"]["cases"] += 1
        aggregate["qdrant_ann"]["expected"] += len(expected_ids)
        aggregate["qdrant_ann"]["hits"] += len(ann_hit_ids)
        cases.append(
            {
                "reference_fragrance_edition_id": reference_id,
                "expected_alternative_ids": expected_ids,
                "exact_cosine": {
                    "retrieved_ids": [
                        result["fragrance_edition_id"] for result in exact_results
                    ],
                    "hit_ids": exact_hit_ids,
                    "recall_at_k": _recall(len(exact_hit_ids), len(expected_ids)),
                },
                "qdrant_ann": {
                    "retrieved_ids": [
                        result["fragrance_edition_id"] for result in ann_results
                    ],
                    "hit_ids": ann_hit_ids,
                    "recall_at_k": _recall(len(ann_hit_ids), len(expected_ids)),
                },
                "inspectable_outcomes": _inspectable_evaluation_outcomes(
                    exact_results,
                    ann_results,
                ),
            }
        )
    return cases, {
        "exact_cosine": _evaluation_metric_summary(aggregate["exact_cosine"]),
        "qdrant_ann": _evaluation_metric_summary(aggregate["qdrant_ann"]),
    }


def _rank_exact_reference_matches(
    reference: sqlite3.Row, rows: list[sqlite3.Row], *, limit: int
) -> list[dict[str, Any]]:
    reference_vector = _embed_scent_profile(reference)
    ranked_matches = []
    for row in rows:
        if row["fragrance_edition_id"] == reference["fragrance_edition_id"]:
            continue
        ranked_matches.append(
            _scent_match_result(
                reference,
                row,
                _cosine_similarity(reference_vector, _embed_scent_profile(row)),
                method="exact_cosine",
            )
        )
    return _sort_reference_matches(ranked_matches)[:limit]


def _rank_ann_reference_matches(
    reference: sqlite3.Row,
    rows_by_id: dict[int, sqlite3.Row],
    index_document: dict[str, Any],
    *,
    limit: int,
) -> list[dict[str, Any]]:
    reference_vector = _embed_scent_profile(reference)
    ranked_matches = []
    for point in index_document["points"]:
        payload = point.get("payload", {})
        edition_id = payload.get("fragrance_edition_id")
        if edition_id == reference["fragrance_edition_id"]:
            continue
        if (
            point.get("id") != edition_id
            or payload.get("catalog_kind") != "real"
            or edition_id not in rows_by_id
        ):
            continue
        ranked_matches.append(
            _scent_match_result(
                reference,
                rows_by_id[edition_id],
                _cosine_similarity(reference_vector, point["vector"]),
                method="qdrant_ann",
            )
        )
    return _sort_reference_matches(ranked_matches)[:limit]


def _sort_reference_matches(results: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return sorted(
        results,
        key=lambda result: (
            -result["scent_match"]["model_specific_score"],
            result["fragrance"],
            result["edition"],
            result["fragrance_edition_id"],
        ),
    )


def _reference_hit_ids(
    results: list[dict[str, Any]], expected_ids: list[int]
) -> list[int]:
    expected = set(expected_ids)
    return sorted(
        result["fragrance_edition_id"]
        for result in results
        if result["fragrance_edition_id"] in expected
    )


def _inspectable_evaluation_outcomes(
    exact_results: list[dict[str, Any]], ann_results: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    inspectable = []
    seen_ids = set()
    for result in [*exact_results, *ann_results]:
        edition_id = result["fragrance_edition_id"]
        label = result["scent_match"]["strength_label"]
        if edition_id in seen_ids or label not in {"weak", "surprising"}:
            continue
        inspectable.append(result)
        seen_ids.add(edition_id)
    return inspectable


def _evaluation_metric_summary(counts: dict[str, int]) -> dict[str, Any]:
    return {
        "cases": counts["cases"],
        "expected_alternatives": counts["expected"],
        "retrieved_expected_alternatives": counts["hits"],
        "recall_at_k": _recall(counts["hits"], counts["expected"]),
        "latency_ms": 0,
    }


def _recall(hits: int, expected: int) -> float:
    return 1.0 if expected == 0 else round(hits / expected, 2)


def _real_catalog_scent_profile_rows(
    connection: sqlite3.Connection,
) -> list[sqlite3.Row]:
    return connection.execute(
        """
        SELECT fe.id AS fragrance_edition_id, f.name AS fragrance,
               fe.name AS edition, fe.concentration, fe.catalog_kind,
               sp.notes_json, sp.main_accords_json, sp.top_notes_json,
               sp.middle_notes_json, sp.base_notes_json, sp.scent_family
        FROM fragrance_editions AS fe
        JOIN fragrances AS f ON f.id = fe.fragrance_id
        JOIN scent_profiles AS sp ON sp.fragrance_edition_id = fe.id
        WHERE fe.catalog_kind = 'real'
        ORDER BY fe.id
        """
    ).fetchall()


def _scale_test_scent_profile_rows(
    connection: sqlite3.Connection,
) -> list[sqlite3.Row]:
    return connection.execute(
        """
        SELECT fe.id AS fragrance_edition_id, f.name AS fragrance,
               fe.name AS edition, fe.concentration, fe.catalog_kind,
               sp.notes_json, sp.main_accords_json, sp.top_notes_json,
               sp.middle_notes_json, sp.base_notes_json, sp.scent_family
        FROM fragrance_editions AS fe
        JOIN fragrances AS f ON f.id = fe.fragrance_id
        JOIN scent_profiles AS sp ON sp.fragrance_edition_id = fe.id
        WHERE fe.catalog_kind = 'scale-test'
        ORDER BY fe.id
        """
    ).fetchall()


def _catalog_fingerprint(points: list[dict[str, Any]]) -> str:
    fingerprint_input = [
        {
            "id": point["id"],
            "payload": point["payload"],
            "vector_sha256": hashlib.sha256(
                json.dumps(point["vector"], separators=(",", ":")).encode("utf-8")
            ).hexdigest(),
        }
        for point in points
    ]
    return hashlib.sha256(
        json.dumps(fingerprint_input, sort_keys=True, separators=(",", ":")).encode(
            "utf-8"
        )
    ).hexdigest()


def _create_embedding_schema(connection: sqlite3.Connection) -> None:
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS scent_profile_embeddings (
            fragrance_edition_id INTEGER PRIMARY KEY REFERENCES fragrance_editions(id),
            model TEXT NOT NULL,
            model_version TEXT NOT NULL,
            pipeline_version TEXT NOT NULL,
            dimensions INTEGER NOT NULL,
            runtime_device TEXT NOT NULL,
            vector_json TEXT NOT NULL
        )
        """
    )


def _create_comparable_price_schema(connection: sqlite3.Connection) -> None:
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS comparable_prices (
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


def _create_wear_profile_schema(connection: sqlite3.Connection) -> None:
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS wear_profiles (
            fragrance_edition_id INTEGER PRIMARY KEY REFERENCES fragrance_editions(id),
            longevity TEXT,
            projection TEXT
        )
        """
    )


def _record_embedding(
    connection: sqlite3.Connection, row: sqlite3.Row, vector: list[float]
) -> None:
    metadata = _embedding_metadata()
    connection.execute(
        """
        INSERT INTO scent_profile_embeddings
            (fragrance_edition_id, model, model_version, pipeline_version,
             dimensions, runtime_device, vector_json)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT (fragrance_edition_id) DO UPDATE SET
            model = excluded.model,
            model_version = excluded.model_version,
            pipeline_version = excluded.pipeline_version,
            dimensions = excluded.dimensions,
            runtime_device = excluded.runtime_device,
            vector_json = excluded.vector_json
        """,
        (
            row["fragrance_edition_id"],
            metadata["model"],
            metadata["model_version"],
            metadata["pipeline_version"],
            metadata["dimensions"],
            metadata["runtime_device"],
            json.dumps(vector),
        ),
    )


def _embedding_metadata() -> dict[str, Any]:
    return {
        "model": EMBEDDING_MODEL,
        "model_version": EMBEDDING_MODEL_VERSION,
        "pipeline_version": SERIALIZATION_PIPELINE_VERSION,
        "dimensions": EMBEDDING_DIMENSIONS,
        "runtime_device": _embedding_runtime_device(),
    }


def _embedding_runtime_report() -> dict[str, Any]:
    metadata = _embedding_metadata()
    requested_device = _requested_embedding_device()
    if requested_device == "cuda" and metadata["runtime_device"] == "cpu":
        return {
            "status": "fallback",
            **metadata,
            "requested_device": "cuda",
            "message": "CUDA embedding runtime is unavailable; using the practical CPU fallback.",
        }
    if metadata["runtime_device"] == "cuda":
        return {
            "status": "accelerated",
            **metadata,
            "requested_device": requested_device,
            "message": "CUDA embedding runtime is selected for local Scent Profile embeddings.",
        }
    return {"status": "ok", **metadata}


def _embedding_runtime_device() -> str:
    requested_device = _requested_embedding_device()
    if requested_device == "cpu":
        return "cpu"
    if _cuda_embedding_supported():
        return "cuda"
    return "cpu"


def _requested_embedding_device() -> str:
    requested = os.environ.get("NOSEPRINT_EMBEDDING_DEVICE", "auto").strip().casefold()
    if requested in {"cuda", "gpu"}:
        return "cuda"
    if requested == "cpu":
        return "cpu"
    return "auto"


def _cuda_embedding_supported() -> bool:
    supported = os.environ.get("NOSEPRINT_CUDA_SUPPORTED")
    if supported is None:
        return False
    return supported.strip().casefold() in {"1", "true", "yes", "on"}


def _embed_scent_profile(row: sqlite3.Row) -> list[float]:
    vector = [0.0] * EMBEDDING_DIMENSIONS
    for token in _serialize_scent_profile(row):
        bucket = _embedding_bucket(token)
        vector[bucket] += 1.0
    return vector


def _embedding_bucket(token: str) -> int:
    digest = hashlib.sha256(
        f"{EMBEDDING_MODEL_VERSION}:{token}".encode("utf-8")
    ).digest()
    return int.from_bytes(digest[:8], "big") % EMBEDDING_DIMENSIONS


def _serialize_scent_profile(row: sqlite3.Row) -> list[str]:
    tokens: list[str] = []
    tokens.extend(_json_tokens("notes", row["notes_json"]))
    tokens.extend(_json_tokens("main_accord", row["main_accords_json"]))
    tokens.extend(_json_tokens("top_note", row["top_notes_json"]))
    tokens.extend(_json_tokens("middle_note", row["middle_notes_json"]))
    tokens.extend(_json_tokens("base_note", row["base_notes_json"]))
    scent_family = (row["scent_family"] or "").strip().casefold()
    tokens.append(f"scent_family:{scent_family if scent_family else '<unknown>'}")
    return tokens


def _json_tokens(field: str, value: str | None) -> list[str]:
    if value is None:
        return [f"{field}:<unknown>"]
    parsed = json.loads(value)
    if not parsed:
        return [f"{field}:<unknown>"]
    return [f"{field}:{item.strip().casefold()}" for item in parsed if item.strip()]


def _cosine_similarity(left: list[float], right: list[float]) -> float:
    left_norm = math.sqrt(sum(value * value for value in left))
    right_norm = math.sqrt(sum(value * value for value in right))
    if left_norm == 0.0 or right_norm == 0.0:
        return 0.0
    dot_product = sum(
        left_value * right_value for left_value, right_value in zip(left, right)
    )
    return dot_product / (left_norm * right_norm)


def _scent_match_strength(
    reference: sqlite3.Row, candidate: sqlite3.Row, score: float
) -> str:
    if _has_incomplete_profile_comparison_facts(reference) or _has_incomplete_profile_comparison_facts(
        candidate
    ):
        return "incomplete"
    if score >= 0.7 and _scent_families_differ(reference, candidate):
        return "surprising"
    if score >= 0.7:
        return "strong"
    return "weak"


def _has_incomplete_profile_comparison_facts(row: sqlite3.Row) -> bool:
    return (
        _json_or_unknown(row["main_accords_json"]) == "unknown"
        or _json_or_unknown(row["top_notes_json"]) == "unknown"
        or _json_or_unknown(row["middle_notes_json"]) == "unknown"
        or _json_or_unknown(row["base_notes_json"]) == "unknown"
        or _value_or_unknown(row["scent_family"]) == "unknown"
    )


def _profile_comparison(
    reference: sqlite3.Row, candidate: sqlite3.Row
) -> dict[str, Any]:
    return {
        "main_accords": _compare_json_profile_facts(
            reference["main_accords_json"], candidate["main_accords_json"]
        ),
        "note_pyramid": {
            "top": _compare_json_profile_facts(
                reference["top_notes_json"], candidate["top_notes_json"]
            ),
            "middle": _compare_json_profile_facts(
                reference["middle_notes_json"], candidate["middle_notes_json"]
            ),
            "base": _compare_json_profile_facts(
                reference["base_notes_json"], candidate["base_notes_json"]
            ),
        },
        "scent_family": _compare_scent_family(
            reference["scent_family"], candidate["scent_family"]
        ),
    }


def _compare_json_profile_facts(
    reference_value: str | None, candidate_value: str | None
) -> dict[str, list[str] | str]:
    reference_facts = _known_json_profile_facts(reference_value)
    candidate_facts = _known_json_profile_facts(candidate_value)
    if reference_facts is None or candidate_facts is None:
        return {
            "shared": "unknown",
            "reference_only": "unknown",
            "candidate_only": "unknown",
        }
    return {
        "shared": sorted(reference_facts & candidate_facts),
        "reference_only": sorted(reference_facts - candidate_facts),
        "candidate_only": sorted(candidate_facts - reference_facts),
    }


def _known_json_profile_facts(value: str | None) -> set[str] | None:
    parsed = _json_or_unknown(value)
    if parsed == "unknown":
        return None
    return {item.strip().casefold() for item in parsed if item.strip()}


def _compare_scent_family(
    reference_value: str | None, candidate_value: str | None
) -> dict[str, str]:
    reference_family = _known_scent_family(reference_value)
    candidate_family = _known_scent_family(candidate_value)
    if reference_family is None or candidate_family is None:
        return {
            "shared": "unknown",
            "reference_only": "unknown",
            "candidate_only": "unknown",
        }
    if reference_family == candidate_family:
        return {
            "shared": reference_family,
            "reference_only": "unknown",
            "candidate_only": "unknown",
        }
    return {
        "shared": "unknown",
        "reference_only": reference_family,
        "candidate_only": candidate_family,
    }


def _known_scent_family(value: str | None) -> str | None:
    family = _value_or_unknown(value)
    if family == "unknown":
        return None
    return family.strip().casefold()


def _scent_families_differ(reference: sqlite3.Row, candidate: sqlite3.Row) -> bool:
    reference_family = _known_scent_family(reference["scent_family"])
    candidate_family = _known_scent_family(candidate["scent_family"])
    return (
        reference_family is not None
        and candidate_family is not None
        and reference_family != candidate_family
    )


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="noseprint-catalog")
    commands = parser.add_subparsers(dest="command", required=True)

    run = commands.add_parser(
        "run",
        help="Import the Parfumo Real Catalog, rebuild the index, and serve the NosePrint UI",
    )
    run.add_argument(
        "--source",
        help="Path to parfumo_data_clean.csv. Required the first time; re-imports and replaces the Real Catalog when provided.",
    )
    run.add_argument("--database", default=str(DEFAULT_APP_DATABASE))
    run.add_argument("--index", default=str(DEFAULT_APP_INDEX))
    run.add_argument("--host", default="127.0.0.1")
    run.add_argument("--port", type=int, default=8000)
    run.add_argument(
        "--prepare-only",
        action="store_true",
        help="Prepare SQLite and the local ANN index without starting the web server.",
    )
    run.set_defaults(handler=_run_app)

    audit = commands.add_parser("audit", help="Audit a candidate Real Catalog source")
    audit.add_argument("--manifest", required=True)
    audit.add_argument("--source", required=True)
    audit.add_argument("--report", required=True)
    audit.set_defaults(handler=_audit)

    import_catalog = commands.add_parser(
        "import", help="Import an explicitly passing source into the Real Catalog"
    )
    import_catalog.add_argument("--audit-report", required=True)
    import_catalog.add_argument("--source", required=True)
    import_catalog.add_argument("--database", required=True)
    import_catalog.set_defaults(handler=_import_catalog)

    inspect = commands.add_parser("inspect", help="Inspect imported Real Catalog records")
    inspect.add_argument("--database", required=True)
    inspect.set_defaults(handler=_inspect)

    inspect_quarantine = commands.add_parser(
        "inspect-quarantine", help="Inspect rejected and quarantined source rows"
    )
    inspect_quarantine.add_argument("--database", required=True)
    inspect_quarantine.set_defaults(handler=_inspect_quarantine)

    curated_template = commands.add_parser(
        "curated-template",
        help="Print the Curated Batch CSV template for preparing Real Catalog rows",
    )
    curated_template.set_defaults(handler=_curated_template)

    curated_preview = commands.add_parser(
        "curated-preview",
        help="Preview Curated Batch CSV readiness before Real Catalog import",
    )
    curated_preview.add_argument("--source", required=True)
    curated_preview.set_defaults(handler=_curated_preview)

    browse = commands.add_parser(
        "browse", help="Search Real Catalog Fragrance Editions by Fragrance name"
    )
    browse.add_argument("--database", required=True)
    browse.add_argument("--query", required=True)
    browse.add_argument("--page", type=int, default=1)
    browse.add_argument("--per-page", type=int)
    browse.set_defaults(handler=_browse)

    scent_profile = commands.add_parser(
        "scent-profile", help="Inspect a Real Catalog Fragrance Edition Scent Profile"
    )
    scent_profile.add_argument("--database", required=True)
    scent_profile.add_argument("--edition-id", required=True, type=int)
    scent_profile.set_defaults(handler=_scent_profile)

    scent_matches = commands.add_parser(
        "scent-matches",
        help="Find exact-cosine Scent Matches for a Real Catalog Fragrance Edition",
    )
    scent_matches.add_argument("--database", required=True)
    scent_matches.add_argument("--index")
    scent_matches.add_argument("--edition-id", required=True, type=int)
    scent_matches.add_argument("--limit", type=int, default=10)
    scent_matches.add_argument(
        "--show-prices",
        action="store_true",
        help="Include Comparable Price snapshots and cheaper-claim status without filtering matches",
    )
    scent_matches.add_argument(
        "--cheaper-only",
        action="store_true",
        help="Return only Scent Matches with same-size known Comparable Prices below the reference price",
    )
    scent_matches.add_argument(
        "--show-wear-profiles",
        action="store_true",
        help="Include Wear Profile longevity and projection facts without changing Scent Match scores",
    )
    scent_matches.add_argument(
        "--wear-longevity",
        help="Return only alternatives with this known Wear Profile longevity fact",
    )
    scent_matches.add_argument(
        "--wear-projection",
        help="Return only alternatives with this known Wear Profile projection fact",
    )
    scent_matches.set_defaults(handler=_scent_matches)

    scent_request = commands.add_parser(
        "scent-request",
        help="Interpret a beginner Scent Request before searching the Real Catalog",
    )
    scent_request.add_argument("--database", required=True)
    scent_request.add_argument("--wanted", default="")
    scent_request.add_argument("--unwanted", default="")
    scent_request.add_argument("--revise-wanted")
    scent_request.add_argument("--revise-unwanted")
    scent_request.add_argument("--confirm", action="store_true")
    scent_request.add_argument("--cancel", action="store_true")
    scent_request.add_argument("--limit", type=int, default=10)
    scent_request.set_defaults(handler=_scent_request)

    qdrant_health = commands.add_parser(
        "qdrant-health",
        help="Report SQLite, embedding runtime, and Qdrant index health",
    )
    qdrant_health.add_argument("--database", required=True)
    qdrant_health.add_argument("--index", required=True)
    qdrant_health.set_defaults(handler=_qdrant_health)

    rebuild_qdrant = commands.add_parser(
        "rebuild-qdrant-index",
        help="Rebuild the Qdrant ANN index from SQLite Scent Profile embeddings",
    )
    rebuild_qdrant.add_argument("--database", required=True)
    rebuild_qdrant.add_argument("--index", required=True)
    rebuild_qdrant.set_defaults(handler=_rebuild_qdrant_index)

    generate_scale_test = commands.add_parser(
        "generate-scale-test-catalog",
        help="Generate deterministic Scale-Test Catalog records for benchmarks",
    )
    generate_scale_test.add_argument("--database", required=True)
    generate_scale_test.add_argument("--records", type=int, default=10_000)
    generate_scale_test.add_argument("--seed", type=int, default=20260624)
    generate_scale_test.set_defaults(handler=_generate_scale_test_catalog)

    import_parfumo_real = commands.add_parser(
        "import-parfumo",
        help="Import the TidyTuesday Parfumo CSV as the Real Catalog",
    )
    import_parfumo_real.add_argument("--source", required=True)
    import_parfumo_real.add_argument("--database", required=True)
    import_parfumo_real.set_defaults(handler=_import_parfumo_real_catalog)

    benchmark_scale_test = commands.add_parser(
        "benchmark-scale-test-catalog",
        help="Benchmark ANN retrieval against the isolated Scale-Test Catalog",
    )
    benchmark_scale_test.add_argument("--database", required=True)
    benchmark_scale_test.add_argument("--index", required=True)
    benchmark_scale_test.add_argument(
        "--reference-edition-id",
        required=True,
        type=int,
    )
    benchmark_scale_test.add_argument("--limit", type=int, default=10)
    benchmark_scale_test.set_defaults(handler=_benchmark_scale_test_catalog)

    evaluate = commands.add_parser(
        "evaluate-reference-matches",
        help="Evaluate exact and Qdrant ANN retrieval with a Reference Match Set",
    )
    evaluate.add_argument("--database", required=True)
    evaluate.add_argument("--index", required=True)
    evaluate.add_argument("--reference-match-set", required=True)
    evaluate.add_argument("--limit", type=int, default=10)
    evaluate.set_defaults(handler=_evaluate_reference_matches)
    return parser


def main(arguments: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(arguments)
    try:
        return args.handler(args)
    except (
        OSError,
        UnicodeError,
        json.JSONDecodeError,
        csv.Error,
        sqlite3.Error,
        KeyError,
        TypeError,
        ValueError,
    ) as error:
        print(f"Catalog workflow failed: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
