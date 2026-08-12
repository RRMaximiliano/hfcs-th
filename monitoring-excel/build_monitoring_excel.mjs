#!/usr/bin/env node

import fs from "node:fs/promises";
import path from "node:path";
import process from "node:process";
import { fileURLToPath } from "node:url";
import { SpreadsheetFile, Workbook } from "@oai/artifact-tool";

const HERE = path.dirname(fileURLToPath(import.meta.url));
const ROOT = path.resolve(HERE, "..");
const FORMS_PATH = path.join(ROOT, "config", "forms.json");
const ENV_PATH = path.join(ROOT, ".env");

const ROLE_LABELS = {
  students: "Estudiantes",
  teachers: "Docentes",
  directors: "Directivos",
};

const ROLE_ORDER = { students: 0, teachers: 1, directors: 2 };
const DISPLAY_FIELDS = {
  students: ["student_display_label", "student_dni_matched_label", "student_manual_name"],
  teachers: ["teacher_display_name", "teacher_dni_matched_label", "teacher_manual_name"],
  directors: ["director_display_name", "director_manual_name", "respondent_role_label"],
};

const DNI_FIELDS = {
  students: ["student_dni_normalized", "student_dni_input"],
  teachers: ["teacher_dni_normalized", "teacher_dni_input"],
  directors: ["director_dni_normalized", "director_dni_input"],
};

const COLORS = {
  navy: "#17324D",
  blue: "#2F6690",
  paleBlue: "#EAF2F8",
  teal: "#2A7F62",
  paleTeal: "#E8F4EF",
  gold: "#D4A72C",
  paleGold: "#FFF4CC",
  red: "#B54747",
  paleRed: "#FBE9E7",
  ink: "#1F2933",
  muted: "#667085",
  line: "#D8DEE4",
  white: "#FFFFFF",
};

function parseArgs(argv) {
  const args = { sample: false, output: "", outputDir: "", previewDir: "", province: "" };
  for (let index = 0; index < argv.length; index += 1) {
    const arg = argv[index];
    if (arg === "--sample") args.sample = true;
    else if (arg === "--by-province") continue;
    else if (arg === "--province") args.province = argv[++index] || "";
    else if (arg === "--output") args.output = argv[++index] || "";
    else if (arg === "--output-dir") args.outputDir = argv[++index] || "";
    else if (arg === "--preview-dir") args.previewDir = argv[++index] || "";
    else if (arg === "--help" || arg === "-h") {
      console.log("Uso: node build_monitoring_excel.mjs [--sample] [--province nombre] [--output archivo.xlsx | --output-dir directorio] [--preview-dir directorio]");
      process.exit(0);
    } else {
      throw new Error(`Opción desconocida: ${arg}`);
    }
  }
  if (!args.province && args.output) throw new Error("--output requiere --province. Para todos los libros provinciales use --output-dir.");
  if (args.province && args.outputDir) throw new Error("--output-dir crea todos los libros provinciales; use --output con --province.");
  return args;
}

async function loadDotenv(filePath) {
  let text;
  try {
    text = await fs.readFile(filePath, "utf8");
  } catch (error) {
    if (error.code === "ENOENT") return;
    throw error;
  }
  for (const rawLine of text.split(/\r?\n/)) {
    const line = rawLine.trim();
    if (!line || line.startsWith("#") || !line.includes("=")) continue;
    const splitAt = line.indexOf("=");
    const key = line.slice(0, splitAt).trim();
    let value = line.slice(splitAt + 1).trim();
    if ((value.startsWith('"') && value.endsWith('"')) || (value.startsWith("'") && value.endsWith("'"))) {
      value = value.slice(1, -1);
    }
    if (/^[A-Z_][A-Z0-9_]*$/.test(key) && process.env[key] === undefined) process.env[key] = value;
  }
}

function expandEnv(value) {
  if (typeof value === "string") {
    const match = value.match(/^\$\{([A-Z0-9_]+)\}$/);
    return match ? (process.env[match[1]] || "") : value;
  }
  if (Array.isArray(value)) return value.map(expandEnv);
  if (value && typeof value === "object") {
    return Object.fromEntries(Object.entries(value).map(([key, item]) => [key, expandEnv(item)]));
  }
  return value;
}

async function fetchJson(url, token) {
  const response = await fetch(url, {
    headers: {
      Authorization: `Token ${token}`,
      Accept: "application/json",
      "User-Agent": "Todos-Humanos-HFC-Monitoring/1.0",
    },
  });
  if (!response.ok) {
    const detail = (await response.text()).slice(0, 300);
    throw new Error(`Kobo respondió HTTP ${response.status} para ${url}: ${detail}`);
  }
  return response.json();
}

async function fetchKoboRows(apiBase, assetUid, token) {
  let dataUrl = `${apiBase.replace(/\/$/, "")}/api/v2/assets/${assetUid}/data/?limit=1000`;
  const rows = [];
  while (dataUrl) {
    const page = await fetchJson(dataUrl, token);
    if (Array.isArray(page)) {
      rows.push(...page);
      dataUrl = "";
    } else {
      rows.push(...(page.results || []));
      dataUrl = page.next || "";
    }
  }
  return rows;
}

function scalar(value) {
  if (value === null || value === undefined) return "";
  if (typeof value === "object") return JSON.stringify(value);
  return String(value).replace(/\s+/g, " ").trim();
}

