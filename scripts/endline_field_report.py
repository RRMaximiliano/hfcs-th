#!/usr/bin/env python3
"""Confidential endline HFC workbook for the field team (run locally, never in CI).

Downloads the three endline Kobo projects, compares them with the confidential
endline sample frame, and writes a workbook with names and IDs:

  Leeme, Resumen, Duplicados, Posibles_no_vinculados, Pendientes, Alertas, Aplicadores

Review decisions typed into the decision columns of Duplicados and
Posibles_no_vinculados are carried forward by submission UUID every time the
workbook is rebuilt (the same logic as DIME's ``ieduplicates``): nothing is
deleted or changed in Kobo, and decisions are never lost on a rerun.

Also writes ``decisiones_cierre.csv`` (the reviewed decisions in ASCII columns, read by
the Stata endline cleaning pipeline) and ``resumen_cierre_<fecha>.md``, an aggregate-only summary that can be
pasted into a daily report.

Requires openpyxl. Output defaults to <TH_PROJECT_ROOT>/Analysis/Data/HFC/Endline/, which
is outside the public repository. The project folder comes from TH_PROJECT_ROOT
in this repository's .env (gitignored).
"""

from __future__ import annotations

import argparse
import csv
import importlib.util
import json
import os
import re
import shutil
import statistics
import sys
import unicodedata
from collections import Counter, defaultdict
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any

try:
    from openpyxl import Workbook, load_workbook
    from openpyxl.styles import Alignment, Font, PatternFill
    from openpyxl.utils import get_column_letter
except ImportError:  # pragma: no cover
    sys.exit("openpyxl is required: python3 -m pip install openpyxl")

ROOT = Path(__file__).resolve().parents[1]


def project_root() -> Path:
    """Dropbox project folder (holds the confidential frame and data).

    Set TH_PROJECT_ROOT in this repository's .env (gitignored).
    """
    env_file = ROOT / ".env"
    if env_file.exists():
        for line in env_file.read_text(encoding="utf-8").splitlines():
            if line.strip().startswith("TH_PROJECT_ROOT="):
                os.environ.setdefault("TH_PROJECT_ROOT", line.split("=", 1)[1].strip().strip('"').strip("'"))
    value = os.environ.get("TH_PROJECT_ROOT", "").strip()
    if not value:
        raise SystemExit("Set TH_PROJECT_ROOT (the Dropbox project folder) in .env")
    path = Path(value).expanduser()
    if not (path / "Instruments").is_dir():
        raise SystemExit(f"TH_PROJECT_ROOT does not look like the project folder: {path}")
    return path
WORKBOOK_NAME = "monitoreo_cierre_confidencial.xlsx"
ART = timezone(timedelta(hours=-3))

SPEC = importlib.util.spec_from_file_location("dash", ROOT / "scripts" / "build_dashboard_data.py")
dash = importlib.util.module_from_spec(SPEC)
assert SPEC.loader
SPEC.loader.exec_module(dash)

ROLE_LABEL = {"students": "Estudiantes", "teachers": "Docentes", "directors": "Directivos"}
ROLE_PREFIX = {"students": "student", "teachers": "teacher", "directors": "director"}
FRAME_FILE = {"students": "students_frame.csv", "teachers": "teachers_frame.csv", "directors": "directors_frame.csv"}
COMPLETION = {"1": "Completa", "2": "Parcial", "3": "No quiso participar", "4": "Se detuvo antes de terminar"}
ADMIN_MODE = {"1": "Individual", "2": "Grupo pequeño", "3": "Aula completa"}
DUP_DECISION_COLS = ["Conservar (sí/no)", "Motivo", "Revisado por", "Fecha de revisión"]
MATCH_DECISION_COLS = ["¿Es la misma persona? (sí/no)", "ID correcto", "Notas", "Revisado por"]
META_PREFIXES = ("_", "meta", "formhub")


# --------------------------------------------------------------------------- helpers
def s(value: Any) -> str:
    return dash.scalar(value)


def digits(value: Any) -> str:
    text = s(value)
    if re.fullmatch(r"\d+\.0", text):
        text = text[:-2]
    return re.sub(r"\D", "", text)


def name_tokens(value: Any) -> frozenset[str]:
    text = unicodedata.normalize("NFKD", s(value)).encode("ascii", "ignore").decode().lower()
    return frozenset(token for token in re.findall(r"[a-z]+", text) if len(token) > 1)


