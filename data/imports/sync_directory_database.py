"""Synchronize the reviewed gym fixture with the trusted Postgres database.

The database URL is read only from an environment variable so credentials do
not enter shell history, process arguments, generated JSON, or Git commits.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_SOURCE = ROOT / "data" / "imports" / "sf-gyms-osm.json"
DEFAULT_OUTPUTS = (
    ROOT / "data" / "imports" / "sf-gyms-osm.json",
    ROOT / "apps" / "web" / "lib" / "sf-gyms-osm.json",
)
DEFAULT_MIGRATION = ROOT / "supabase" / "migrations" / "0005_directory_snapshots.sql"
HASH_RE = re.compile(r"^[0-9a-f]{64}$")
ALLOWED_PUBLICATION_STATUSES = {"publish", "suppress-alias", "review-hold"}
ALLOWED_PRICING_STATUSES = {
    "verified",
    "estimated",
    "free",
    "pay-per-visit",
    "not-applicable",
    "gated",
    "unresolved",
}
FORBIDDEN_SECRET_KEYS = {
    "access_token",
    "api_key",
    "client_secret",
    "database_url",
    "password",
    "refresh_token",
    "service_role_key",
    "supabase_service_role_key",
}


class FixtureValidationError(ValueError):
    """Raised when a fixture cannot be safely synchronized."""


@dataclass(frozen=True)
class PreparedRecord:
    canonical_location_id: str
    source_id: str
    ordinal: int
    name: str
    operator_id: str | None
    publication_status: str
    pricing_status: str
    record_hash: str
    payload_json: str


@dataclass(frozen=True)
class PreparedFixture:
    metadata: dict[str, Any]
    metadata_json: str
    records: tuple[PreparedRecord, ...]
    source_hash: str


def _canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True)


def _sha256(value: Any) -> str:
    return hashlib.sha256(_canonical_json(value).encode("utf-8")).hexdigest()


def _find_forbidden_secret_key(value: Any, path: str = "$") -> str | None:
    if isinstance(value, dict):
        for raw_key, child in value.items():
            key = str(raw_key).strip().lower()
            if key in FORBIDDEN_SECRET_KEYS:
                return f"{path}.{raw_key}"
            found = _find_forbidden_secret_key(child, f"{path}.{raw_key}")
            if found:
                return found
    elif isinstance(value, list):
        for index, child in enumerate(value):
            found = _find_forbidden_secret_key(child, f"{path}[{index}]")
            if found:
                return found
    return None


def prepare_fixture(raw: dict[str, Any]) -> PreparedFixture:
    metadata = raw.get("_meta")
    gyms = raw.get("gyms")
    if not isinstance(metadata, dict):
        raise FixtureValidationError("Fixture _meta must be an object")
    if not isinstance(gyms, list):
        raise FixtureValidationError("Fixture gyms must be an array")

    secret_path = _find_forbidden_secret_key(raw)
    if secret_path:
        raise FixtureValidationError(f"Secret-shaped field is forbidden at {secret_path}")

    prepared: list[PreparedRecord] = []
    canonical_ids: set[str] = set()
    source_ids: set[str] = set()
    for ordinal, gym in enumerate(gyms):
        if not isinstance(gym, dict):
            raise FixtureValidationError(f"Gym at index {ordinal} must be an object")
        source_id = str(gym.get("id") or "").strip()
        canonical_id = str(gym.get("canonicalLocationId") or source_id).strip()
        name = str(gym.get("name") or "").strip()
        publication_status = str(gym.get("publicationStatus") or "").strip()
        pricing_status = str(gym.get("pricingStatus") or "").strip()
        operator_id_value = gym.get("operatorId")
        operator_id = str(operator_id_value).strip() if operator_id_value else None

        if not source_id or not canonical_id or not name:
            raise FixtureValidationError(
                f"Gym at index {ordinal} is missing id, canonicalLocationId, or name"
            )
        if canonical_id in canonical_ids:
            raise FixtureValidationError(f"Duplicate canonicalLocationId: {canonical_id}")
        if source_id in source_ids:
            raise FixtureValidationError(f"Duplicate source id: {source_id}")
        if publication_status not in ALLOWED_PUBLICATION_STATUSES:
            raise FixtureValidationError(
                f"Invalid publicationStatus for {source_id}: {publication_status}"
            )
        if pricing_status not in ALLOWED_PRICING_STATUSES:
            raise FixtureValidationError(f"Invalid pricingStatus for {source_id}: {pricing_status}")

        payload_json = _canonical_json(gym)
        record_hash = hashlib.sha256(payload_json.encode("utf-8")).hexdigest()
        if not HASH_RE.fullmatch(record_hash):
            raise FixtureValidationError(f"Invalid record hash generated for {source_id}")
        prepared.append(
            PreparedRecord(
                canonical_location_id=canonical_id,
                source_id=source_id,
                ordinal=ordinal,
                name=name,
                operator_id=operator_id,
                publication_status=publication_status,
                pricing_status=pricing_status,
                record_hash=record_hash,
                payload_json=payload_json,
            )
        )
        canonical_ids.add(canonical_id)
        source_ids.add(source_id)

    source_hash = _sha256(raw)
    return PreparedFixture(
        metadata=metadata,
        metadata_json=_canonical_json(metadata),
        records=tuple(prepared),
        source_hash=source_hash,
    )


def load_fixture(path: Path) -> PreparedFixture:
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise FixtureValidationError("Fixture root must be an object")
    return prepare_fixture(raw)


def render_fixture(metadata: dict[str, Any], payloads: list[dict[str, Any]]) -> str:
    return json.dumps(
        {"_meta": metadata, "gyms": payloads},
        ensure_ascii=False,
        indent=2,
    ) + "\n"


def _database_url(env_name: str) -> str:
    value = os.environ.get(env_name, "").strip()
    if not value:
        raise RuntimeError(
            f"{env_name} is not configured. Store the Postgres connection string only in "
            "a local environment variable or encrypted GitHub secret."
        )
    if not value.startswith(("postgresql://", "postgres://")):
        raise RuntimeError(f"{env_name} must be a Postgres connection string")
    return value


async def _connect(database_url: str):
    try:
        import asyncpg
    except ImportError as exc:  # pragma: no cover - depends on local environment
        raise RuntimeError("Install the project dependencies to use database sync") from exc
    return await asyncpg.connect(database_url, command_timeout=120)


async def apply_migration(connection: Any, migration_path: Path) -> None:
    sql = migration_path.read_text(encoding="utf-8")
    async with connection.transaction():
        await connection.execute(sql)


async def push_fixture(connection: Any, fixture: PreparedFixture) -> str:
    async with connection.transaction():
        run_id = await connection.fetchval(
            """
            insert into public.gym_directory_sync_runs (
              source_hash, source_record_count, metadata, status
            ) values ($1, $2, $3::jsonb, 'running')
            returning id::text
            """,
            fixture.source_hash,
            len(fixture.records),
            fixture.metadata_json,
        )
        for record in fixture.records:
            await connection.execute(
                """
                insert into public.gym_directory_records (
                  canonical_location_id, source_id, ordinal, name, operator_id,
                  publication_status, pricing_status, record_hash, payload,
                  sync_run_id, synced_at
                ) values ($1, $2, $3, $4, $5, $6, $7, $8, $9::jsonb, $10::uuid,
                          timezone('utc', now()))
                on conflict (canonical_location_id) do update set
                  source_id = excluded.source_id,
                  ordinal = excluded.ordinal,
                  name = excluded.name,
                  operator_id = excluded.operator_id,
                  publication_status = excluded.publication_status,
                  pricing_status = excluded.pricing_status,
                  record_hash = excluded.record_hash,
                  payload = excluded.payload,
                  sync_run_id = excluded.sync_run_id,
                  synced_at = excluded.synced_at
                """,
                record.canonical_location_id,
                record.source_id,
                record.ordinal,
                record.name,
                record.operator_id,
                record.publication_status,
                record.pricing_status,
                record.record_hash,
                record.payload_json,
                run_id,
            )
        await connection.execute(
            "delete from public.gym_directory_records where sync_run_id <> $1::uuid",
            run_id,
        )
        stored_count = await connection.fetchval(
            "select count(*) from public.gym_directory_records where sync_run_id = $1::uuid",
            run_id,
        )
        if stored_count != len(fixture.records):
            raise RuntimeError(
                f"Database record count mismatch: expected {len(fixture.records)}, got {stored_count}"
            )
        await connection.execute(
            """
            update public.gym_directory_sync_runs
            set status = 'complete', completed_at = timezone('utc', now())
            where id = $1::uuid
            """,
            run_id,
        )
    return run_id


async def pull_fixture(connection: Any) -> tuple[dict[str, Any], list[dict[str, Any]], str]:
    run = await connection.fetchrow(
        """
        select id::text, source_hash, source_record_count, metadata
        from public.gym_directory_sync_runs
        where status = 'complete'
        order by completed_at desc, started_at desc
        limit 1
        """
    )
    if run is None:
        raise RuntimeError("Database has no completed gym directory sync")
    rows = await connection.fetch(
        """
        select payload
        from public.gym_directory_records
        where sync_run_id = $1::uuid
        order by ordinal, canonical_location_id
        """,
        run["id"],
    )
    if len(rows) != run["source_record_count"]:
        raise RuntimeError(
            "Database snapshot is incomplete: completed run count does not match current records"
        )
    metadata = run["metadata"]
    if isinstance(metadata, str):
        metadata = json.loads(metadata)
    payloads: list[dict[str, Any]] = []
    for row in rows:
        payload = row["payload"]
        if isinstance(payload, str):
            payload = json.loads(payload)
        payloads.append(payload)
    pulled = {"_meta": metadata, "gyms": payloads}
    pulled_hash = _sha256(pulled)
    if pulled_hash != run["source_hash"]:
        raise RuntimeError("Database snapshot hash does not match the completed sync run")
    return metadata, payloads, pulled_hash


def write_outputs(metadata: dict[str, Any], payloads: list[dict[str, Any]], outputs: list[Path]) -> None:
    rendered = render_fixture(metadata, payloads)
    for output in outputs:
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(rendered, encoding="utf-8", newline="\n")


async def run(args: argparse.Namespace) -> int:
    if args.command == "verify":
        fixture = load_fixture(args.source)
        print(f"Verified {len(fixture.records)} records; sha256={fixture.source_hash}")
        return 0

    database_url = _database_url(args.database_url_env)
    connection = await _connect(database_url)
    try:
        if args.apply_migration:
            await apply_migration(connection, args.migration)

        if args.command in {"push", "roundtrip"}:
            fixture = load_fixture(args.source)
            run_id = await push_fixture(connection, fixture)
            print(
                f"Database sync complete: {len(fixture.records)} records, "
                f"run={run_id}, sha256={fixture.source_hash}"
            )

        if args.command in {"pull", "roundtrip"}:
            metadata, payloads, source_hash = await pull_fixture(connection)
            write_outputs(metadata, payloads, args.output)
            print(
                f"Database export complete: {len(payloads)} records, "
                f"sha256={source_hash}, outputs={len(args.output)}"
            )
    finally:
        await connection.close()
    return 0


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description=__doc__)
    result.add_argument("command", choices=("verify", "push", "pull", "roundtrip"))
    result.add_argument("--source", type=Path, default=DEFAULT_SOURCE)
    result.add_argument(
        "--output",
        action="append",
        type=Path,
        default=None,
        help="Pull destination; repeat for multiple outputs (defaults to source and web fixtures)",
    )
    result.add_argument(
        "--database-url-env",
        default="DATABASE_URL",
        help="Name of the environment variable containing the Postgres URL",
    )
    result.add_argument("--apply-migration", action="store_true")
    result.add_argument("--migration", type=Path, default=DEFAULT_MIGRATION)
    return result


def main() -> int:
    args = parser().parse_args()
    if args.output is None:
        args.output = list(DEFAULT_OUTPUTS)
    try:
        return asyncio.run(run(args))
    except (FixtureValidationError, RuntimeError, OSError, json.JSONDecodeError) as exc:
        print(f"Database sync failed: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