function findValue(row, candidates = []) {
  for (const candidate of candidates) {
    const direct = scalar(row[candidate]);
    if (direct) return direct;
    for (const [key, value] of Object.entries(row)) {
      if (key.split("/").at(-1) === candidate && scalar(value)) return scalar(value);
    }
  }
  return "";
}

function normalizeIdentifier(value) {
  return scalar(value).toUpperCase().replace(/[^A-Z0-9]/g, "");
}

function normalizeProvince(value) {
  const cleaned = scalar(value);
  if (!cleaned) return "Sin provincia";
  const lookupKey = cleaned.replace(/[_-]+/g, " ").replace(/\s+/g, " ").toLocaleLowerCase("es");
  const aliases = new Map([
    ["buenos aires", "Buenos Aires"], ["caba", "CABA"], ["catamarca", "Catamarca"],
    ["chaco", "Chaco"], ["chubut", "Chubut"], ["cordoba", "Córdoba"], ["córdoba", "Córdoba"],
    ["corrientes", "Corrientes"], ["entre rios", "Entre Ríos"], ["entre ríos", "Entre Ríos"],
    ["formosa", "Formosa"], ["jujuy", "Jujuy"], ["la pampa", "La Pampa"], ["la rioja", "La Rioja"],
    ["mendoza", "Mendoza"], ["misiones", "Misiones"], ["neuquen", "Neuquén"], ["neuquén", "Neuquén"],
    ["rio negro", "Río Negro"], ["río negro", "Río Negro"], ["salta", "Salta"], ["san juan", "San Juan"],
    ["san luis", "San Luis"], ["santa cruz", "Santa Cruz"], ["santa fe", "Santa Fe"],
    ["santiago del estero", "Santiago del Estero"], ["tierra del fuego", "Tierra del Fuego"],
    ["tucuman", "Tucumán"], ["tucumán", "Tucumán"],
  ]);
  return aliases.get(lookupKey) || cleaned;
}

function validateProvinceMappings() {
  for (const value of ["la_rioja", "La Rioja", "LA-RIOJA"]) {
    if (normalizeProvince(value) !== "La Rioja") {
      throw new Error(`La normalización de provincia no reconoce ${value} como La Rioja.`);
    }
  }
}

function splitGrades(value, role) {
  if (!scalar(value)) return [];
  const teacherAliases = { "1": "2.º", "2": "3.º", "3": "4.º", "4": "Multigrado", "5": "Otro" };
  const studentAliases = {
    "1": "2.º", "01": "2.º",
    "2": "3.º", "02": "3.º",
    "3": "4.º", "03": "4.º",
    "4": "4.º", "04": "4.º",
    "2DO": "2.º", "2°": "2.º", "2º": "2.º", "2.º": "2.º",
    "3RO": "3.º", "3°": "3.º", "3º": "3.º", "3.º": "3.º",
    "4TO": "4.º", "4°": "4.º", "4º": "4.º", "4.º": "4.º",
  };
  const aliases = role === "teachers" ? teacherAliases : studentAliases;
  const result = [];
  for (const raw of scalar(value).split(/[\s,;|]+/)) {
    if (!raw) continue;
    const grade = aliases[raw.toUpperCase()] || raw;
    if (!result.includes(grade)) result.push(grade);
  }
  return result;
}

function validateGradeMappings() {
  const studentExpected = ["2.º", "3.º", "4.º"];
  const teacherExpected = ["2.º", "3.º", "4.º", "Multigrado", "Otro"];
  if (JSON.stringify(splitGrades("1 2 3", "students")) !== JSON.stringify(studentExpected)) {
    throw new Error("La codificación de grados de estudiantes no coincide con 1=2.º, 2=3.º, 3=4.º.");
  }
  if (JSON.stringify(splitGrades("1 2 3 4 5", "teachers")) !== JSON.stringify(teacherExpected)) {
    throw new Error("La codificación de grados de docentes no coincide con la especificación.");
  }
}

function parseTimestamp(value) {
  const text = scalar(value);
  if (!text) return null;
  const hasExplicitTimezone = /(?:Z|[+-]\d{2}:?\d{2})$/i.test(text);
  const isIsoDateTime = /^\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}/.test(text);
  const date = new Date(isIsoDateTime && !hasExplicitTimezone ? `${text}Z` : text);
  return Number.isNaN(date.getTime()) ? null : date;
}

function validateTimestampParsing() {
  const parsed = parseTimestamp("2026-08-12T14:30:00");
  if (!parsed || parsed.toISOString() !== "2026-08-12T14:30:00.000Z") {
    throw new Error("Las fechas de Kobo sin zona horaria deben interpretarse como UTC.");
  }
}

function parseNumber(value) {
  const number = Number.parseFloat(scalar(value).replace(",", "."));
  return Number.isFinite(number) ? number : null;
}

function compareText(left, right) {
  return scalar(left).localeCompare(scalar(right), "es", { numeric: true, sensitivity: "base" });
}

function slugify(value) {
  return scalar(value)
    .toLocaleLowerCase("es")
    .normalize("NFD")
    .replace(/[\u0300-\u036f]/g, "")
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/^-|-$/g, "") || "sin-provincia";
}