def flat(row: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for key, value in row.items():
        out.setdefault(key.rsplit("/", 1)[-1], value)
    return out


def local_time(value: str) -> str:
    parsed = dash.parse_timestamp(s(value))
    return parsed.astimezone(ART).strftime("%Y-%m-%d %H:%M") if parsed else ""


def answered_fields(row: dict[str, Any]) -> int:
    return sum(1 for key, value in row.items() if not key.startswith(META_PREFIXES) and s(value))


def latest_frame() -> Path:
    preloads = project_root() / "Instruments" / "Sample" / "Preloads"
    frames = sorted(preloads.glob("ENDLINE_FRAME_v*"))
    if not frames:
        raise SystemExit(f"No ENDLINE_FRAME_v* in {preloads}")
    return frames[-1]


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


# --------------------------------------------------------------------------- data
def fetch(config: dict[str, Any], token: str, sample: bool) -> dict[str, list[dict[str, Any]]]:
    data = {}
    for form in config["forms"]:
        role = form["key"]
        if sample:
            rows = dash.records_from_json(ROOT / "tests" / "fixtures" / config.get("fixture_dir", "") / f"{role}.json")
        else:
            rows, _ = dash.fetch_kobo_form(config["api_base"], form["asset_uid"], token)
        data[role] = [flat(row) for row in rows]
    return data


def person_name(role: str, row: dict[str, Any], frame_by_id: dict[str, dict[str, str]]) -> str:
    pre = ROLE_PREFIX[role]
    frame_row = frame_by_id.get(s(row.get(f"{pre}_id")))
    if frame_row and frame_row.get("person_name"):
        return frame_row["person_name"]
    return s(row.get(f"{pre}_manual_name")) or s(row.get(f"{pre}_display_label")) or s(row.get(f"{pre}_dni_matched_label"))


def submission_base(role: str, row: dict[str, Any], frame_by_id, schools) -> dict[str, Any]:
    pre = ROLE_PREFIX[role]
    school_id = s(row.get("school_id"))
    school = schools.get(school_id, {})
    return {
        "Población": ROLE_LABEL[role],
        "Provincia": school.get("province") or dash.normalize_province(s(row.get("province"))),
        "Escuela": school.get("school_name") or s(row.get("school_name")),
        "Código escuela": school_id,
        "ID de estudio": s(row.get(f"{pre}_id")),
        "Nombre": person_name(role, row, frame_by_id),
        "DNI o ID escrito": digits(row.get(f"{pre}_dni_input")) or s(row.get(f"{pre}_dni_input")),
        "Método de identificación": s(row.get(f"{pre}_id_method")),
        "Grado": s(row.get("grade")) if role == "students" else s(row.get("grade_taught")),
        "Sección": s(row.get("section_id")) if role == "students" else "",
        "Aplicador": s(row.get("enumerator_id")),
        "Enviado (hora AR)": local_time(row.get("_submission_time") or row.get("end")),
        "Minutos": dash.parse_number(s(row.get("duration_minutes"))),
        "Estado": COMPLETION.get(s(row.get("completion_status")), s(row.get("completion_status"))),
        "Campos respondidos": answered_fields(row),
        "UUID envío": s(row.get("_uuid")) or s(row.get("instanceID")),
    }


# --------------------------------------------------------------------------- checks
def build_duplicates(data, config, frame_by_id, schools) -> list[dict[str, Any]]:
    out = []
    for form in config["forms"]:
        role = form["key"]
        pre = ROLE_PREFIX[role]
        groups: dict[tuple, list] = defaultdict(list)
        for row in data[role]:
            pid = s(row.get(f"{pre}_id"))
            if not pid:
                continue
            key = (pid,) if role == "students" else (pid, s(row.get("school_id")))
            groups[key].append(row)
        for number, (key, rows) in enumerate(sorted((k, v) for k, v in groups.items() if len(v) > 1), start=1):
            # Same ranking as the baseline cleaning: complete first, then most answered, then latest.
            ranked = sorted(rows, key=lambda r: (s(r.get("completion_status")) == "1", answered_fields(r), s(r.get("_submission_time"))))
            keep = s(ranked[-1].get("_uuid"))
            for row in rows:
                base = submission_base(role, row, frame_by_id, schools)
                out.append({
                    "Grupo": f"{ROLE_LABEL[role][:3].upper()}-{number:03d}",
                    **base,
                    "Sugerencia": "Conservar" if base["UUID envío"] == keep else "Revisar / descartar",
                })
    return out


def build_possible_matches(data, config, frames, frame_by_id, schools) -> list[dict[str, Any]]:
    out = []
    for form in config["forms"]:
        role = form["key"]
        pre = ROLE_PREFIX[role]
        frame = frames[role]
        by_school_dni = defaultdict(list)
        by_dni = defaultdict(list)
        by_school = defaultdict(list)
        for person in frame:
            dni = digits(person.get("dni_normalized"))
            if dni:
                by_school_dni[(person["school_id"], dni)].append(person)
                by_dni[dni].append(person)
            by_school[person["school_id"]].append(person)
        surveyed = {s(r.get(f"{pre}_id")) for r in data[role]}
        for row in data[role]:
            if s(row.get(f"{pre}_id_method")) != "manual_new":
                continue
            school_id = s(row.get("school_id"))
            dni = digits(row.get(f"{pre}_dni_input"))
            tokens = name_tokens(row.get(f"{pre}_manual_name"))
            candidates: list[tuple[str, dict[str, str]]] = []
            for person in by_school_dni.get((school_id, dni), []) if dni else []:
                candidates.append(("Mismo DNI, misma escuela", person))
            if dni and not candidates:
                for person in by_dni.get(dni, []):
                    candidates.append(("Mismo DNI, otra escuela", person))
            if tokens:
                for person in by_school[school_id]:
                    other = name_tokens(person.get("person_name"))
                    if not other or any(person is c[1] for c in candidates):
                        continue
                    if other == tokens:
                        candidates.append(("Mismo nombre, misma escuela", person))
                    elif len(tokens & other) >= 2 and (tokens <= other or other <= tokens):
                        candidates.append(("Nombre parecido, misma escuela", person))
            for basis, person in candidates:
                base = submission_base(role, row, frame_by_id, schools)
                out.append({
                    **base,
                    "Coincidencia": basis,
                    "ID candidato (marco)": person.get("frame_id", ""),
                    "Nombre candidato": person.get("person_name", ""),
                    "DNI candidato": digits(person.get("dni_normalized")),
                    "Escuela candidato": person.get("school_id", ""),
                    "Grado candidato 2026": person.get("grade", ""),
                    "Sección candidato": person.get("section_id", ""),
                    "Candidato en línea de base": "Sí" if person.get("baseline_participant") == "1" else "No",
                    "Candidato ya encuestado en cierre": "Sí" if person.get("frame_id") in surveyed else "No",
                })
    return out


def build_pending(data, config, frames, schools) -> list[dict[str, Any]]:
    out = []
    for form in config["forms"]:
        role = form["key"]
        pre = ROLE_PREFIX[role]
        visited = {s(r.get("school_id")) for r in data[role]}
        surveyed = {s(r.get(f"{pre}_id")) for r in data[role]}
        for person in frames[role]:
            if person.get("baseline_participant") != "1" or person["school_id"] not in visited or person["frame_id"] in surveyed:
                continue
            school = schools.get(person["school_id"], {})
            out.append({
                "Población": ROLE_LABEL[role],
                "Provincia": school.get("province", person.get("province", "")),
                "Escuela": school.get("school_name", person.get("school_name", "")),
                "Código escuela": person["school_id"],
                "Grado 2026": person.get("grade", ""),
                "Sección": person.get("section_id", ""),
                "Nombre": person.get("person_name", ""),
                "DNI o ID para escribir": digits(person.get("dni_normalized")) or "",
                "Código IP (San Juan)": person.get("ip_code", ""),
                "ID de estudio": person["frame_id"],
            })
    out.sort(key=lambda r: (r["Población"], r["Provincia"], r["Escuela"], s(r["Grado 2026"]), r["Sección"], r["Nombre"]))
    return out


def build_alerts(data, config, frame_by_id, schools) -> list[dict[str, Any]]:
    out = []
    for form in config["forms"]:
        role = form["key"]
        checks = form.get("checks", {})
        for row in data[role]:
            minutes = dash.parse_number(s(row.get("duration_minutes")))
            flags = dash.quality_flags(row, form, minutes)
            reasons = []
            if flags.get("short_duration"):
                reasons.append(f"Duración menor a {checks.get('short_minutes')} min")
            if flags.get("very_long_duration"):
                reasons.append(f"Duración mayor a {checks.get('very_long_minutes')} min")
            if flags.get("incomplete"):
                reasons.append("No completa")
            if flags.get("admin_deviation"):
                reasons.append(f"Aplicación: {ADMIN_MODE.get(s(row.get('student_admin_mode')), '?')}")
            # knew_arm is a team-level problem (see Aplicadores and the summary), not a per-interview action.
            if flags.get("no_consent"):
                reasons.append("Sin consentimiento")
            if not reasons:
                continue
            base = submission_base(role, row, frame_by_id, schools)
            out.append({
                **base,
                "Alertas": "; ".join(reasons),
                "Motivo de interrupción": s(row.get("stop_reason_notes")) or s(row.get("stop_reason")),
                "Notas de desviación": s(row.get("administration_deviation_notes")),
            })
    return out


def build_enumerators(data, config) -> list[dict[str, Any]]:
    form = next(f for f in config["forms"] if f["key"] == "students")
    groups = defaultdict(list)
    for row in data["students"]:
        groups[s(row.get("enumerator_id")) or "(sin aplicador)"].append(row)
    ids = Counter(s(r.get("student_id")) for r in data["students"])
    out = []
    for enumerator, rows in groups.items():
        flags = [dash.quality_flags(r, form, dash.parse_number(s(r.get("duration_minutes")))) for r in rows]
        mins = [m for m in (dash.parse_number(s(r.get("duration_minutes"))) for r in rows) if m is not None]
        n = len(rows)
        out.append({
            "Aplicador": enumerator,
            "Escuelas": len({s(r.get("school_id")) for r in rows}),
            "Envíos": n,
            "Días": len({local_time(r.get("_submission_time"))[:10] for r in rows}),
            "Mediana minutos": round(statistics.median(mins), 1) if mins else None,
            "% registros nuevos": round(100 * sum(f["new_registration"] for f in flags) / n, 1),
            "Envíos con ID repetido": sum(1 for r in rows if ids[s(r.get("student_id"))] > 1),
            "% no completas": round(100 * sum(f["incomplete"] for f in flags) / n, 1),
            "% no individual": round(100 * sum(f["admin_deviation"] for f in flags) / n, 1),
            "% sabía el grupo": round(100 * sum(f["knew_arm"] for f in flags) / n, 1),
            "Duración muy corta": sum(f["short_duration"] for f in flags),
            "Duración muy larga": sum(f["very_long_duration"] for f in flags),
        })
    out.sort(key=lambda r: -r["Envíos"])
    return out


def build_summary(data, config, frames, schools, duplicates, matches, pending) -> list[dict[str, Any]]:
    out = []
    for form in config["forms"]:
        role = form["key"]
        pre = ROLE_PREFIX[role]
        baseline = Counter(p["school_id"] for p in frames[role] if p.get("baseline_participant") == "1")
        by_school = defaultdict(list)
        for row in data[role]:
            by_school[s(row.get("school_id"))].append(row)
        dup_extra = Counter()
        for row in duplicates:
            if row["Población"] == ROLE_LABEL[role] and row["Sugerencia"] != "Conservar":
                dup_extra[row["Código escuela"]] += 1
        match_count = Counter(r["Código escuela"] for r in matches if r["Población"] == ROLE_LABEL[role])
        pend = Counter(r["Código escuela"] for r in pending if r["Población"] == ROLE_LABEL[role])
        for school_id, rows in sorted(by_school.items(), key=lambda kv: (schools.get(kv[0], {}).get("province", ""), schools.get(kv[0], {}).get("school_name", ""))):
            linked = {s(r.get(f"{pre}_id")) for r in rows if s(r.get("frame_baseline_participant")) == "1"}
            target = baseline.get(school_id, 0)
            flags = [dash.quality_flags(r, form, dash.parse_number(s(r.get("duration_minutes")))) for r in rows]
            out.append({
                "Población": ROLE_LABEL[role],
                "Provincia": schools.get(school_id, {}).get("province", ""),
                "Escuela": schools.get(school_id, {}).get("school_name", school_id),
                "Código escuela": school_id,
                "Envíos": len(rows),
                "En línea de base": target,
                "Vinculados": len(linked),
                "Seguimiento %": round(100 * len(linked) / target, 1) if target else None,
                "Pendientes": pend.get(school_id, 0),
                "Registros nuevos": sum(f["new_registration"] for f in flags),
                "Posibles no vinculados": match_count.get(school_id, 0),
                "Duplicados extra": dup_extra.get(school_id, 0),
                "No completas": sum(f["incomplete"] for f in flags),
                "Muy cortas": sum(f["short_duration"] for f in flags),
                "Primer envío": min(local_time(r.get("_submission_time")) for r in rows),
                "Último envío": max(local_time(r.get("_submission_time")) for r in rows),
            })
    return out


# --------------------------------------------------------------------------- workbook
def previous_decisions(path: Path, sheet: str, key_cols: list[str], decision_cols: list[str]) -> dict[tuple, dict[str, Any]]:
    if not path.exists():
        return {}
    try:
        wb = load_workbook(path, read_only=True)
    except Exception:
        return {}
    if sheet not in wb.sheetnames:
        return {}
    rows = list(wb[sheet].iter_rows(values_only=True))
    if not rows:
        return {}
    header = [s(h) for h in rows[0]]
    if not all(c in header for c in key_cols):
        return {}
    out = {}
    for row in rows[1:]:
        record = dict(zip(header, row))
        decisions = {c: record.get(c) for c in decision_cols if record.get(c) not in (None, "")}
        if decisions:
            out[tuple(s(record.get(c)) for c in key_cols)] = decisions
    return out


def apply_decisions(rows, decisions, key_cols, decision_cols):
    carried = 0
    for row in rows:
        saved = decisions.get(tuple(s(row.get(c)) for c in key_cols), {})
        carried += bool(saved)
        for col in decision_cols:
            row[col] = saved.get(col, "")
    return carried


def normalize_yes(value: Any) -> str:
    text = unicodedata.normalize("NFKD", s(value)).encode("ascii", "ignore").decode().strip().lower()
    if text in {"si", "s", "yes", "y", "1", "conservar"}:
        return "yes"
    if text in {"no", "n", "0", "descartar"}:
        return "no"
    return ""


def write_decisions_csv(path: Path, duplicates: list[dict[str, Any]], matches: list[dict[str, Any]]) -> int:
    """Machine-readable copy of the reviewed decisions for the Stata cleaning pipeline.

    One row per reviewed submission. ASCII headers so Stata can import it directly.
    """
    role_key = {label: key for key, label in ROLE_LABEL.items()}
    rows = []
    for row in duplicates:
        keep = normalize_yes(row.get("Conservar (sí/no)"))
        if keep:
            rows.append({
                "submission_uuid": row["UUID envío"], "role": role_key[row["Población"]], "decision_type": "duplicate",
                "study_id": row["ID de estudio"], "keep": keep, "same_person": "", "correct_id": "",
                "reason": s(row.get("Motivo")), "reviewed_by": s(row.get("Revisado por")), "review_date": s(row.get("Fecha de revisión")),
            })
    for row in matches:
        same = normalize_yes(row.get("¿Es la misma persona? (sí/no)"))
        if same:
            rows.append({
                "submission_uuid": row["UUID envío"], "role": role_key[row["Población"]], "decision_type": "link",
                "study_id": row["ID de estudio"], "keep": "", "same_person": same,
                "correct_id": s(row.get("ID correcto")) or (row["ID candidato (marco)"] if same == "yes" else ""),
                "reason": s(row.get("Notas")), "reviewed_by": s(row.get("Revisado por")), "review_date": "",
            })
    columns = ["submission_uuid", "role", "decision_type", "study_id", "keep", "same_person", "correct_id", "reason", "reviewed_by", "review_date"]
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns)
        writer.writeheader()
        writer.writerows(rows)
    os.chmod(path, 0o600)
    return len(rows)


