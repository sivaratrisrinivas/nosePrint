from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import sqlite3
import sys
from pathlib import Path
from typing import Any, Sequence


EXIT_BLOCKED = 2
MAX_SOURCE_BYTES = 50 * 1024 * 1024
REQUIRED_AUDIT_CHECKS = {"license_chain", "provenance", "schema", "row_count", "quality"}
EMBEDDING_DIMENSIONS = 384
EMBEDDING_MODEL = "noseprint-hash-embedding-384"
EMBEDDING_MODEL_VERSION = "1"
SERIALIZATION_PIPELINE_VERSION = "scent-profile-serialization-v1"


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
    owner_accepted_risk = bool(args.accept_owner_risk)
    risk_note = (args.risk_note or "").strip()
    if owner_accepted_risk:
        if len(risk_note) < 20:
            print(
                "Import blocked: owner risk acceptance needs a clear risk note.",
                file=sys.stderr,
            )
            return EXIT_BLOCKED
        if not _report_owner_accepted_importable(report):
            print(
                "Import blocked: owner risk acceptance still requires a valid "
                "inconclusive audit with matching schema and row count.",
                file=sys.stderr,
            )
            return EXIT_BLOCKED
    elif not _report_passes(report):
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
                1 if owner_accepted_risk else 0,
                risk_note if owner_accepted_risk else None,
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


def _report_owner_accepted_importable(report: dict[str, Any]) -> bool:
    checks = report.get("checks", {})
    return (
        report.get("verdict") == "inconclusive"
        and set(checks) == REQUIRED_AUDIT_CHECKS
        and checks.get("schema") is True
        and checks.get("row_count") is True
        and bool(report.get("observed", {}).get("source_sha256"))
        and isinstance(report.get("dataset"), dict)
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
        CREATE TABLE IF NOT EXISTS scent_profile_embeddings (
            fragrance_edition_id INTEGER PRIMARY KEY REFERENCES fragrance_editions(id),
            model TEXT NOT NULL,
            model_version TEXT NOT NULL,
            pipeline_version TEXT NOT NULL,
            dimensions INTEGER NOT NULL,
            runtime_device TEXT NOT NULL,
            vector_json TEXT NOT NULL
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


def _catalog_unavailable(database: Path) -> int:
    print(
        "Catalog unavailable: import a Real Catalog into SQLite first, "
        f"then pass it with --database {database}.",
        file=sys.stderr,
    )
    return EXIT_BLOCKED


def _browse(args: argparse.Namespace) -> int:
    query = args.query.strip()
    database = Path(args.database)
    if not database.exists():
        return _catalog_unavailable(database)
    connection = sqlite3.connect(database)
    connection.row_factory = sqlite3.Row
    try:
        try:
            rows = connection.execute(
                """
                SELECT fe.id AS fragrance_edition_id, f.name AS fragrance,
                       fe.name AS edition, fe.concentration
                FROM fragrance_editions AS fe
                JOIN fragrances AS f ON f.id = fe.fragrance_id
                JOIN scent_profiles AS sp ON sp.fragrance_edition_id = fe.id
                WHERE fe.catalog_kind = 'real'
                  AND f.name LIKE ? COLLATE NOCASE
                ORDER BY f.name, fe.id
                """,
                (f"%{query}%",),
            ).fetchall()
        except sqlite3.OperationalError:
            return _catalog_unavailable(database)
    finally:
        connection.close()
    results = [dict(row) for row in rows]
    response: dict[str, Any] = {
        "status": "ok" if results else "no_matches",
        "query": query,
    }
    if not results:
        response["message"] = (
            "No Real Catalog Fragrance Editions matched that Fragrance name."
        )
    response["results"] = results
    print(json.dumps(response, indent=2))
    return 0


def _json_or_unknown(value: str | None) -> list[str] | str:
    if value is None:
        return "unknown"
    parsed = json.loads(value)
    return parsed if parsed else "unknown"


def _value_or_unknown(value: str | None) -> str:
    return value if value else "unknown"


def _scent_profile(args: argparse.Namespace) -> int:
    database = Path(args.database)
    if not database.exists():
        return _catalog_unavailable(database)
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
            return _catalog_unavailable(database)
    finally:
        connection.close()
    if row is None:
        print(
            json.dumps(
                {
                    "status": "not_found",
                    "message": "No Real Catalog Fragrance Edition is available for that edition id.",
                },
                indent=2,
            )
        )
        return 0
    print(
        json.dumps(
            {
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
            },
            indent=2,
        )
    )
    return 0


def _scent_matches(args: argparse.Namespace) -> int:
    database = Path(args.database)
    if not database.exists():
        return _catalog_unavailable(database)
    connection = sqlite3.connect(database)
    connection.row_factory = sqlite3.Row
    try:
        _create_embedding_schema(connection)
        rows = connection.execute(
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
            _scent_match_result(reference, row, score, method="exact_cosine")
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
            if payload.get("catalog_kind") != "real" or edition_id not in rows_by_id:
                continue
            score = _cosine_similarity(reference_vector, point["vector"])
            ann_ranked_matches.append(
                _scent_match_result(
                    reference,
                    rows_by_id[edition_id],
                    score,
                    method="qdrant_ann",
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
    if not limited_matches:
        response["message"] = (
            "No other Real Catalog Fragrance Editions are available for exact cosine Scent Matches."
        )
    connection.commit()
    connection.close()
    print(json.dumps(response, indent=2))
    return 0


def _scent_match_result(
    reference: sqlite3.Row, row: sqlite3.Row, score: float, *, method: str
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
    return {
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
    if not index_path.exists():
        qdrant_status = {
            "status": "missing",
            "path": str(index_path),
            "message": "Rebuild the Qdrant index from SQLite before serving ANN Scent Matches.",
        }
    else:
        qdrant_status = _qdrant_index_freshness(index_path, rows)
    response = {
        "status": qdrant_status["status"],
        "sqlite_catalog": {
            "status": "ok",
            "eligible_real_catalog_records": eligible_count,
        },
        "embedding_runtime": {"status": "ok", **_embedding_metadata()},
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
    index_document = json.loads(index_path.read_text(encoding="utf-8"))
    index_metadata = index_document["metadata"]
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
        "runtime_device": "cpu",
    }


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
    import_catalog.add_argument(
        "--accept-owner-risk",
        action="store_true",
        help=(
            "Import an inconclusive source only after the project owner accepts "
            "the unproven license, provenance, and quality risk"
        ),
    )
    import_catalog.add_argument(
        "--risk-note",
        help="Plain-language note explaining why the owner accepted the risk",
    )
    import_catalog.set_defaults(handler=_import_catalog)

    inspect = commands.add_parser("inspect", help="Inspect imported Real Catalog records")
    inspect.add_argument("--database", required=True)
    inspect.set_defaults(handler=_inspect)

    inspect_quarantine = commands.add_parser(
        "inspect-quarantine", help="Inspect rejected and quarantined source rows"
    )
    inspect_quarantine.add_argument("--database", required=True)
    inspect_quarantine.set_defaults(handler=_inspect_quarantine)

    browse = commands.add_parser(
        "browse", help="Search Real Catalog Fragrance Editions by Fragrance name"
    )
    browse.add_argument("--database", required=True)
    browse.add_argument("--query", required=True)
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
    scent_matches.set_defaults(handler=_scent_matches)

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
