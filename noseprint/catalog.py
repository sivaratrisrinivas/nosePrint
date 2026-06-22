from __future__ import annotations

import argparse
import csv
import hashlib
import json
import sqlite3
import sys
from pathlib import Path
from typing import Any, Sequence


EXIT_BLOCKED = 2
MAX_SOURCE_BYTES = 50 * 1024 * 1024
REQUIRED_AUDIT_CHECKS = {"license_chain", "provenance", "schema", "row_count", "quality"}


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
                dataset["id"],
                dataset["download_url"],
                dataset["publisher"],
                dataset["claimed_license"],
                json.dumps(report, sort_keys=True),
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
            audit_report_json TEXT NOT NULL
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
            catalog_kind TEXT NOT NULL CHECK (catalog_kind = 'real'),
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
    connection: sqlite3.Connection, *, fragrance_id: int, edition_name: str
) -> int:
    connection.execute(
        """
        INSERT OR IGNORE INTO fragrance_editions
            (fragrance_id, name, concentration, catalog_kind)
        VALUES (?, ?, NULL, 'real')
        """,
        (fragrance_id, edition_name),
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
        catalog.append(
            {
                "brand": row["brand"],
                "fragrance": row["fragrance"],
                "edition": row["edition"],
                "concentration": row["concentration"],
                "notes": json.loads(row["notes_json"]),
                "source_dataset": row["dataset_id"],
                "source_row": row["source_row"],
                "original_name": original["Name"],
            }
        )
    print(json.dumps(catalog, indent=2))
    return 0


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


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="noseprint-catalog")
    commands = parser.add_subparsers(dest="command", required=True)

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