HEADER_FILL = PatternFill("solid", fgColor="1F4E46")
DECISION_FILL = PatternFill("solid", fgColor="FFF4CC")


def write_sheet(wb, title, rows, columns=None, decision_cols=(), note=None):
    ws = wb.create_sheet(title)
    columns = columns or (list(rows[0].keys()) if rows else ["Sin casos"])
    start = 1
    if note:
        ws.cell(row=1, column=1, value=note).font = Font(italic=True, color="555555")
        start = 3
    for index, column in enumerate(columns, start=1):
        cell = ws.cell(row=start, column=index, value=column)
        cell.font = Font(bold=True, color="FFFFFF" if column not in decision_cols else "000000")
        cell.fill = DECISION_FILL if column in decision_cols else HEADER_FILL
        cell.alignment = Alignment(wrap_text=True, vertical="top")
    for r, row in enumerate(rows, start=start + 1):
        for c, column in enumerate(columns, start=1):
            cell = ws.cell(row=r, column=c, value=row.get(column))
            if column in decision_cols:
                cell.fill = DECISION_FILL
    for c, column in enumerate(columns, start=1):
        width = max([len(s(column))] + [len(s(row.get(column))) for row in rows[:500]])
        ws.column_dimensions[get_column_letter(c)].width = min(max(10, width + 2), 45)
    ws.freeze_panes = ws.cell(row=start + 1, column=1)
    if rows:
        ws.auto_filter.ref = f"A{start}:{get_column_letter(len(columns))}{start + len(rows)}"
    return ws