function scopedTitle(title, province) {
  return province ? `${title} — ${province}` : title;
}

function normalizeRecords(rawByRole, configByRole) {
  const records = [];
  for (const [role, rawRows] of Object.entries(rawByRole)) {
    const form = configByRole[role];
    const fields = form.fields;
    rawRows.forEach((row, index) => {
      const participantId = findValue(row, fields.participant_id);
      const participantKey = normalizeIdentifier(participantId);
      const timestamp = parseTimestamp(findValue(row, fields.timestamp));
      const grades = splitGrades(findValue(row, fields.grade), role);
      const schoolId = findValue(row, fields.school_id);
      records.push({
        role,
        roleLabel: ROLE_LABELS[role] || role,
        participantId,
        participantKey,
        documentId: findValue(row, DNI_FIELDS[role] || []),
        displayName: findValue(row, DISPLAY_FIELDS[role] || []),
        schoolId,
        schoolName: findValue(row, fields.school_name) || schoolId || "Escuela sin identificar",
        province: normalizeProvince(findValue(row, fields.province)),
        grades,
        gradeLabel: grades.length ? grades.join(", ") : (role === "directors" ? "No aplica" : "Sin grado"),
        enumerator: findValue(row, fields.enumerator) || "Sin aplicador",
        timestamp,
        timestampIso: timestamp ? timestamp.toISOString() : "",
        durationMinutes: parseNumber(findValue(row, fields.duration_minutes)),
        durationLong: ["1", "true", "yes", "si", "sí"].includes(findValue(row, fields.duration_flag_long).toLowerCase()),
        koboId: findValue(row, ["_id"]) || `${role}-${index + 1}`,
        koboUuid: findValue(row, ["_uuid", "meta/instanceID", "instanceID"]),
        missingId: !participantKey,
        missingSchool: !schoolId,
        duplicate: false,
        duplicateGroup: "",
        duplicatePosition: null,
        duplicateGroupSize: 0,
      });
    });
  }
  return records;
}

function duplicateKey(record, configByRole) {
  if (record.missingId) return "";
  const scope = configByRole[record.role].duplicate_scope || ["participant_key"];
  const parts = [record.role];
  for (const field of scope) {
    const jsField = field === "participant_key" ? "participantKey" : field === "school_id" ? "schoolId" : field;
    parts.push(scalar(record[jsField]));
  }
  return JSON.stringify(parts);
}

function applyDuplicateFlags(records, configByRole) {
  const groups = new Map();
  for (const record of records) {
    const key = duplicateKey(record, configByRole);
    if (!key) continue;
    if (!groups.has(key)) groups.set(key, []);
    groups.get(key).push(record);
  }
  const duplicated = [...groups.values()]
    .filter((group) => group.length > 1)
    .sort((a, b) => {
      const firstA = a[0];
      const firstB = b[0];
      return ROLE_ORDER[firstA.role] - ROLE_ORDER[firstB.role]
        || compareText(firstA.province, firstB.province)
        || compareText(firstA.schoolName, firstB.schoolName)
        || compareText(firstA.participantKey, firstB.participantKey);
    });
  duplicated.forEach((group, groupIndex) => {
    const groupId = `DUP-${String(groupIndex + 1).padStart(3, "0")}`;
    group.sort((a, b) => (a.timestamp?.getTime() || 0) - (b.timestamp?.getTime() || 0) || compareText(a.koboId, b.koboId));
    group.forEach((record, recordIndex) => {
      record.duplicate = true;
      record.duplicateGroup = groupId;
      record.duplicatePosition = recordIndex + 1;
      record.duplicateGroupSize = group.length;
    });
  });
  return duplicated;
}

function uniqueBy(values, keyFn) {
  return new Set(values.map(keyFn).filter(Boolean)).size;
}

function summarizeRoles(records, duplicateGroups) {
  return Object.keys(ROLE_LABELS).map((role) => {
    const roleRecords = records.filter((record) => record.role === role);
    const groups = duplicateGroups.filter((group) => group[0].role === role);
    return {
      role,
      label: ROLE_LABELS[role],
      submissions: roleRecords.length,
      unique: uniqueBy(roleRecords, (record) => record.participantKey),
      duplicateGroups: groups.length,
      duplicateExtras: groups.reduce((sum, group) => sum + group.length - 1, 0),
      missingId: roleRecords.filter((record) => record.missingId).length,
      missingSchool: roleRecords.filter((record) => record.missingSchool).length,
      lastSubmission: roleRecords.map((record) => record.timestamp).filter(Boolean).sort((a, b) => b - a)[0] || null,
    };
  });
}

