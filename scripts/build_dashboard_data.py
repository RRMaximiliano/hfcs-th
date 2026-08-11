#!/usr/bin/env python3
"""Fetch Kobo submissions or read Kobo XLSX exports and build a safe HFC payload."""

from __future__ import annotations

import argparse
import csv
import hashlib
import hmac
import json
import os
import re
import statistics
import sys
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable
from xml.etree import ElementTree as ET


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CONFIG = ROOT / "config" / "forms.json"
DEFAULT_TARGETS = ROOT / "config" / "targets.csv"
DEFAULT_OUTPUT = ROOT / "data" / "dashboard.json"


def load_dotenv(path: Path) -> None:
    """Load simple KEY=VALUE entries without overriding exported variables."""
    if not path.exists():
        return
    with path.open(encoding="utf-8") as handle:
        for raw_line in handle:
            line = raw_line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            key = key.strip()
            value = value.strip().strip('"').strip("'")
            if re.fullmatch(r"[A-Z_][A-Z0-9_]*", key):
                os.environ.setdefault(key, value)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--targets", type=Path, default=DEFAULT_TARGETS)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--sample", action="store_true", help="Use bundled JSON fixtures.")
    parser.add_argument(
        "--json",
        action="append",
        default=[],
        metavar="ROLE=PATH",
        help="Read a local Kobo JSON export for a role. May be repeated.",
    )
    parser.add_argument(
        "--xlsx",
        action="append",
        default=[],
        metavar="ROLE=PATH",
        help="Read the first sheet of a Kobo XLSX export. May be repeated.",
    )
    return parser.parse_args()


def load_json(path: Path) -> Any:
    with path.open(encoding="utf-8") as handle:
        return json.load(handle)


def parse_role_paths(items: list[str]) -> dict[str, Path]:
    parsed: dict[str, Path] = {}
    for item in items:
        if "=" not in item:
            raise ValueError(f"Expected ROLE=PATH, received: {item}")
        role, raw_path = item.split("=", 1)
        parsed[role.strip()] = Path(raw_path).expanduser().resolve()
    return parsed


def expand_env(value: Any) -> Any:
    if isinstance(value, str):
        match = re.fullmatch(r"\$\{([A-Z0-9_]+)\}", value)
        if match:
            return os.environ.get(match.group(1), "")
        return value
    if isinstance(value, list):
        return [expand_env(item) for item in value]
    if isinstance(value, dict):
        return {key: expand_env(item) for key, item in value.items()}
    return value


