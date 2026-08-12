"""EPDK snapshot normalization and first-release compatibility projection.

The module is deliberately network-free. Acquisition is a separate operational step; callers
provide an already captured response plus immutable provenance.
"""

from __future__ import annotations

import hashlib
import json
import math
from collections import defaultdict
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Literal
from urllib.parse import urlsplit

from chargepath.models import Station

EPDK_SOURCE_NAME = "EPDK charging-station service"
EPDK_SOURCE_URL = "https://apigateway.epdk.gov.tr/sarjIstasyonlari"

EXPECTED_ROOT_FIELDS = frozenset(
    {
        "statusCode",
        "statusDescription",
        "message",
        "columnNames",
        "numRows",
        "result",
        "elapsedTime",
        "errors",
        "data",
    }
)
EXPECTED_SITE_FIELDS = frozenset(
    {
        "sarjIstasyonuNo",
        "sarjIstasyonuAdi",
        "yesilSarjIstasyonuMu",
        "hizmetSekli",
        "sarjAgiIsletmecisiUnvan",
        "sarjAgiIsletmecisiLisansNo",
        "sarjIstasyonuIsletmecisi",
        "marka",
        "olumluGorusVerenDagitimSirketiLisansNo",
        "olumluGorusVerenDagitimSirketiLisansUnvani",
        "dagitimSirketiOlumluGorusBelgeNumarasi",
        "soketler",
        "adres",
        "enlem",
        "boylam",
    }
)
EXPECTED_SOCKET_FIELDS = frozenset({"soketNo", "soketTipi", "soketTuru", "soketGucu"})


class EpdkSchemaDriftError(ValueError):
    """Raised when the response envelope no longer matches the reviewed observed schema."""


@dataclass(frozen=True, slots=True)
class SnapshotProvenance:
    """Immutable provenance copied onto every normalized site and socket."""

    snapshot_id: str
    source_name: str
    source_url: str
    retrieved_at: str
    response_sha256: str
    reuse_status: Literal["pending_verification", "approved", "prohibited"]
    source_freshness: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.snapshot_id, str) or not self.snapshot_id.strip():
            raise ValueError("snapshot_id must not be empty")
        if not isinstance(self.source_name, str) or not self.source_name.strip():
            raise ValueError("source name and HTTPS URL are required")
        if not isinstance(self.source_url, str):
            raise ValueError("source name and HTTPS URL are required")
        parsed_url = urlsplit(self.source_url)
        if (
            parsed_url.scheme != "https"
            or not parsed_url.hostname
            or parsed_url.username is not None
            or parsed_url.password is not None
            or parsed_url.fragment
        ):
            raise ValueError("source name and HTTPS URL are required")
        if not isinstance(self.retrieved_at, str) or not self.retrieved_at.endswith("Z"):
            raise ValueError("retrieved_at must be an explicit UTC timestamp ending in Z")
        try:
            retrieved_at = datetime.fromisoformat(self.retrieved_at[:-1] + "+00:00")
        except ValueError as error:
            raise ValueError("retrieved_at must be a valid UTC timestamp") from error
        if retrieved_at.tzinfo != UTC:
            raise ValueError("retrieved_at must be a valid UTC timestamp")
        if not isinstance(self.response_sha256, str):
            raise ValueError("response_sha256 must be a lowercase SHA-256 digest")
        checksum = self.response_sha256.lower()
        if len(checksum) != 64 or any(
            character not in "0123456789abcdef" for character in checksum
        ):
            raise ValueError("response_sha256 must be a lowercase SHA-256 digest")
        if self.reuse_status not in {"pending_verification", "approved", "prohibited"}:
            raise ValueError("reuse_status is unsupported")
        if self.source_freshness is not None and (
            not isinstance(self.source_freshness, str) or not self.source_freshness.strip()
        ):
            raise ValueError("source_freshness must be non-empty text when supplied")
        object.__setattr__(self, "response_sha256", checksum)