function summarizeSchoolGrade(records, configByRole) {
  const groups = new Map();
  for (const record of records) {
    const gradeList = record.grades.length ? record.grades : [record.role === "directors" ? "No aplica" : "Sin grado"];
    for (const grade of gradeList) {
      const key = JSON.stringify([record.province, record.schoolId, record.schoolName, grade, record.role]);
      if (!groups.has(key)) groups.set(key, { grade, records: [] });
      groups.get(key).records.push(record);
    }
  }
  return [...groups.values()].map(({ grade, records: group }) => {
    const first = group[0];
    const duplicateGroupIds = new Set(group.filter((record) => record.duplicate).map((record) => record.duplicateGroup));
    const extraSubmissions = [...duplicateGroupIds].reduce((sum, groupId) => {
      const inGroup = records.filter((record) => record.duplicateGroup === groupId);
      return sum + Math.max(0, inGroup.length - 1);
    }, 0);
    return {
      province: first.province,
      schoolName: first.schoolName,
      schoolId: first.schoolId,
      grade,
      role: first.role,
      roleLabel: first.roleLabel,
      submissions: group.length,
      unique: uniqueBy(group, (record) => duplicateKey(record, configByRole)),
      duplicateGroups: duplicateGroupIds.size,
      duplicateExtras: extraSubmissions,
      missingId: group.filter((record) => record.missingId).length,
      lastSubmission: group.map((record) => record.timestamp).filter(Boolean).sort((a, b) => b - a)[0] || null,
    };
  }).sort((a, b) => compareText(a.province, b.province)
    || compareText(a.schoolName, b.schoolName)
    || compareText(a.grade, b.grade)
    || ROLE_ORDER[a.role] - ROLE_ORDER[b.role]);
}

function summarizeEnumerators(records) {
  const groups = new Map();
  for (const record of records) {
    if (!groups.has(record.enumerator)) groups.set(record.enumerator, []);
    groups.get(record.enumerator).push(record);
  }
  return [...groups.entries()].map(([enumerator, group]) => ({
    enumerator,
    submissions: group.length,
    students: group.filter((record) => record.role === "students").length,
    teachers: group.filter((record) => record.role === "teachers").length,
    directors: group.filter((record) => record.role === "directors").length,
    duplicateRows: group.filter((record) => record.duplicate).length,
    duplicateGroups: new Set(group.filter((record) => record.duplicate).map((record) => record.duplicateGroup)).size,
    missingId: group.filter((record) => record.missingId).length,
    schools: uniqueBy(group, (record) => record.schoolId),
    lastSubmission: group.map((record) => record.timestamp).filter(Boolean).sort((a, b) => b - a)[0] || null,
  })).sort((a, b) => b.submissions - a.submissions || compareText(a.enumerator, b.enumerator));
}

function titleBlock(sheet, title, subtitle, endColumn, generatedAt) {
  sheet.showGridLines = false;
  sheet.mergeCells(`A1:${endColumn}1`);
  sheet.getRange("A1").values = [[title]];
  sheet.getRange(`A1:${endColumn}1`).format = {
    fill: COLORS.navy,
    font: { color: COLORS.white, bold: true, size: 18 },
    verticalAlignment: "center",
  };
  sheet.getRange("1:1").format.rowHeight = 32;
  sheet.mergeCells(`A2:${endColumn}2`);
  sheet.getRange("A2").values = [[subtitle]];
  sheet.getRange(`A2:${endColumn}2`).format = {
    fill: COLORS.paleBlue,
    font: { color: COLORS.ink, italic: true },
    wrapText: true,
    verticalAlignment: "center",
  };
  sheet.getRange("2:2").format.rowHeight = 30;
  sheet.mergeCells(`A3:${endColumn}3`);
  sheet.getRange("A3").values = [[`Actualizado: ${generatedAt.toISOString().replace("T", " ").replace(".000Z", " UTC")}`]];
  sheet.getRange(`A3:${endColumn}3`).format = { font: { color: COLORS.muted, size: 9 } };
}

function styleHeader(range) {
  range.format = {
    fill: COLORS.blue,
    font: { color: COLORS.white, bold: true },
    verticalAlignment: "center",
    wrapText: true,
    borders: { preset: "inside", style: "thin", color: "#FFFFFF" },
  };
  range.format.rowHeight = 30;
}

function styleDataArea(range) {
  range.format = {
    font: { color: COLORS.ink, size: 10 },
    verticalAlignment: "center",
    borders: { insideHorizontal: { style: "thin", color: COLORS.line } },
  };
}

function setColumnWidths(sheet, widths) {
  widths.forEach(([column, width]) => {
    sheet.getRange(`${column}:${column}`).format.columnWidth = width;
  });
}

function addTable(sheet, range, name, style = "TableStyleMedium2") {
  const table = sheet.tables.add(range, true, name);
  table.style = style;
  table.showFilterButton = true;
  return table;
}