def fetch_json(url: str, token: str) -> Any:
    request = urllib.request.Request(
        url,
        headers={
            "Authorization": f"Token {token}",
            "Accept": "application/json",
            "User-Agent": "Todos-Humanos-HFC/1.0",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            return json.load(response)
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")[:500]
        raise RuntimeError(f"Kobo API returned HTTP {exc.code} for {url}: {body}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"Could not reach Kobo API at {url}: {exc.reason}") from exc


def fetch_kobo_form(api_base: str, asset_uid: str, token: str) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    asset_url = f"{api_base.rstrip('/')}/api/v2/assets/{asset_uid}/"
    asset = fetch_json(asset_url, token)
    data_url = f"{asset_url}data/?limit=1000"
    rows: list[dict[str, Any]] = []
    while data_url:
        page = fetch_json(data_url, token)
        if isinstance(page, list):
            rows.extend(page)
            data_url = ""
        else:
            rows.extend(page.get("results", []))
            data_url = page.get("next") or ""
    return rows, {
        "asset_name": asset.get("name", ""),
        "api_submission_total": asset.get("deployment__submission_count"),
        "last_submission_time": asset.get("deployment__last_submission_time"),
        "deployment_active": asset.get("deployment__active"),
    }


def col_index(reference: str) -> int:
    letters = re.match(r"[A-Z]+", reference.upper())
    if not letters:
        return 0
    result = 0
    for char in letters.group(0):
        result = result * 26 + (ord(char) - 64)
    return result - 1


def read_xlsx_first_sheet(path: Path) -> list[dict[str, Any]]:
    """Read values from the first XLSX worksheet using only the standard library."""
    ns_main = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
    ns_rel = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
    ns_pkg = "http://schemas.openxmlformats.org/package/2006/relationships"
    with zipfile.ZipFile(path) as archive:
        shared: list[str] = []
        if "xl/sharedStrings.xml" in archive.namelist():
            root = ET.fromstring(archive.read("xl/sharedStrings.xml"))
            for item in root.findall(f"{{{ns_main}}}si"):
                shared.append("".join(node.text or "" for node in item.iter(f"{{{ns_main}}}t")))

        workbook = ET.fromstring(archive.read("xl/workbook.xml"))
        first_sheet = workbook.find(f".//{{{ns_main}}}sheet")
        if first_sheet is None:
            return []
        relationship_id = first_sheet.attrib.get(f"{{{ns_rel}}}id", "")
        relationships = ET.fromstring(archive.read("xl/_rels/workbook.xml.rels"))
        target = ""
        for relation in relationships.findall(f"{{{ns_pkg}}}Relationship"):
            if relation.attrib.get("Id") == relationship_id:
                target = relation.attrib.get("Target", "")
                break
        if not target:
            raise ValueError(f"Could not locate the first worksheet in {path}")
        sheet_path = target.lstrip("/")
        if not sheet_path.startswith("xl/"):
            sheet_path = f"xl/{sheet_path}"
        root = ET.fromstring(archive.read(sheet_path))
        matrix: list[list[Any]] = []
        for row_element in root.findall(f".//{{{ns_main}}}sheetData/{{{ns_main}}}row"):
            row: list[Any] = []
            for cell in row_element.findall(f"{{{ns_main}}}c"):
                index = col_index(cell.attrib.get("r", "A1"))
                while len(row) <= index:
                    row.append("")
                cell_type = cell.attrib.get("t", "")
                value_node = cell.find(f"{{{ns_main}}}v")
                inline_node = cell.find(f"{{{ns_main}}}is")
                value: Any = ""
                if cell_type == "inlineStr" and inline_node is not None:
                    value = "".join(node.text or "" for node in inline_node.iter(f"{{{ns_main}}}t"))
                elif value_node is not None:
                    raw = value_node.text or ""
                    if cell_type == "s" and raw:
                        value = shared[int(raw)]
                    elif cell_type == "b":
                        value = raw == "1"
                    else:
                        value = raw
                row[index] = value
            matrix.append(row)
    if not matrix:
        return []
    headers = [str(value).strip() for value in matrix[0]]
    records: list[dict[str, Any]] = []
    for row in matrix[1:]:
        if not any(str(value).strip() for value in row):
            continue
        records.append({header: row[index] if index < len(row) else "" for index, header in enumerate(headers) if header})
    return records


def records_from_json(path: Path) -> list[dict[str, Any]]:
    payload = load_json(path)
    if isinstance(payload, list):
        return payload
    if isinstance(payload, dict) and isinstance(payload.get("results"), list):
        return payload["results"]
    raise ValueError(f"JSON input must be a list or contain a results list: {path}")


def scalar(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False, sort_keys=True)
    return re.sub(r"\s+", " ", str(value)).strip()


def find_value(row: dict[str, Any], candidates: Iterable[str]) -> str:
    for candidate in candidates:
        if candidate in row and scalar(row[candidate]):
            return scalar(row[candidate])
        matches = [key for key in row if key.rsplit("/", 1)[-1] == candidate]
        for key in matches:
            value = scalar(row.get(key))
            if value:
                return value
    return ""


def normalize_identifier(value: str) -> str:
    return re.sub(r"[^A-Z0-9]", "", value.upper())


def normalize_province(value: str) -> str:
    cleaned = re.sub(r"\s+", " ", value).strip()
    if not cleaned:
        return "Sin provincia"
    aliases = {
        "buenos aires": "Buenos Aires",
        "caba": "CABA",
        "catamarca": "Catamarca",
        "chaco": "Chaco",
        "chubut": "Chubut",
        "cordoba": "Córdoba",
        "córdoba": "Córdoba",
        "corrientes": "Corrientes",
        "entre rios": "Entre Ríos",
        "entre ríos": "Entre Ríos",
        "formosa": "Formosa",
        "jujuy": "Jujuy",
        "la pampa": "La Pampa",
        "la rioja": "La Rioja",
        "mendoza": "Mendoza",
        "misiones": "Misiones",
        "neuquen": "Neuquén",
        "neuquén": "Neuquén",
        "rio negro": "Río Negro",
        "río negro": "Río Negro",
        "salta": "Salta",
        "san juan": "San Juan",
        "san luis": "San Luis",
        "santa cruz": "Santa Cruz",
        "santa fe": "Santa Fe",
        "santiago del estero": "Santiago del Estero",
        "tierra del fuego": "Tierra del Fuego",
        "tucuman": "Tucumán",
        "tucumán": "Tucumán",
    }
    return aliases.get(cleaned.casefold(), cleaned)


def split_grades(value: str, role: str) -> list[str]:
    if not value:
        return []
    parts = re.split(r"[\s,;|]+", value)
    cleaned = []
    for part in parts:
        item = part.strip()
        if not item:
            continue
        if role == "teachers":
            aliases = {
                "1": "2.º",
                "2": "3.º",
                "3": "4.º",
                "4": "Multigrado",
                "5": "Otro",
            }
        else:
            aliases = {
                "1": "1.º (fuera de alcance)",
                "01": "1.º (fuera de alcance)",
                "1RO": "1.º (fuera de alcance)",
                "1°": "1.º (fuera de alcance)",
                "2": "2.º",
                "02": "2.º",
                "2DO": "2.º",
                "2°": "2.º",
                "3": "3.º",
                "03": "3.º",
                "3RO": "3.º",
                "3°": "3.º",
                "4": "4.º",
                "04": "4.º",
                "4TO": "4.º",
                "4°": "4.º",
            }
        normalized = aliases.get(item.upper(), item)
        if normalized not in cleaned:
            cleaned.append(normalized)
    return cleaned


def parse_number(value: str) -> float | None:
    if not value:
        return None
    try:
        return float(value.replace(",", "."))
    except ValueError:
        return None


def parse_timestamp(value: str) -> datetime | None:
    if not value:
        return None
    cleaned = value.strip().replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(cleaned)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def pseudonym(prefix: str, value: str, salt: str) -> str:
    if not value:
        return "Sin ID"
    digest = hmac.new(salt.encode(), value.encode(), hashlib.sha256).hexdigest()[:8].upper()
    return f"{prefix}-{digest}"


def load_targets(path: Path) -> dict[str, dict[str, Any]]:
    if not path.exists():
        return {}
    targets: dict[str, dict[str, Any]] = {}
    with path.open(encoding="utf-8-sig", newline="") as handle:
        for row in csv.DictReader(handle):
            school_id = scalar(row.get("school_id"))
            if not school_id:
                continue
            targets[school_id] = {
                "school_id": school_id,
                "school_name": scalar(row.get("school_name")),
                "province": scalar(row.get("province")),
                "students": int(float(row.get("target_students") or 0)),
                "teachers": int(float(row.get("target_teachers") or 0)),
                "directors": int(float(row.get("target_directors") or 0)),
            }
    return targets


def normalize_records(
    raw_by_role: dict[str, list[dict[str, Any]]],
    config_by_role: dict[str, dict[str, Any]],
    salt: str,
) -> list[dict[str, Any]]:
    normalized: list[dict[str, Any]] = []
    role_prefixes = {"students": "EST", "teachers": "DOC", "directors": "DIR"}
    for role, raw_rows in raw_by_role.items():
        form = config_by_role[role]
        fields = form["fields"]
        for index, row in enumerate(raw_rows):
            participant_raw = find_value(row, fields["participant_id"])
            participant_key = normalize_identifier(participant_raw)
            school_id = find_value(row, fields["school_id"])
            school_name = find_value(row, fields["school_name"])
            province = normalize_province(find_value(row, fields["province"]))
            grades = split_grades(find_value(row, fields["grade"]), role)
            enumerator_raw = find_value(row, fields["enumerator"])
            timestamp_raw = find_value(row, fields["timestamp"])
            timestamp = parse_timestamp(timestamp_raw)
            duration = parse_number(find_value(row, fields["duration_minutes"]))
            long_duration_value = find_value(row, fields.get("duration_flag_long", []))
            long_duration = long_duration_value.lower() in {"1", "true", "yes", "si", "sí"}
            submission_id = find_value(row, ["_uuid", "meta/instanceID", "_id"]) or f"{role}-{index + 1}"
            prefix = role_prefixes.get(role, role[:3].upper())
            normalized.append(
                {
                    "record_key": f"{role}:{submission_id}",
                    "role": role,
                    "participant_key": participant_key,
                    "case_ref": pseudonym(prefix, participant_key, salt),
                    "school_id": school_id,
                    "school_name": school_name or school_id or "Escuela sin identificar",
                    "province": province,
                    "grades": grades,
                    "grade_out_of_scope": role == "students" and "1.º (fuera de alcance)" in grades,
                    "enumerator_ref": pseudonym("APL", normalize_identifier(enumerator_raw), salt) if enumerator_raw else "Sin aplicador",
                    "timestamp": timestamp.isoformat().replace("+00:00", "Z") if timestamp else "",
                    "date": timestamp.date().isoformat() if timestamp else "Sin fecha",
                    "duration_minutes": duration,
                    "duration_flag_long": long_duration,
                    "missing_id": not participant_key,
                    "missing_school": not school_id,
                }
            )
    return normalized


def duplicate_key(record: dict[str, Any], config_by_role: dict[str, dict[str, Any]]) -> tuple[str, ...] | None:
    if record["missing_id"]:
        return None
    scope = config_by_role[record["role"]].get("duplicate_scope", ["participant_key"])
    values = [record["role"]]
    for field in scope:
        values.append(scalar(record.get(field)))
    return tuple(values)


def build_payload(
    raw_by_role: dict[str, list[dict[str, Any]]],
    metadata_by_role: dict[str, dict[str, Any]],
    config: dict[str, Any],
    targets: dict[str, dict[str, Any]],
    salt: str,
    is_sample: bool,
) -> dict[str, Any]:
    config_by_role = {form["key"]: form for form in config["forms"]}
    records = normalize_records(raw_by_role, config_by_role, salt)

    duplicate_groups: dict[tuple[str, ...], list[dict[str, Any]]] = defaultdict(list)
    for record in records:
        key = duplicate_key(record, config_by_role)
        if key:
            duplicate_groups[key].append(record)
    duplicated_keys = {key for key, group in duplicate_groups.items() if len(group) > 1}
    for record in records:
        key = duplicate_key(record, config_by_role)
        record["duplicate"] = key in duplicated_keys if key else False

    form_summaries = []
    for form in config["forms"]:
        role = form["key"]
        role_records = [record for record in records if record["role"] == role]
        valid_ids = {record["participant_key"] for record in role_records if record["participant_key"]}
        role_groups = [group for key, group in duplicate_groups.items() if key[0] == role and len(group) > 1]
        durations = [record["duration_minutes"] for record in role_records if record["duration_minutes"] is not None]
        meta = metadata_by_role.get(role, {})
        last_time = max((record["timestamp"] for record in role_records if record["timestamp"]), default="")
        form_summaries.append(
            {
                "key": role,
                "label": form["label"],
                "submissions": len(role_records),
                "api_submission_total": meta.get("api_submission_total"),
                "unique_participants": len(valid_ids),
                "duplicate_groups": len(role_groups),
                "duplicate_submissions": sum(len(group) - 1 for group in role_groups),
                "missing_id": sum(record["missing_id"] for record in role_records),
                "missing_school": sum(record["missing_school"] for record in role_records),
                "long_duration": sum(record["duration_flag_long"] for record in role_records),
                "out_of_scope_grade": sum(record["grade_out_of_scope"] for record in role_records),
                "median_duration_minutes": round(statistics.median(durations), 1) if durations else None,
                "last_submission_time": meta.get("last_submission_time") or last_time,
                "deployment_active": meta.get("deployment_active"),
                "asset_name": meta.get("asset_name") or form["label"],
                "public_form_url": form.get("public_form_url", ""),
            }
        )

    school_groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for record in records:
        school_groups[record["school_id"] or "__missing__"].append(record)
    for school_id in targets:
        school_groups.setdefault(school_id, [])

    schools = []
    for school_id, group in school_groups.items():
        target = targets.get(school_id, {})
        sample_record = next(iter(group), {})
        school_row: dict[str, Any] = {
            "school_id": "" if school_id == "__missing__" else school_id,
            "school_name": target.get("school_name") or sample_record.get("school_name") or "Escuela sin identificar",
            "province": target.get("province") or sample_record.get("province") or "Sin provincia",
            "roles": {},
        }
        collected_total = 0
        target_total = 0
        for role in config_by_role:
            role_records = [record for record in group if record["role"] == role]
            valid_ids = {record["participant_key"] for record in role_records if record["participant_key"]}
            role_groups: dict[tuple[str, ...], int] = Counter(
                duplicate_key(record, config_by_role) for record in role_records if duplicate_key(record, config_by_role)
            )
            role_target = int(target.get(role, 0))
            school_row["roles"][role] = {
                "submissions": len(role_records),
                "unique_participants": len(valid_ids),
                "duplicate_submissions": sum(count - 1 for count in role_groups.values() if count > 1),
                "missing_id": sum(record["missing_id"] for record in role_records),
                "target": role_target,
            }
            collected_total += len(valid_ids)
            target_total += role_target
        school_row["collected_total"] = collected_total
        school_row["target_total"] = target_total
        school_row["completion_rate"] = round(collected_total / target_total, 4) if target_total else None
        school_row["last_submission_time"] = max((record["timestamp"] for record in group if record["timestamp"]), default="")
        schools.append(school_row)
    schools.sort(key=lambda row: (row["province"], row["school_name"], row["school_id"]))

    grade_groups: dict[tuple[str, str, str, str], list[dict[str, Any]]] = defaultdict(list)
    for record in records:
        grades = record["grades"] or ["Sin grado"]
        for grade in grades:
            grade_groups[(record["school_id"], record["school_name"], record["province"], grade)].append(record)
    grades = []
    for (school_id, school_name, province, grade), group in grade_groups.items():
        row: dict[str, Any] = {
            "school_id": school_id,
            "school_name": school_name,
            "province": province,
            "grade": grade,
            "roles": {},
        }
        for role in config_by_role:
            role_records = [record for record in group if record["role"] == role]
            role_group_counts: dict[tuple[str, ...], int] = Counter(
                duplicate_key(record, config_by_role) for record in role_records if duplicate_key(record, config_by_role)
            )
            row["roles"][role] = {
                "submissions": len(role_records),
                "unique_participants": len({record["participant_key"] for record in role_records if record["participant_key"]}),
                "duplicate_submissions": sum(count - 1 for count in role_group_counts.values() if count > 1),
            }
        grades.append(row)
    grades.sort(key=lambda row: (row["province"], row["school_name"], row["grade"]))

    enumerator_groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for record in records:
        enumerator_groups[record["enumerator_ref"]].append(record)
    enumerators = []
    for ref, group in enumerator_groups.items():
        enumerators.append(
            {
                "enumerator_ref": ref,
                "submissions": len(group),
                "students": sum(record["role"] == "students" for record in group),
                "teachers": sum(record["role"] == "teachers" for record in group),
                "directors": sum(record["role"] == "directors" for record in group),
                "duplicates": sum(record["duplicate"] for record in group),
                "missing_id": sum(record["missing_id"] for record in group),
                "schools": len({record["school_id"] for record in group if record["school_id"]}),
                "school_ids": sorted({record["school_id"] for record in group if record["school_id"]}),
                "provinces": sorted({record["province"] for record in group if record["province"]}),
                "last_submission_time": max((record["timestamp"] for record in group if record["timestamp"]), default=""),
            }
        )
    enumerators.sort(key=lambda row: (-row["submissions"], row["enumerator_ref"]))

    daily_counter: dict[str, Counter[str]] = defaultdict(Counter)
    for record in records:
        daily_counter[record["date"]][record["role"]] += 1
    daily = []
    for date in sorted(daily_counter):
        counts = daily_counter[date]
        daily.append({"date": date, **{role: counts[role] for role in config_by_role}, "total": sum(counts.values())})

    duplicate_rows = []
    for key, group in duplicate_groups.items():
        if len(group) < 2:
            continue
        first = group[0]
        duplicate_rows.append(
            {
                "role": first["role"],
                "case_ref": first["case_ref"],
                "school_id": first["school_id"],
                "school_name": first["school_name"],
                "province": first["province"],
                "grades": first["grades"],
                "submission_count": len(group),
                "extra_submissions": len(group) - 1,
                "timestamps": sorted(record["timestamp"] for record in group if record["timestamp"]),
            }
        )
    duplicate_rows.sort(key=lambda row: (-row["extra_submissions"], row["role"], row["school_name"]))

    recent_records = sorted(records, key=lambda record: record["timestamp"], reverse=True)[:250]
    safe_recent = [
        {
            key: record[key]
            for key in (
                "role",
                "case_ref",
                "school_id",
                "school_name",
                "province",
                "grades",
                "enumerator_ref",
                "timestamp",
                "duration_minutes",
                "duration_flag_long",
                "grade_out_of_scope",
                "missing_id",
                "missing_school",
                "duplicate",
            )
        }
        for record in recent_records
    ]

    generated_at = datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
    total_submissions = len(records)
    total_duplicate_extras = sum(len(group) - 1 for group in duplicate_groups.values() if len(group) > 1)
    return {
        "schema_version": 1,
        "generated_at": generated_at,
        "is_sample": is_sample,
        "privacy": {
            "raw_identifiers_published": False,
            "case_references": "HMAC-SHA256 pseudonyms truncated to 8 hexadecimal characters",
        },
        "summary": {
            "submissions": total_submissions,
            "unique_participants": sum(item["unique_participants"] for item in form_summaries),
            "duplicate_submissions": total_duplicate_extras,
            "missing_id": sum(record["missing_id"] for record in records),
            "missing_school": sum(record["missing_school"] for record in records),
            "out_of_scope_grade": sum(record["grade_out_of_scope"] for record in records),
            "schools_with_data": len({record["school_id"] for record in records if record["school_id"]}),
            "schools_with_targets": len(targets),
        },
        "forms": form_summaries,
        "schools": schools,
        "grades": grades,
        "enumerators": enumerators,
        "daily": daily,
        "duplicates": duplicate_rows,
        "recent_records": safe_recent,
    }


def main() -> int:
    args = parse_args()
    load_dotenv(ROOT / ".env")
    config = expand_env(load_json(args.config))
    config_by_role = {form["key"]: form for form in config["forms"]}
    json_paths = parse_role_paths(args.json)
    xlsx_paths = parse_role_paths(args.xlsx)
    unknown_roles = (json_paths.keys() | xlsx_paths.keys()) - config_by_role.keys()
    if unknown_roles:
        raise ValueError(f"Unknown role key(s): {', '.join(sorted(unknown_roles))}")

    token = os.environ.get("KOBO_API_TOKEN", "").strip()
    explicit_salt = os.environ.get("HFC_HASH_SALT", "").strip()
    is_offline = args.sample or bool(json_paths) or bool(xlsx_paths)
    if is_offline and not args.sample and not explicit_salt:
        raise RuntimeError("Set HFC_HASH_SALT before processing local exports.")
    salt = "todos-humanos-sample-only" if args.sample else (explicit_salt or token)

    raw_by_role: dict[str, list[dict[str, Any]]] = {}
    metadata_by_role: dict[str, dict[str, Any]] = {}
    if args.sample:
        for role in config_by_role:
            fixture = ROOT / "tests" / "fixtures" / f"{role}.json"
            raw_by_role[role] = records_from_json(fixture)
            metadata_by_role[role] = {"asset_name": f"Muestra - {config_by_role[role]['label']}"}
    elif json_paths or xlsx_paths:
        for role in config_by_role:
            if role in json_paths:
                raw_by_role[role] = records_from_json(json_paths[role])
            elif role in xlsx_paths:
                raw_by_role[role] = read_xlsx_first_sheet(xlsx_paths[role])
            else:
                raw_by_role[role] = []
            metadata_by_role[role] = {"asset_name": config_by_role[role]["label"]}
    else:
        if not token:
            raise RuntimeError("KOBO_API_TOKEN is required for an API refresh.")
        api_base = config.get("api_base", "https://eu.kobotoolbox.org")
        for role, form in config_by_role.items():
            asset_uid = scalar(form.get("asset_uid"))
            if not asset_uid:
                raise RuntimeError(f"Missing asset UID for {role}. Set {form.get('asset_uid_env', 'the configured environment variable')}.")
            raw_by_role[role], metadata_by_role[role] = fetch_kobo_form(api_base, asset_uid, token)

    payload = build_payload(
        raw_by_role,
        metadata_by_role,
        config,
        load_targets(args.targets),
        salt,
        args.sample,
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("w", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
    print(f"Wrote {payload['summary']['submissions']} submissions to {args.output}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (RuntimeError, ValueError, OSError, zipfile.BadZipFile) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        raise SystemExit(1)