@dataclass(frozen=True, slots=True)
class NormalizedSite:
    source_id: str
    name: str
    operator: str
    latitude: float
    longitude: float
    access: Literal["public", "private"]
    provenance: SnapshotProvenance

    def __post_init__(self) -> None:
        if any(
            not isinstance(value, str) or not value.strip()
            for value in (self.source_id, self.name, self.operator)
        ):
            raise ValueError("normalized site identifiers and text must not be empty")
        if (
            isinstance(self.latitude, bool)
            or not isinstance(self.latitude, (int, float))
            or not math.isfinite(self.latitude)
            or not -90 <= self.latitude <= 90
            or isinstance(self.longitude, bool)
            or not isinstance(self.longitude, (int, float))
            or not math.isfinite(self.longitude)
            or not -180 <= self.longitude <= 180
        ):
            raise ValueError("normalized site coordinates must be finite WGS84 values")
        if self.access not in {"public", "private"}:
            raise ValueError("normalized site access is unsupported")
        if not isinstance(self.provenance, SnapshotProvenance):
            raise ValueError("normalized site provenance is required")


@dataclass(frozen=True, slots=True)
class NormalizedSocket:
    source_id: str
    site_source_id: str
    current_type: Literal["AC", "DC"]
    connector_type: Literal["TYPE2", "CCS2", "CHADEMO"]
    max_power_kw: float
    provenance: SnapshotProvenance

    def __post_init__(self) -> None:
        if any(
            not isinstance(value, str) or not value.strip()
            for value in (self.source_id, self.site_source_id)
        ):
            raise ValueError("normalized socket identifiers must not be empty")
        if self.current_type not in {"AC", "DC"}:
            raise ValueError("normalized socket current type is unsupported")
        if self.connector_type not in {"TYPE2", "CCS2", "CHADEMO"}:
            raise ValueError("normalized socket connector type is unsupported")
        if (self.current_type == "AC") != (self.connector_type == "TYPE2"):
            raise ValueError("normalized socket current and connector types are inconsistent")
        if (
            isinstance(self.max_power_kw, bool)
            or not isinstance(self.max_power_kw, (int, float))
            or not math.isfinite(self.max_power_kw)
            or self.max_power_kw <= 0
        ):
            raise ValueError("normalized socket power must be finite and positive")
        if not isinstance(self.provenance, SnapshotProvenance):
            raise ValueError("normalized socket provenance is required")

    @property
    def stable_id(self) -> str:
        return f"{self.site_source_id}:{self.source_id}"


@dataclass(frozen=True, slots=True)
class ValidationIssue:
    entity: Literal["site", "socket"]
    source_id: str
    reason: str

    def __post_init__(self) -> None:
        if self.entity not in {"site", "socket"}:
            raise ValueError("validation issue entity is unsupported")
        if any(
            not isinstance(value, str) or not value.strip()
            for value in (self.source_id, self.reason)
        ):
            raise ValueError("validation issue source and reason must not be empty")


@dataclass(frozen=True, slots=True)
class ReconciliationCounts:
    input: int
    accepted: int
    rejected: int
    duplicates: int

    def __post_init__(self) -> None:
        if any(
            isinstance(value, bool) or not isinstance(value, int)
            for value in (self.input, self.accepted, self.rejected, self.duplicates)
        ):
            raise ValueError("reconciliation counts must be integers")
        if min(self.input, self.accepted, self.rejected, self.duplicates) < 0:
            raise ValueError("reconciliation counts must not be negative")
        if self.input != self.accepted + self.rejected + self.duplicates:
            raise ValueError("input must equal accepted plus rejected plus duplicates")


@dataclass(frozen=True, slots=True)
class StationValidationReport:
    snapshot_id: str
    sites: ReconciliationCounts
    sockets: ReconciliationCounts
    issues: tuple[ValidationIssue, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.snapshot_id, str) or not self.snapshot_id.strip():
            raise ValueError("validation report snapshot_id must not be empty")
        if not isinstance(self.sites, ReconciliationCounts) or not isinstance(
            self.sockets, ReconciliationCounts
        ):
            raise ValueError("validation report reconciliation counts are required")
        if not isinstance(self.issues, tuple) or any(
            not isinstance(issue, ValidationIssue) for issue in self.issues
        ):
            raise ValueError("validation report issues must be ValidationIssue values")
        expected_issue_count = (
            self.sites.rejected
            + self.sites.duplicates
            + self.sockets.rejected
            + self.sockets.duplicates
        )
        if len(self.issues) != expected_issue_count:
            raise ValueError("validation report issue count does not match reconciliation")

    def to_dict(self) -> dict[str, Any]:
        return {
            "snapshot_id": self.snapshot_id,
            "sites": _counts_dict(self.sites),
            "sockets": _counts_dict(self.sockets),
            "issues": [
                {"entity": issue.entity, "source_id": issue.source_id, "reason": issue.reason}
                for issue in self.issues
            ],
        }