function buildSummarySheet(workbook, generatedAt, roleSummary, province = "") {
  const sheet = workbook.worksheets.add("Resumen");
  titleBlock(sheet, scopedTitle("Monitoreo de recolección HFC", province), "Archivo confidencial para coordinación de campo. Los duplicados son señales de revisión, no decisiones de eliminación.", "H", generatedAt);
  sheet.getRange("A5:B5").merge();
  sheet.getRange("A5").values = [["Envíos recibidos"]];
  sheet.getRange("C5:D5").merge();
  sheet.getRange("C5").values = [["Casos con ID"]];
  sheet.getRange("E5:F5").merge();
  sheet.getRange("E5").values = [["Grupos duplicados"]];
  sheet.getRange("G5:H5").merge();
  sheet.getRange("G5").values = [["Envíos adicionales"]];
  sheet.getRange("A6:B7").merge();
  sheet.getRange("C6:D7").merge();
  sheet.getRange("E6:F7").merge();
  sheet.getRange("G6:H7").merge();
  sheet.getRange("A6").formulas = [["=SUM(B11:B13)"]];
  sheet.getRange("C6").formulas = [["=SUM(C11:C13)"]];
  sheet.getRange("E6").formulas = [["=SUM(D11:D13)"]];
  sheet.getRange("G6").formulas = [["=SUM(E11:E13)"]];
  sheet.getRange("A5:H5").format = { fill: COLORS.navy, font: { color: COLORS.white, bold: true }, horizontalAlignment: "center" };
  sheet.getRange("A6:H7").format = { fill: COLORS.paleTeal, font: { color: COLORS.navy, bold: true, size: 20 }, horizontalAlignment: "center", verticalAlignment: "center", numberFormat: "#,##0", borders: { preset: "outside", style: "thin", color: COLORS.line } };

  const roleRows = roleSummary.map((row) => [row.label, row.submissions, row.unique, row.duplicateGroups, row.duplicateExtras, row.missingId, row.missingSchool, row.lastSubmission]);
  sheet.getRange("A10:H10").values = [["Rol", "Envíos", "Casos con ID", "Grupos duplicados", "Envíos adicionales", "Sin ID", "Sin escuela", "Último envío (UTC)"]];
  sheet.getRange(`A11:H${10 + roleRows.length}`).values = roleRows;
  styleHeader(sheet.getRange("A10:H10"));
  styleDataArea(sheet.getRange(`A11:H${10 + roleRows.length}`));
  sheet.getRange(`B11:G${10 + roleRows.length}`).format.numberFormat = "#,##0";
  sheet.getRange(`H11:H${10 + roleRows.length}`).format.numberFormat = "yyyy-mm-dd hh:mm";
  addTable(sheet, `A10:H${10 + roleRows.length}`, "ResumenRoles", "TableStyleMedium2");

  sheet.getRange("A16:H16").merge();
  sheet.getRange("A16").values = [["Cómo usar este archivo"]];
  sheet.getRange("A16:H16").format = { fill: COLORS.gold, font: { color: COLORS.ink, bold: true } };
  const instructions = [
    ["1", "Revise primero la hoja Posibles duplicados; cada fila es un envío y cada grupo comparte un código DUP-###."],
    ["2", "No asuma que el primer o el último envío es el correcto. Confirme con el aplicador y complete las columnas de revisión."],
    ["3", "Use Recibidos para buscar por escuela, grado, rol, identificador o aplicador."],
    ["4", "Conteos escuela-grado resume lo recibido. Docentes multigrado aparecen en cada grado seleccionado, por lo que esos renglones no deben sumarse para obtener el total general."],
    ["5", "Guarde la copia revisada con otro nombre antes de generar una actualización nueva."],
  ];
  sheet.getRange("A17:B21").values = instructions;
  sheet.getRange("A17:A21").format = { fill: COLORS.paleGold, font: { bold: true, color: COLORS.navy }, horizontalAlignment: "center" };
  sheet.getRange("B17:H21").merge(true);
  instructions.forEach((row, index) => sheet.getRange(`B${17 + index}`).values = [[row[1]]] );
  sheet.getRange("B17:H21").format = { wrapText: true, verticalAlignment: "center", borders: { insideHorizontal: { style: "thin", color: COLORS.line } } };
  sheet.getRange("17:21").format.rowHeight = 28;
  setColumnWidths(sheet, [["A", 10], ["B", 16], ["C", 16], ["D", 16], ["E", 16], ["F", 14], ["G", 14], ["H", 22]]);
  sheet.freezePanes.freezeRows(3);
  return sheet;
}

function buildDuplicatesSheet(workbook, generatedAt, duplicateGroups, province = "") {
  const sheet = workbook.worksheets.add("Posibles duplicados");
  titleBlock(sheet, scopedTitle("Posibles duplicados para revisar", province), "Cada envío del grupo aparece por separado. Complete las cinco columnas amarillas sin borrar la información descargada de Kobo.", "S", generatedAt);
  const headers = ["Grupo", "Rol", "Identificador de caso", "DNI reportado", "Nombre / etiqueta", "Provincia", "Escuela", "Código escuela", "Grado(s)", "Aplicador", "Fecha/hora UTC", "ID Kobo", "UUID Kobo", "Orden en grupo", "Estado revisión", "¿Conservar?", "Confirmado por", "Fecha revisión", "Observaciones"];
  const rows = duplicateGroups.flatMap((group) => group.map((record) => [
    record.duplicateGroup, record.roleLabel, record.participantId, record.documentId, record.displayName, record.province,
    record.schoolName, record.schoolId, record.gradeLabel, record.enumerator, record.timestamp,
    record.koboId, record.koboUuid, `${record.duplicatePosition} de ${record.duplicateGroupSize}`,
    "Pendiente", "Sin revisar", "", null, "",
  ]));
  sheet.getRange("A5:S5").values = [headers];
  styleHeader(sheet.getRange("A5:S5"));
  if (rows.length) {
    const endRow = 5 + rows.length;
    sheet.getRange(`A6:S${endRow}`).values = rows;
    styleDataArea(sheet.getRange(`A6:S${endRow}`));
    sheet.getRange(`K6:K${endRow}`).format.numberFormat = "yyyy-mm-dd hh:mm";
    sheet.getRange(`R6:R${endRow}`).format.numberFormat = "yyyy-mm-dd";
    sheet.getRange(`C6:D${endRow}`).format.numberFormat = "@";
    sheet.getRange(`L6:M${endRow}`).format.numberFormat = "@";
    sheet.getRange(`O6:S${endRow}`).format.fill = COLORS.paleGold;
    sheet.getRange(`O6:O${endRow}`).dataValidation = { rule: { type: "list", values: ["Pendiente", "En revisión", "Resuelto"] } };
    sheet.getRange(`P6:P${endRow}`).dataValidation = { rule: { type: "list", values: ["Sin revisar", "Sí", "No", "Repetición válida"] } };
    sheet.getRange(`O6:O${endRow}`).conditionalFormats.add("containsText", { text: "Pendiente", format: { fill: COLORS.paleRed, font: { color: COLORS.red, bold: true } } });
    sheet.getRange(`P6:P${endRow}`).conditionalFormats.add("containsText", { text: "Sí", format: { fill: COLORS.paleTeal, font: { color: COLORS.teal, bold: true } } });
    sheet.getRange(`P6:P${endRow}`).conditionalFormats.add("containsText", { text: "No", format: { fill: COLORS.paleRed, font: { color: COLORS.red, bold: true } } });
    addTable(sheet, `A5:S${endRow}`, "PosiblesDuplicados", "TableStyleMedium3");
  }
  setColumnWidths(sheet, [["A", 12], ["B", 14], ["C", 28], ["D", 17], ["E", 28], ["F", 16], ["G", 28], ["H", 15], ["I", 22], ["J", 16], ["K", 20], ["L", 13], ["M", 34], ["N", 15], ["O", 17], ["P", 18], ["Q", 20], ["R", 16], ["S", 36]]);
  sheet.freezePanes.freezeRows(5);
  sheet.freezePanes.freezeColumns(2);
  return sheet;
}