def summary_markdown(now, data, config, frames, summary, duplicates, matches, pending, alerts) -> str:
    lines = [f"# Cierre Todos Humanos: resumen de monitoreo ({now:%Y-%m-%d %H:%M} hora AR)", ""]
    lines.append("| Población | Envíos | Escuelas | Vinculados / en LB (escuelas visitadas) | Registros nuevos | Posibles no vinculados | Duplicados extra | No completas | Muy cortas |")
    lines.append("|---|---|---|---|---|---|---|---|---|")
    for form in config["forms"]:
        label = ROLE_LABEL[form["key"]]
        rows = [r for r in summary if r["Población"] == label]
        target = sum(r["En línea de base"] for r in rows)
        linked = sum(r["Vinculados"] for r in rows)
        pct = f" ({100 * linked / target:.0f}%)" if target else ""
        unique_matches = len({r["UUID envío"] for r in matches if r["Población"] == label})
        lines.append(
            f"| {label} | {sum(r['Envíos'] for r in rows)} | {len(rows)} | {linked} / {target}{pct} | "
            f"{sum(r['Registros nuevos'] for r in rows)} | {unique_matches} | {sum(r['Duplicados extra'] for r in rows)} | "
            f"{sum(r['No completas'] for r in rows)} | {sum(r['Muy cortas'] for r in rows)} |"
        )
    days = Counter(local_time(r.get("_submission_time"))[:10] for rows in data.values() for r in rows)
    lines += ["", "Envíos por día: " + ", ".join(f"{d}: {n}" for d, n in sorted(days.items())), ""]
    students = data["students"]
    if students:
        knew = sum(s(r.get("assessor_knew_arm")) == "1" for r in students)
        mode = Counter(ADMIN_MODE.get(s(r.get("student_admin_mode")), "Sin dato") for r in students)
        lines.append(f"Estudiantes: el aplicador sabía si la escuela participa en {knew} de {len(students)} entrevistas ({100 * knew / len(students):.0f}%). Modo de aplicación: " + ", ".join(f"{k} {v}" for k, v in mode.most_common()) + ".")
    lines.append(f"Pendientes de línea de base en escuelas visitadas: {len(pending)} personas (detalle en el Excel confidencial).")
    lines.append("")
    lines.append("Sin datos personales: este resumen puede compartirse. El Excel confidencial no.")
    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--config", type=Path, default=ROOT / "config" / "forms_endline.json")
    parser.add_argument("--frame-dir", type=Path, default=None)
    parser.add_argument("--output-dir", type=Path, default=None)
    parser.add_argument("--sample", action="store_true", help="Use the synthetic endline fixtures (for testing).")
    args = parser.parse_args()

    dash.load_dotenv(ROOT / ".env")
    if args.output_dir is None:
        args.output_dir = project_root() / "Analysis" / "Data" / "HFC" / "Endline"
    config = dash.expand_env(dash.load_json(args.config))
    token = os.environ.get("KOBO_API_TOKEN", "").strip()
    if not args.sample and not token:
        raise SystemExit("KOBO_API_TOKEN is required (Analysis/HFCs/.env).")
    try:
        if args.output_dir.resolve().is_relative_to(ROOT.resolve()) and not args.sample:
            raise SystemExit("Refusing to write the confidential workbook inside the public repository.")
    except AttributeError:  # Python < 3.9 has no is_relative_to
        pass

    frame_dir = args.frame_dir or latest_frame()
    frames = {role: read_csv(frame_dir / name) for role, name in FRAME_FILE.items()}
    schools = {row["name"]: row for row in read_csv(frame_dir / "schools.csv")}
    frame_by_id = {p["frame_id"]: p for rows in frames.values() for p in rows}

    data = fetch(config, token, args.sample)
    duplicates = build_duplicates(data, config, frame_by_id, schools)
    matches = build_possible_matches(data, config, frames, frame_by_id, schools)
    pending = build_pending(data, config, frames, schools)
    alerts = build_alerts(data, config, frame_by_id, schools)
    enumerators = build_enumerators(data, config)
    summary = build_summary(data, config, frames, schools, duplicates, matches, pending)

    args.output_dir.mkdir(parents=True, exist_ok=True)
    path = args.output_dir / WORKBOOK_NAME
    dup_keys = ["UUID envío"]
    match_keys = ["UUID envío", "ID candidato (marco)"]
    carried_dup = apply_decisions(duplicates, previous_decisions(path, "Duplicados", dup_keys, DUP_DECISION_COLS), dup_keys, DUP_DECISION_COLS)
    carried_match = apply_decisions(matches, previous_decisions(path, "Posibles_no_vinculados", match_keys, MATCH_DECISION_COLS), match_keys, MATCH_DECISION_COLS)

    now = datetime.now(ART)
    archive = args.output_dir / "archivo"
    if path.exists():
        archive.mkdir(exist_ok=True)
        shutil.copy2(path, archive / f"monitoreo_cierre_confidencial_{datetime.fromtimestamp(path.stat().st_mtime, ART):%Y%m%d_%H%M}.xlsx")

    wb = Workbook()
    wb.remove(wb.active)
    readme = [
        {"Hoja": "Resumen", "Contenido": "Una fila por escuela y población: envíos, seguimiento de línea de base, pendientes y alertas."},
        {"Hoja": "Duplicados", "Contenido": "Todos los envíos de un ID repetido. 'Sugerencia' sigue la regla de línea de base (completa, más campos respondidos, más reciente). Complete las columnas amarillas; se conservan al regenerar."},
        {"Hoja": "Posibles_no_vinculados", "Contenido": "Registros nuevos cuyo DNI o nombre coincide con alguien del marco. Si es la misma persona, debe corregirse el ID para no perder el vínculo con línea de base. Columnas amarillas se conservan."},
        {"Hoja": "Pendientes", "Contenido": "Participantes de línea de base en escuelas ya visitadas que todavía no tienen envío de cierre (para planificar revisitas)."},
        {"Hoja": "Alertas", "Contenido": "Envíos con duración fuera de rango, no completos, aplicación no individual o sin consentimiento. (Si el aplicador conocía el grupo se ve en Aplicadores y en el resumen.)"},
        {"Hoja": "Aplicadores", "Contenido": "Indicadores por aplicador (estudiantes)."},
        {"Hoja": "", "Contenido": ""},
        {"Hoja": "Generado", "Contenido": f"{now:%Y-%m-%d %H:%M} (hora Argentina). Marco {frame_dir.name}. Decisiones conservadas: {carried_dup} (duplicados), {carried_match} (vínculos)."},
        {"Hoja": "Confidencial", "Contenido": "Contiene nombres, DNI e IDs. No publicar, no subir a GitHub, no reenviar fuera del equipo autorizado. No incluye el grupo de tratamiento."},
    ]
    write_sheet(wb, "Leeme", readme)
    write_sheet(wb, "Resumen", summary)
    write_sheet(wb, "Duplicados", duplicates, decision_cols=DUP_DECISION_COLS)
    write_sheet(wb, "Posibles_no_vinculados", matches, decision_cols=MATCH_DECISION_COLS)
    write_sheet(wb, "Pendientes", pending)
    write_sheet(wb, "Alertas", alerts)
    write_sheet(wb, "Aplicadores", enumerators)
    wb.save(path)
    os.chmod(path, 0o600)
    n_decisions = write_decisions_csv(args.output_dir / "decisiones_cierre.csv", duplicates, matches)

    md = summary_markdown(now, data, config, frames, summary, duplicates, matches, pending, alerts)
    md_path = args.output_dir / f"resumen_cierre_{now:%Y-%m-%d}.md"
    md_path.write_text(md, encoding="utf-8")
    print(md)
    print(f"Workbook: {path}")
    print(f"Summary:  {md_path}")
    print(f"Decisions for cleaning: {args.output_dir / 'decisiones_cierre.csv'} ({n_decisions} reviewed rows)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