@dataclass(frozen=True, slots=True)
class NormalizationResult:
    sites: tuple[NormalizedSite, ...]
    sockets: tuple[NormalizedSocket, ...]
    report: StationValidationReport


@dataclass(frozen=True, slots=True)
class CompatibleStationOption:
    """One optimizer node derived from one or more compatible sockets at a site."""

    station: Station
    site_source_id: str
    socket_source_ids: tuple[str, ...]
    provenance: SnapshotProvenance

    def __post_init__(self) -> None:
        if not isinstance(self.station, Station):
            raise ValueError("compatible option station must be a Station")
        if not isinstance(self.site_source_id, str) or not self.site_source_id.strip():
            raise ValueError("compatible option site_source_id must not be empty")
        if (
            not isinstance(self.socket_source_ids, tuple)
            or not self.socket_source_ids
            or any(
                not isinstance(socket_id, str) or not socket_id.strip()
                for socket_id in self.socket_source_ids
            )
            or len(set(self.socket_source_ids)) != len(self.socket_source_ids)
        ):
            raise ValueError("compatible option socket ids must be unique non-empty text")
        if not isinstance(self.provenance, SnapshotProvenance):
            raise ValueError("compatible option provenance is required")


@dataclass(frozen=True, slots=True)
class SnapshotManifest:
    manifest_version: int
    snapshot_id: str
    source: str
    source_url: str
    request_fingerprint_sha256: str
    retrieved_at: str
    response_status: int
    response_sha256: str
    response_bytes: int
    source_record_count: int
    source_socket_count: int
    source_schema_version: str
    transformation_version: str
    reuse_status: str
    source_freshness: str
    quota_status: str
    reconciliation: dict[str, dict[str, int]]

    def to_dict(self) -> dict[str, Any]:
        return {
            "manifest_version": self.manifest_version,
            "snapshot_id": self.snapshot_id,
            "source": self.source,
            "source_url": self.source_url,
            "request_fingerprint_sha256": self.request_fingerprint_sha256,
            "retrieved_at": self.retrieved_at,
            "response_status": self.response_status,
            "response_sha256": self.response_sha256,
            "response_bytes": self.response_bytes,
            "source_record_count": self.source_record_count,
            "source_socket_count": self.source_socket_count,
            "source_schema_version": self.source_schema_version,
            "transformation_version": self.transformation_version,
            "reuse_status": self.reuse_status,
            "source_freshness": self.source_freshness,
            "quota_status": self.quota_status,
            "reconciliation": self.reconciliation,
        }