function buildReceivedSheet(workbook, generatedAt, records, province = "") {
  const sheet = workbook.worksheets.add("Recibidos");
  titleBlock(sheet, scopedTitle("Todos los envíos recibidos", province), "Listado confidencial ordenado por provincia, escuela, grado, rol y fecha. Use los filtros para preparar listas de campo.", "P", generatedAt);
  const headers = ["Provincia", "Escuela", "Código escuela", "Grado(s)", "Rol", "Identificador de caso", "DNI reportado", "Nombre / etiqueta", "Aplicador", "Fecha/hora UTC", "ID Kobo", "UUID Kobo", "Grupo duplicado", "Alerta", "Duración (min)", "Control adicional"];
  const sorted = [...records].sort((a, b) => compareText(a.province, b.province)
    || compareText(a.schoolName, b.schoolName)
    || compareText(a.gradeLabel, b.gradeLabel)
    || ROLE_ORDER[a.role] - ROLE_ORDER[b.role]
    || (a.timestamp?.getTime() || 0) - (b.timestamp?.getTime() || 0));
  const rows = sorted.map((record) => [
    record.province, record.schoolName, record.schoolId, record.gradeLabel, record.roleLabel,
    record.participantId, record.documentId, record.displayName, record.enumerator, record.timestamp, record.koboId,
    record.koboUuid, record.duplicateGroup, record.duplicate ? "POSIBLE DUPLICADO" : "",
    record.durationMinutes, [record.missingId ? "Sin ID" : "", record.missingSchool ? "Sin escuela" : "", record.durationLong ? "Duración alta" : ""].filter(Boolean).join("; "),
  ]);
  sheet.getRange("A5:P5").values = [headers];
  styleHeader(sheet.getRange("A5:P5"));
  if (rows.length) {
    const endRow = 5 + rows.length;
    sheet.getRange(`A6:P${endRow}`).values = rows;
    styleDataArea(sheet.getRange(`A6:P${endRow}`));
    sheet.getRange(`F6:G${endRow}`).format.numberFormat = "@";
    sheet.getRange(`J6:J${endRow}`).format.numberFormat = "yyyy-mm-dd hh:mm";
    sheet.getRange(`K6:L${endRow}`).format.numberFormat = "@";
    sheet.getRange(`O6:O${endRow}`).format.numberFormat = "0.0";
    sheet.getRange(`N6:N${endRow}`).conditionalFormats.add("containsText", { text: "POSIBLE DUPLICADO", format: { fill: COLORS.paleRed, font: { color: COLORS.red, bold: true } } });
    sheet.getRange(`P6:P${endRow}`).conditionalFormats.add("notContainsBlanks", { format: { fill: COLORS.paleGold, font: { color: COLORS.ink } } });
    addTable(sheet, `A5:P${endRow}`, "EnviosRecibidos", "TableStyleMedium2");
  }
  setColumnWidths(sheet, [["A", 16], ["B", 29], ["C", 15], ["D", 24], ["E", 14], ["F", 28], ["G", 17], ["H", 30], ["I", 16], ["J", 20], ["K", 13], ["L", 34], ["M", 14], ["N", 21], ["O", 14], ["P", 28]]);
  sheet.freezePanes.freezeRows(5);
  sheet.freezePanes.freezeColumns(3);
  return sheet;
}