def request_fingerprint(*, method: str, url: str, headers: Mapping[str, str], body: str) -> str:
    """Hash the canonical, non-secret request contract used for one acquisition."""

    canonical = json.dumps(
        {
            "method": method.upper(),
            "url": url,
            "headers": {key.lower(): headers[key] for key in sorted(headers, key=str.lower)},
            "body": body,
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def build_snapshot_manifest(
    *,
    raw_response: bytes,
    payload: Mapping[str, Any],
    provenance: SnapshotProvenance,
    report: StationValidationReport,
    response_status: int,
) -> SnapshotManifest:
    """Build metadata-only evidence for a raw snapshot that remains outside Git."""

    if report.snapshot_id != provenance.snapshot_id:
        raise ValueError("validation report snapshot does not match provenance")
    if (
        isinstance(response_status, bool)
        or not isinstance(response_status, int)
        or not 100 <= response_status <= 599
    ):
        raise ValueError("response_status must be a valid HTTP status code")
    try:
        decoded_response = json.loads(raw_response)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ValueError("raw snapshot must contain valid JSON") from error
    if decoded_response != payload:
        raise ValueError("raw snapshot content does not match the supplied payload")
    data = payload.get("data")
    if not isinstance(data, list):
        raise ValueError("snapshot payload data must be a list")
    actual_checksum = hashlib.sha256(raw_response).hexdigest()
    if actual_checksum != provenance.response_sha256:
        raise ValueError("raw response checksum does not match snapshot provenance")
    socket_count = sum(
        len(row.get("soketler", []))
        for row in data
        if isinstance(row, Mapping) and isinstance(row.get("soketler"), list)
    )
    if report.sites.input != len(data) or report.sockets.input != socket_count:
        raise ValueError("validation report input counts do not match the snapshot payload")
    payload_status = payload.get("statusCode")
    if payload_status != response_status:
        raise ValueError("HTTP response status does not match the snapshot payload")
    return SnapshotManifest(
        manifest_version=1,
        snapshot_id=provenance.snapshot_id,
        source=provenance.source_name,
        source_url=provenance.source_url,
        request_fingerprint_sha256=request_fingerprint(
            method="GET",
            url=provenance.source_url,
            headers={"Accept": "application/json", "Content-Type": "application/json"},
            body="{}",
        ),
        retrieved_at=provenance.retrieved_at,
        response_status=response_status,
        response_sha256=actual_checksum,
        response_bytes=len(raw_response),
        source_record_count=len(data),
        source_socket_count=socket_count,
        source_schema_version="epdk-observed-2026-08-09-v1",
        transformation_version="chargepath-normalize-v1",
        reuse_status=provenance.reuse_status,
        source_freshness=provenance.source_freshness or "unknown_not_supplied_by_response",
        quota_status="unknown_not_documented",
        reconciliation={
            "sites": _counts_dict(report.sites),
            "sockets": _counts_dict(report.sockets),
        },
    )


def normalize_epdk_response(
    payload: Mapping[str, Any], provenance: SnapshotProvenance
) -> NormalizationResult:
    """Validate the observed EPDK envelope and normalize static site/socket records."""

    _require_exact_fields(payload, EXPECTED_ROOT_FIELDS, "root")
    data = payload["data"]
    if not isinstance(data, list):
        raise EpdkSchemaDriftError("root.data must be a list")
    if payload["statusCode"] != 200 or payload["statusDescription"] != "OK":
        raise EpdkSchemaDriftError("response envelope does not report a successful result")
    if payload["errors"] != []:
        raise EpdkSchemaDriftError("response envelope contains provider errors")
    if payload["numRows"] != len(data):
        raise EpdkSchemaDriftError("root.numRows must equal the number of data rows")
    columns = payload["columnNames"]
    if (
        not isinstance(columns, list)
        or len(columns) != len(EXPECTED_SITE_FIELDS)
        or set(columns) != EXPECTED_SITE_FIELDS
    ):
        raise EpdkSchemaDriftError("root.columnNames no longer matches the reviewed site fields")

    sites: list[NormalizedSite] = []
    sockets: list[NormalizedSocket] = []
    issues: list[ValidationIssue] = []
    seen_sites: set[str] = set()
    seen_sockets: set[tuple[str, str]] = set()
    site_rejected = 0
    site_duplicates = 0
    socket_input = 0
    socket_rejected = 0
    socket_duplicates = 0

    for index, raw_site in enumerate(data):
        row_label = f"row[{index}]"
        if not isinstance(raw_site, Mapping):
            site_rejected += 1
            issues.append(ValidationIssue("site", row_label, "site_not_an_object"))
            continue
        unknown_site_fields = set(raw_site) - EXPECTED_SITE_FIELDS
        if unknown_site_fields:
            raise EpdkSchemaDriftError(
                f"{row_label} has unreviewed fields: {sorted(unknown_site_fields)}"
            )

        raw_sockets = raw_site.get("soketler")
        if isinstance(raw_sockets, list):
            socket_input += len(raw_sockets)
        site_id = _nonempty_text(raw_site.get("sarjIstasyonuNo"))
        issue_id = site_id or row_label
        site_problem = _site_problem(raw_site)
        if site_problem is not None:
            site_rejected += 1
            issues.append(ValidationIssue("site", issue_id, site_problem))
            if isinstance(raw_sockets, list):
                socket_rejected += len(raw_sockets)
                issues.extend(
                    ValidationIssue(
                        "socket",
                        f"{issue_id}:{_socket_issue_id(socket, socket_index)}",
                        "parent_site_rejected",
                    )
                    for socket_index, socket in enumerate(raw_sockets)
                )
            continue
        assert site_id is not None
        if site_id in seen_sites:
            site_duplicates += 1
            issues.append(ValidationIssue("site", site_id, "duplicate_site_source_id"))
            if isinstance(raw_sockets, list):
                socket_duplicates += len(raw_sockets)
                issues.extend(
                    ValidationIssue(
                        "socket",
                        f"{site_id}:{_socket_issue_id(socket, socket_index)}",
                        "duplicate_parent_site_source_id",
                    )
                    for socket_index, socket in enumerate(raw_sockets)
                )
            continue

        seen_sites.add(site_id)
        site = NormalizedSite(
            source_id=site_id,
            name=_required_text(raw_site["sarjIstasyonuAdi"]),
            operator=_required_text(raw_site["sarjAgiIsletmecisiUnvan"]),
            latitude=_finite_number(raw_site["enlem"]),
            longitude=_finite_number(raw_site["boylam"]),
            access="public" if raw_site["hizmetSekli"] == "HALKA_ACIK" else "private",
            provenance=provenance,
        )
        sites.append(site)

        assert isinstance(raw_sockets, list)
        for socket_index, raw_socket in enumerate(raw_sockets):
            socket_label = f"{site_id}:{_socket_issue_id(raw_socket, socket_index)}"
            if not isinstance(raw_socket, Mapping):
                socket_rejected += 1
                issues.append(ValidationIssue("socket", socket_label, "socket_not_an_object"))
                continue
            unknown_socket_fields = set(raw_socket) - EXPECTED_SOCKET_FIELDS
            if unknown_socket_fields:
                raise EpdkSchemaDriftError(
                    f"{socket_label} has unreviewed fields: {sorted(unknown_socket_fields)}"
                )
            socket_problem = _socket_problem(raw_socket)
            if socket_problem is not None:
                socket_rejected += 1
                issues.append(ValidationIssue("socket", socket_label, socket_problem))
                continue
            socket_id = _required_text(raw_socket["soketNo"])
            identity = (site_id, socket_id)
            if identity in seen_sockets:
                socket_duplicates += 1
                issues.append(ValidationIssue("socket", socket_label, "duplicate_socket_source_id"))
                continue
            seen_sockets.add(identity)
            connector = {
                "AC_TYPE2": "TYPE2",
                "DC_CCS": "CCS2",
                "DC_CHADEMO": "CHADEMO",
            }[_required_text(raw_socket["soketTuru"])]
            sockets.append(
                NormalizedSocket(
                    source_id=socket_id,
                    site_source_id=site_id,
                    current_type=_required_text(raw_socket["soketTipi"]),  # type: ignore[arg-type]
                    connector_type=connector,  # type: ignore[arg-type]
                    max_power_kw=_positive_power(raw_socket["soketGucu"]),
                    provenance=provenance,
                )
            )

    sites.sort(key=lambda site: site.source_id)
    sockets.sort(key=lambda socket: (socket.site_source_id, socket.source_id))
    report = StationValidationReport(
        snapshot_id=provenance.snapshot_id,
        sites=ReconciliationCounts(len(data), len(sites), site_rejected, site_duplicates),
        sockets=ReconciliationCounts(
            socket_input, len(sockets), socket_rejected, socket_duplicates
        ),
        issues=tuple(issues),
    )
    return NormalizationResult(tuple(sites), tuple(sockets), report)


def project_ccs2_dc_options(result: NormalizationResult) -> tuple[CompatibleStationOption, ...]:
    """Project static compatible CCS2 DC sockets into one optimizer node per site."""

    sites = {site.source_id: site for site in result.sites}
    compatible: dict[str, list[NormalizedSocket]] = defaultdict(list)
    for socket in result.sockets:
        if socket.current_type == "DC" and socket.connector_type == "CCS2":
            compatible[socket.site_source_id].append(socket)

    options: list[CompatibleStationOption] = []
    for site_id in sorted(compatible):
        site = sites.get(site_id)
        if site is None:
            raise ValueError("compatible socket references an unknown normalized site")
        if site.access != "public":
            continue
        site_sockets = sorted(compatible[site_id], key=lambda socket: socket.source_id)
        if any(socket.provenance != site.provenance for socket in site_sockets):
            raise ValueError("site and compatible socket provenance must match")
        options.append(
            CompatibleStationOption(
                station=Station(
                    id=f"epdk:{site.source_id}",
                    name=site.name,
                    latitude=site.latitude,
                    longitude=site.longitude,
                    max_power_kw=max(socket.max_power_kw for socket in site_sockets),
                    connector_type="CCS2",
                ),
                site_source_id=site.source_id,
                socket_source_ids=tuple(socket.source_id for socket in site_sockets),
                provenance=site.provenance,
            )
        )
    return tuple(options)


def _require_exact_fields(value: Mapping[str, Any], expected: frozenset[str], field: str) -> None:
    actual = set(value)
    if actual != expected:
        missing = sorted(expected - actual)
        extra = sorted(actual - expected)
        raise EpdkSchemaDriftError(f"{field} fields changed; missing={missing}, extra={extra}")


def _site_problem(row: Mapping[str, Any]) -> str | None:
    required_fields = (
        "sarjIstasyonuNo",
        "sarjIstasyonuAdi",
        "sarjAgiIsletmecisiUnvan",
        "hizmetSekli",
        "enlem",
        "boylam",
        "soketler",
    )
    missing = [field for field in required_fields if field not in row]
    if missing:
        return f"missing_required_site_fields:{','.join(missing)}"
    if any(
        _nonempty_text(row[field]) is None
        for field in ("sarjIstasyonuNo", "sarjIstasyonuAdi", "sarjAgiIsletmecisiUnvan")
    ):
        return "invalid_required_site_identifier_or_text"
    if row["hizmetSekli"] not in {"HALKA_ACIK", "OZEL"}:
        return "unsupported_access_classification"
    if not isinstance(row["soketler"], list):
        return "sockets_not_a_list"
    try:
        latitude = _finite_number(row["enlem"])
        longitude = _finite_number(row["boylam"])
    except (TypeError, ValueError):
        return "invalid_coordinates"
    if not -90 <= latitude <= 90 or not -180 <= longitude <= 180:
        return "invalid_coordinates"
    return None


def _socket_problem(row: Mapping[str, Any]) -> str | None:
    missing = [field for field in EXPECTED_SOCKET_FIELDS if field not in row]
    if missing:
        return f"missing_required_socket_fields:{','.join(sorted(missing))}"
    if _nonempty_text(row["soketNo"]) is None:
        return "invalid_socket_source_id"
    current_type = _nonempty_text(row["soketTipi"])
    connector = _nonempty_text(row["soketTuru"])
    if current_type not in {"AC", "DC"}:
        return "unsupported_current_type"
    if connector not in {"AC_TYPE2", "DC_CCS", "DC_CHADEMO"}:
        return "unsupported_connector_type"
    if (current_type == "AC") != (connector == "AC_TYPE2"):
        return "inconsistent_current_and_connector_type"
    try:
        _positive_power(row["soketGucu"])
    except (TypeError, ValueError):
        return "invalid_socket_power"
    return None


def _socket_issue_id(value: Any, index: int) -> str:
    if isinstance(value, Mapping):
        return _nonempty_text(value.get("soketNo")) or f"socket[{index}]"
    return f"socket[{index}]"


def _nonempty_text(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    normalized = value.strip()
    return normalized or None


def _required_text(value: Any) -> str:
    result = _nonempty_text(value)
    if result is None:
        raise ValueError("expected non-empty text")
    return result


def _finite_number(value: Any) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TypeError("expected a number")
    result = float(value)
    if not math.isfinite(result):
        raise ValueError("expected a finite number")
    return result


def _positive_power(value: Any) -> float:
    if not isinstance(value, str):
        raise TypeError("observed socket power must remain a string")
    result = float(value.strip())
    if not math.isfinite(result) or result <= 0:
        raise ValueError("socket power must be finite and positive")
    return result


def _counts_dict(counts: ReconciliationCounts) -> dict[str, int]:
    return {
        "input": counts.input,
        "accepted": counts.accepted,
        "rejected": counts.rejected,
        "duplicates": counts.duplicates,
    }