function buildCountsSheet(workbook, generatedAt, rows, province = "") {
  const sheet = workbook.worksheets.add("Conteos escuela-grado");
  titleBlock(sheet, scopedTitle("Conteos por escuela y grado", province), "Docentes con varios grados aparecen una vez en cada grado seleccionado; no sume esos renglones para reconstruir el total general.", "K", generatedAt);
  const headers = ["Provincia", "Escuela", "Código escuela", "Grado", "Rol", "Envíos", "Casos únicos", "Grupos duplicados", "Envíos adicionales", "Sin ID", "Último envío (UTC)"];
  const values = rows.map((row) => [row.province, row.schoolName, row.schoolId, row.grade, row.roleLabel, row.submissions, row.unique, row.duplicateGroups, row.duplicateExtras, row.missingId, row.lastSubmission]);
  sheet.getRange("A5:K5").values = [headers];
  styleHeader(sheet.getRange("A5:K5"));
  if (values.length) {
    const endRow = 5 + values.length;
    sheet.getRange(`A6:K${endRow}`).values = values;
    styleDataArea(sheet.getRange(`A6:K${endRow}`));
    sheet.getRange(`F6:J${endRow}`).format.numberFormat = "#,##0";
    sheet.getRange(`K6:K${endRow}`).format.numberFormat = "yyyy-mm-dd hh:mm";
    sheet.getRange(`I6:I${endRow}`).conditionalFormats.add("cellIs", { operator: "greaterThan", formula: 0, format: { fill: COLORS.paleRed, font: { color: COLORS.red, bold: true } } });
    addTable(sheet, `A5:K${endRow}`, "ConteosEscuelaGrado", "TableStyleMedium2");
  }
  setColumnWidths(sheet, [["A", 16], ["B", 30], ["C", 15], ["D", 24], ["E", 14], ["F", 12], ["G", 14], ["H", 18], ["I", 19], ["J", 12], ["K", 21]]);
  sheet.freezePanes.freezeRows(5);
  sheet.freezePanes.freezeColumns(3);
  return sheet;
}

function buildEnumeratorsSheet(workbook, generatedAt, rows, province = "") {
  const sheet = workbook.worksheets.add("Aplicadores");
  titleBlock(sheet, scopedTitle("Carga por aplicador", province), "Permite identificar quién debe apoyar la revisión de cada grupo duplicado y qué escuelas cargó.", "J", generatedAt);
  const headers = ["Aplicador", "Envíos", "Estudiantes", "Docentes", "Directivos", "Filas en grupos duplicados", "Grupos duplicados", "Sin ID", "Escuelas", "Último envío (UTC)"];
  const values = rows.map((row) => [row.enumerator, row.submissions, row.students, row.teachers, row.directors, row.duplicateRows, row.duplicateGroups, row.missingId, row.schools, row.lastSubmission]);
  sheet.getRange("A5:J5").values = [headers];
  styleHeader(sheet.getRange("A5:J5"));
  if (values.length) {
    const endRow = 5 + values.length;
    sheet.getRange(`A6:J${endRow}`).values = values;
    styleDataArea(sheet.getRange(`A6:J${endRow}`));
    sheet.getRange(`B6:I${endRow}`).format.numberFormat = "#,##0";
    sheet.getRange(`J6:J${endRow}`).format.numberFormat = "yyyy-mm-dd hh:mm";
    sheet.getRange(`F6:F${endRow}`).conditionalFormats.add("cellIs", { operator: "greaterThan", formula: 0, format: { fill: COLORS.paleGold, font: { color: COLORS.ink, bold: true } } });
    addTable(sheet, `A5:J${endRow}`, "CargaAplicadores", "TableStyleMedium2");
  }
  setColumnWidths(sheet, [["A", 20], ["B", 12], ["C", 14], ["D", 12], ["E", 12], ["F", 23], ["G", 18], ["H", 12], ["I", 12], ["J", 21]]);
  sheet.freezePanes.freezeRows(5);
  return sheet;
}

async function loadRawData(args, configByRole) {
  if (args.sample) {
    const entries = await Promise.all(Object.keys(configByRole).map(async (role) => {
      const text = await fs.readFile(path.join(ROOT, "tests", "fixtures", `${role}.json`), "utf8");
      return [role, JSON.parse(text)];
    }));
    return Object.fromEntries(entries);
  }
  const token = scalar(process.env.KOBO_API_TOKEN);
  if (!token) throw new Error(`Falta KOBO_API_TOKEN en ${ENV_PATH}`);
  const config = JSON.parse(await fs.readFile(FORMS_PATH, "utf8"));
  const apiBase = config.api_base || "https://eu.kobotoolbox.org";
  const entries = [];
  for (const [role, form] of Object.entries(configByRole)) {
    if (!form.asset_uid) throw new Error(`Falta ${form.asset_uid_env || `asset UID de ${role}`} en ${ENV_PATH}`);
    entries.push([role, await fetchKoboRows(apiBase, form.asset_uid, token)]);
  }
  return Object.fromEntries(entries);
}

async function buildAndSaveWorkbook({ records, configByRole, generatedAt, province = "", outputPath, previewDir = "" }) {
  const scopedRecords = records.map((record) => ({
    ...record,
    grades: [...record.grades],
    duplicate: false,
    duplicateGroup: "",
    duplicatePosition: null,
    duplicateGroupSize: 0,
  }));
  const duplicateGroups = applyDuplicateFlags(scopedRecords, configByRole);
  const roleSummary = summarizeRoles(scopedRecords, duplicateGroups);
  const schoolGradeSummary = summarizeSchoolGrade(scopedRecords, configByRole);
  const enumeratorSummary = summarizeEnumerators(scopedRecords);

  const workbook = Workbook.create();
  buildSummarySheet(workbook, generatedAt, roleSummary, province);
  buildDuplicatesSheet(workbook, generatedAt, duplicateGroups, province);
  buildReceivedSheet(workbook, generatedAt, scopedRecords, province);
  buildCountsSheet(workbook, generatedAt, schoolGradeSummary, province);
  buildEnumeratorsSheet(workbook, generatedAt, enumeratorSummary, province);

  const summaryInspection = await workbook.inspect({
    kind: "table",
    range: "Resumen!A1:H21",
    include: "values,formulas",
    tableMaxRows: 21,
    tableMaxCols: 8,
    maxChars: 6000,
  });
  const errorInspection = await workbook.inspect({
    kind: "match",
    searchTerm: "#REF!|#DIV/0!|#VALUE!|#NAME\\?|#N/A",
    options: { useRegex: true, maxResults: 100 },
    summary: "errores de fórmula",
    maxChars: 2000,
  });

  if (previewDir) {
    await fs.mkdir(previewDir, { recursive: true });
    const previewRanges = {
      "Resumen": "A1:H21",
      "Posibles duplicados": `A1:S${Math.min(25, 5 + duplicateGroups.flat().length)}`,
      "Recibidos": `A1:P${Math.min(22, 5 + scopedRecords.length)}`,
      "Conteos escuela-grado": `A1:K${Math.min(22, 5 + schoolGradeSummary.length)}`,
      "Aplicadores": `A1:J${Math.min(22, 5 + enumeratorSummary.length)}`,
    };
    for (const [sheetName, range] of Object.entries(previewRanges)) {
      const preview = await workbook.render({ sheetName, range, scale: 1.2, format: "png" });
      await fs.writeFile(path.join(previewDir, `${slugify(sheetName)}.png`), new Uint8Array(await preview.arrayBuffer()));
    }
  }

  await fs.mkdir(path.dirname(outputPath), { recursive: true });
  const file = await SpreadsheetFile.exportXlsx(workbook);
  await file.save(outputPath);
  return {
    province,
    outputPath,
    submissions: scopedRecords.length,
    roleSummary,
    duplicateGroups: duplicateGroups.length,
    duplicateRows: duplicateGroups.flat().length,
    duplicateExtras: duplicateGroups.reduce((sum, group) => sum + group.length - 1, 0),
    summaryInspectionLength: summaryInspection.ndjson.length,
    formulaErrors: errorInspection.ndjson,
  };
}

async function main() {
  const args = parseArgs(process.argv.slice(2));
  validateGradeMappings();
  validateProvinceMappings();
  validateTimestampParsing();
  await loadDotenv(ENV_PATH);
  const config = expandEnv(JSON.parse(await fs.readFile(FORMS_PATH, "utf8")));
  const configByRole = Object.fromEntries(config.forms.map((form) => [form.key, form]));
  const rawByRole = await loadRawData(args, configByRole);
  const records = normalizeRecords(rawByRole, configByRole);
  const generatedAt = new Date();
  const provinces = [...new Set(records.map((record) => record.province))].sort(compareText);
  const jobs = [];
  const defaultOutputDir = path.join(HERE, "outputs", "provinces", "current");
  if (args.province) {
    const matched = provinces.find((item) => compareText(item, args.province) === 0);
    if (!matched) throw new Error(`Provincia no encontrada: ${args.province}. Disponibles: ${provinces.join(", ")}`);
    jobs.push({
      province: matched,
      records: records.filter((record) => record.province === matched),
      outputPath: args.output ? path.resolve(HERE, args.output) : path.join(defaultOutputDir, `monitoreo_${slugify(matched)}.xlsx`),
      previewDir: args.previewDir ? path.resolve(HERE, args.previewDir) : "",
    });
  } else {
    const outputDir = args.outputDir ? path.resolve(HERE, args.outputDir) : defaultOutputDir;
    for (const province of provinces) {
      const provinceSlug = slugify(province);
      jobs.push({
        province,
        records: records.filter((record) => record.province === province),
        outputPath: path.join(outputDir, `monitoreo_${provinceSlug}.xlsx`),
        previewDir: args.previewDir ? path.join(path.resolve(HERE, args.previewDir), provinceSlug) : "",
      });
    }
  }

  const results = [];
  for (const job of jobs) {
    results.push(await buildAndSaveWorkbook({ ...job, configByRole, generatedAt }));
  }
  for (const result of results) {
    const totals = result.roleSummary.map((row) => `${row.label}: ${row.submissions}`).join("; ");
    console.log(`Libro creado (${result.province || "Nacional"}): ${result.outputPath}`);
    console.log(`Envíos: ${result.submissions} (${totals})`);
    console.log(`Grupos duplicados: ${result.duplicateGroups}; filas para revisar: ${result.duplicateRows}; envíos adicionales: ${result.duplicateExtras}`);
    console.log(`Verificación resumen: ${result.summaryInspectionLength} caracteres; errores: ${result.formulaErrors}`);
  }
}

main().catch((error) => {
  console.error(`ERROR: ${error.message}`);
  process.exitCode = 1;
});
