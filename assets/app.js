const ROLE_LABELS = {
  students: "Estudiantes",
  teachers: "Docentes",
  directors: "Directivos",
};

const VIEW_TITLES = {
  overview: "Resumen operativo",
  schools: "Estado por escuela",
  grades: "Estudiantes y docentes por grado",
  quality: "Calidad de datos",
  duplicates: "Posibles duplicados",
  enumerators: "Carga por aplicador",
  submissions: "Envíos recientes",
};

const state = {
  data: null,
  view: "overview",
  filters: { province: "", school: "", role: "", search: "" },
  exportRows: [],
  exportName: "datos",
};

const app = document.querySelector("#app-content");
const provinceFilter = document.querySelector("#province-filter");
const schoolFilter = document.querySelector("#school-filter");
const roleFilter = document.querySelector("#role-filter");
const searchFilter = document.querySelector("#search-filter");

function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

function int(value) {
  return new Intl.NumberFormat("es-AR", { maximumFractionDigits: 0 }).format(Number(value || 0));
}

function decimal(value) {
  if (value === null || value === undefined || value === "") return "Sin dato";
  return new Intl.NumberFormat("es-AR", { maximumFractionDigits: 1 }).format(Number(value));
}

function dateTime(value) {
  if (!value) return "Sin envíos";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return new Intl.DateTimeFormat("es-AR", {
    dateStyle: "short",
    timeStyle: "short",
    timeZone: "America/Argentina/Buenos_Aires",
  }).format(date);
}

function shortDate(value) {
  if (!value || value === "Sin fecha") return "Sin fecha";
  const [year, month, day] = value.split("-");
  return `${day}/${month}`;
}

function matches(value, query) {
  return String(value ?? "").toLocaleLowerCase("es").includes(query);
}

function selectedRoles() {
  return state.filters.role ? [state.filters.role] : Object.keys(ROLE_LABELS);
}

function filteredSchools() {
  const query = state.filters.search.toLocaleLowerCase("es");
  return state.data.schools.filter((row) => {
    const geography = (!state.filters.province || row.province === state.filters.province)
      && (!state.filters.school || row.school_id === state.filters.school);
    const population = !state.filters.role || row.roles[state.filters.role]?.submissions > 0;
    const text = !query || matches(`${row.school_name} ${row.school_id} ${row.province}`, query);
    return geography && population && text;
  });
}

function recordMatches(row) {
  const query = state.filters.search.toLocaleLowerCase("es");
  return (!state.filters.province || row.province === state.filters.province)
    && (!state.filters.school || row.school_id === state.filters.school)
    && (!state.filters.role || row.role === state.filters.role)
    && (!query || matches(`${row.school_name} ${row.school_id} ${row.case_ref} ${row.enumerator_ref} ${(row.grades || []).join(" ")}`, query));
}

function hasQuality() {
  return Boolean(state.data?.wave?.has_quality_checks);
}

function aggregateMetrics(schools, roles = selectedRoles()) {
  const result = { submissions: 0, unique: 0, linked: 0, duplicates: 0, missingId: 0, target: 0, schools: 0, quality: {} };
  for (const school of schools) {
    let hasData = false;
    for (const role of roles) {
      const metrics = school.roles[role] || {};
      result.submissions += metrics.submissions || 0;
      result.unique += metrics.unique_participants || 0;
      result.duplicates += metrics.duplicate_submissions || 0;
      result.missingId += metrics.missing_id || 0;
      result.target += metrics.target || 0;
      result.linked += metrics.linked_unique || 0;
      for (const [flag, count] of Object.entries(metrics.quality || {})) result.quality[flag] = (result.quality[flag] || 0) + count;
      hasData ||= (metrics.submissions || 0) > 0;
    }
    if (hasData) result.schools += 1;
  }
  return result;
}

function statusTag(label, tone = "") {
  return `<span class="status-tag ${tone}">${escapeHtml(label)}</span>`;
}

function emptyState(title, detail) {
  return `<div class="empty-state"><div><h2>${escapeHtml(title)}</h2><p>${escapeHtml(detail)}</p></div></div>`;
}

function table(columns, rows, emptyMessage) {
  if (!rows.length) return emptyState("Sin resultados", emptyMessage);
  const head = columns.map((column) => `<th class="${column.number ? "number" : ""}">${escapeHtml(column.label)}</th>`).join("");
  const body = rows.map((row) => `<tr>${columns.map((column) => {
    const value = column.value ? column.value(row) : row[column.key];
    const content = column.render ? column.render(row) : escapeHtml(value);
    return `<td class="${column.number ? "number" : ""}">${content}</td>`;
  }).join("")}</tr>`).join("");
  state.exportRows = rows.map((row) => Object.fromEntries(columns.map((column) => [column.label, column.value ? column.value(row) : row[column.key]])));
  return `<div class="table-wrap"><table><thead><tr>${head}</tr></thead><tbody>${body}</tbody></table></div>`;
}

function panelHeader(title, detail, downloadable = false) {
  return `<div class="panel-header"><div><h2>${escapeHtml(title)}</h2><p>${escapeHtml(detail)}</p></div>${downloadable ? '<div class="panel-actions"><button class="secondary-button" type="button" data-download>Descargar CSV</button></div>' : ""}</div>`;
}

function metricCard(label, value, detail, primary = false) {
  const textClass = /[A-Za-zÁÉÍÓÚáéíóú]/.test(String(value)) ? "is-text" : "";
  return `<article class="metric ${primary ? "primary" : ""}"><span class="metric-label">${escapeHtml(label)}</span><strong class="metric-value ${textClass}">${escapeHtml(value)}</strong><span class="metric-detail">${escapeHtml(detail)}</span></article>`;
}

function renderOverview() {
  const schools = filteredSchools();
  const metrics = aggregateMetrics(schools);
  const roleRows = Object.keys(ROLE_LABELS).map((role) => {
    const roleMetrics = aggregateMetrics(schools, [role]);
    const form = state.data.forms.find((item) => item.key === role) || {};
    const apiLabel = form.api_submission_total === null || form.api_submission_total === undefined
      ? "Total API pendiente"
      : `API: ${int(form.api_submission_total)}`;
    return `<div class="role-row"><span><strong>${ROLE_LABELS[role]}</strong><small class="cell-subtitle">${escapeHtml(apiLabel)}</small></span>
      <span class="role-stat"><b>${int(roleMetrics.submissions)}</b><small>envíos</small></span>
      <span class="role-stat"><b>${int(roleMetrics.unique)}</b><small>personas</small></span>
      <span class="role-stat"><b>${int(roleMetrics.duplicates)}</b><small>duplicados</small></span></div>`;
  }).join("");

  const daily = state.data.daily.filter((row) => row.date !== "Sin fecha").slice(-12);
  const maxDaily = Math.max(1, ...daily.map((row) => row.total));
  const bars = daily.map((row) => `<div class="chart-column" title="${escapeHtml(row.date)}: ${int(row.total)} envíos">
    <strong>${int(row.total)}</strong><div class="chart-bar" style="height:${Math.max(3, (row.total / maxDaily) * 150)}px"></div><span>${shortDate(row.date)}</span>
  </div>`).join("");

  const recentDuplicates = state.data.duplicates.filter(recordMatches).slice(0, 5);
  const duplicateList = recentDuplicates.length
    ? table([
        { label: "Caso", key: "case_ref", render: (row) => `<strong>${escapeHtml(row.case_ref)}</strong>` },
        { label: "Población", value: (row) => ROLE_LABELS[row.role] },
        { label: "Escuela", key: "school_name" },
        { label: "Extras", key: "extra_submissions", number: true },
      ], recentDuplicates, "No hay casos duplicados con los filtros actuales.")
    : emptyState("Sin duplicados", "No hay casos duplicados con los filtros actuales.");

  state.exportName = "resumen_duplicados";
  app.innerHTML = `<section class="view">
    <div class="metric-grid">
      ${metricCard("Envíos", int(metrics.submissions), `${int(metrics.schools)} escuelas con datos`, true)}
      ${metricCard("Personas únicas", int(metrics.unique), `${int(metrics.missingId)} sin ID`)}
      ${metricCard("Envíos duplicados", int(metrics.duplicates), "Registros adicionales sobre un mismo caso")}
      ${metrics.target ? (hasQuality()
        ? followUpCard(schools)
        : metricCard("Avance", `${int((metrics.unique / metrics.target) * 100)}%`, `${int(metrics.unique)} de ${int(metrics.target)}`)) : ""}
    </div>
    <div class="split-grid">
      <section class="panel">${panelHeader("Estado por población", "Envíos descargados, personas únicas y posibles duplicados")}
        <div class="role-list">${roleRows}</div>
        <p class="data-note">El total API permite detectar si una descarga quedó incompleta. En modo de muestra aparece como pendiente.</p>
      </section>
      <section class="panel">${panelHeader("Ritmo de recolección", "Total general de envíos por día")}
        ${daily.length ? `<div class="chart-wrap" aria-label="Envíos diarios">${bars}</div>` : emptyState("Sin fechas", "No se encontraron fechas válidas en los envíos.")}
      </section>
    </div>
    <section class="panel">${panelHeader("Casos para revisar", "Primeros posibles duplicados según los filtros", true)}${duplicateList}</section>
  </section>`;
}

function renderSchools() {
  const rows = filteredSchools();
  const roles = selectedRoles();
  const prepared = rows.map((row) => {
    const metrics = aggregateMetrics([row], roles);
    const reached = hasQuality() ? metrics.linked : metrics.unique;
    return { ...row, ...metrics, completion: metrics.target ? reached / metrics.target : null };
  });
  const hasTargets = prepared.some((row) => row.target > 0);
  state.exportName = "estado_por_escuela";
  const columns = [
    { label: "Provincia", key: "province" },
    { label: "Escuela", value: (row) => `${row.school_name} (${row.school_id || "sin código"})`, render: (row) => `<span class="cell-title">${escapeHtml(row.school_name)}</span><span class="cell-subtitle">${escapeHtml(row.school_id || "Sin código")}</span>` },
    { label: "Envíos", key: "submissions", number: true },
    { label: "Personas", key: "unique", number: true },
    { label: "Duplicados", key: "duplicates", number: true, render: (row) => row.duplicates ? statusTag(int(row.duplicates), "danger") : statusTag("0") },
    { label: "Sin ID", key: "missingId", number: true },
    { label: "Último envío", key: "last_submission_time", render: (row) => dateTime(row.last_submission_time) },
  ];
  if (hasTargets) {
    columns.splice(-1, 0,
      { label: hasQuality() ? "En línea de base" : "Meta", key: "target", number: true, render: (row) => row.target ? int(row.target) : '<span class="muted">Sin meta</span>' },
      ...(hasQuality() ? [{ label: "Vinculados", key: "linked", number: true }] : []),
      { label: "Avance", value: (row) => row.completion === null ? "" : row.completion, number: true, render: (row) => row.completion === null ? '<span class="muted">No aplica</span>' : statusTag(`${int(row.completion * 100)}%`) },
    );
  }
  const content = table(columns, prepared, "No hay escuelas que coincidan con los filtros.");
  app.innerHTML = `<section class="view"><section class="panel">${panelHeader("Cobertura por escuela", `${int(prepared.length)} escuelas visibles`, true)}${content}</section></section>`;
}

function renderGrades() {
  const groups = new Map();
  const roles = selectedRoles();
  for (const row of state.data.grades) {
    if (state.filters.province && row.province !== state.filters.province) continue;
    if (state.filters.school && row.school_id !== state.filters.school) continue;
    const query = state.filters.search.toLocaleLowerCase("es");
    if (query && !matches(`${row.grade} ${row.school_name} ${row.province}`, query)) continue;
    if (!groups.has(row.grade)) groups.set(row.grade, { grade: row.grade, schools: new Set(), students: 0, studentDuplicates: 0, teachers: 0, teacherSubmissions: 0 });
    const item = groups.get(row.grade);
    item.schools.add(row.school_id || row.school_name);
    if (roles.includes("students")) {
      item.students += row.roles.students?.unique_participants || 0;
      item.studentDuplicates += row.roles.students?.duplicate_submissions || 0;
    }
    if (roles.includes("teachers")) {
      item.teachers += row.roles.teachers?.unique_participants || 0;
      item.teacherSubmissions += row.roles.teachers?.submissions || 0;
    }
  }
  const rows = [...groups.values()]
    .map((row) => ({ ...row, schoolCount: row.schools.size }))
    .filter((row) => row.students + row.teachers + row.teacherSubmissions > 0)
    .sort((a, b) => a.grade.localeCompare(b.grade, "es", { numeric: true }));
  state.exportName = "conteos_por_grado";
  const content = table([
    { label: "Grado", key: "grade", render: (row) => `<strong>${escapeHtml(row.grade)}</strong>` },
    { label: "Escuelas", key: "schoolCount", number: true },
    { label: "Estudiantes únicos", key: "students", number: true },
    { label: "Duplicados estudiantes", key: "studentDuplicates", number: true, render: (row) => row.studentDuplicates ? statusTag(int(row.studentDuplicates), "danger") : statusTag("0") },
    { label: "Docentes únicos", key: "teachers", number: true },
    { label: "Envíos docentes", key: "teacherSubmissions", number: true },
  ], rows, "No hay grados que coincidan con los filtros.");
  app.innerHTML = `<section class="view"><section class="panel">${panelHeader("Cobertura por grado", "Docentes con más de un grado aparecen una vez en cada grado declarado", true)}${content}</section></section>`;
}

function followUpCard(schoolRows) {
  const visited = schoolRows.filter((row) => aggregateMetrics([row]).submissions > 0);
  const inVisited = aggregateMetrics(visited);
  const overall = aggregateMetrics(schoolRows);
  if (!inVisited.target) return metricCard("Seguimiento de línea de base", "Sin dato", "Todavía no hay escuelas visitadas con participantes de línea de base", true);
  return metricCard(
    "Seguimiento en escuelas visitadas",
    `${int((inVisited.linked / inVisited.target) * 100)}%`,
    `${int(inVisited.linked)} de ${int(inVisited.target)} en ${int(visited.length)} escuelas. Operativo completo: ${int(overall.target ? (overall.linked / overall.target) * 100 : 0)}% de ${int(overall.target)}`,
    true,
  );
}

function qualityTag(count, tone = "warning") {
  return count ? statusTag(int(count), tone) : statusTag("0");
}

function renderQuality() {
  if (!hasQuality()) {
    app.innerHTML = emptyState("Sin controles de calidad", "Esta ronda no tiene controles de calidad configurados.");
    return;
  }
  const labels = state.data.wave.quality_labels;
  const thresholds = state.data.wave.thresholds || {};
  const schools = filteredSchools().map((row) => ({ ...row, ...aggregateMetrics([row]) })).filter((row) => row.submissions > 0);
  const total = aggregateMetrics(filteredSchools());
  const q = total.quality;
  const pct = (count) => (total.submissions ? `${int((count / total.submissions) * 100)}% de los envíos` : "Sin envíos");
  const studentLimits = thresholds.students || {};
  const qualityColumns = [
    { label: "Registros nuevos", value: (row) => row.quality.new_registration || 0, number: true, render: (row) => qualityTag(row.quality.new_registration) },
    { label: "Duplicados", key: "duplicates", number: true, render: (row) => qualityTag(row.duplicates, "danger") },
    { label: "No completas", value: (row) => row.quality.incomplete || 0, number: true, render: (row) => qualityTag(row.quality.incomplete) },
    { label: "No individual", value: (row) => row.quality.admin_deviation || 0, number: true, render: (row) => qualityTag(row.quality.admin_deviation) },
    { label: "Conocía grupo", value: (row) => row.quality.knew_arm || 0, number: true, render: (row) => qualityTag(row.quality.knew_arm) },
    { label: "Muy corta", value: (row) => row.quality.short_duration || 0, number: true, render: (row) => qualityTag(row.quality.short_duration, "danger") },
    { label: "Muy larga", value: (row) => row.quality.very_long_duration || 0, number: true, render: (row) => qualityTag(row.quality.very_long_duration) },
  ];
  const schoolTable = table([
    { label: "Provincia", key: "province" },
    { label: "Escuela", value: (row) => `${row.school_name} (${row.school_id || "sin código"})`, render: (row) => `<span class="cell-title">${escapeHtml(row.school_name)}</span><span class="cell-subtitle">${escapeHtml(row.school_id || "Sin código")}</span>` },
    { label: "Envíos", key: "submissions", number: true },
    { label: "En línea de base", key: "target", number: true },
    { label: "Vinculados", key: "linked", number: true },
    { label: "Seguimiento", value: (row) => (row.target ? row.linked / row.target : ""), number: true, render: (row) => row.target ? statusTag(`${int((row.linked / row.target) * 100)}%`, row.linked / row.target < 0.8 ? "warning" : "") : '<span class="muted">No aplica</span>' },
    ...qualityColumns,
  ], schools, "No hay escuelas con envíos para los filtros actuales.");
  const schoolExport = state.exportRows;

  const query = state.filters.search.toLocaleLowerCase("es");
  const enumerators = state.data.enumerators.filter((row) => {
    const roleMatch = !state.filters.role || (row[state.filters.role] || 0) > 0;
    const provinceMatch = !state.filters.province || (row.provinces || []).includes(state.filters.province);
    const schoolMatch = !state.filters.school || (row.school_ids || []).includes(state.filters.school);
    return roleMatch && provinceMatch && schoolMatch && row.students > 0 && (!query || matches(row.enumerator_ref, query));
  });
  const enumeratorTable = table([
    { label: "Aplicador", key: "enumerator_ref", render: (row) => `<strong>${escapeHtml(row.enumerator_ref)}</strong>` },
    { label: "Estudiantes", key: "students", number: true },
    { label: "Escuelas", key: "schools", number: true },
    { label: "Mediana min.", key: "median_duration_minutes", number: true, render: (row) => decimal(row.median_duration_minutes) },
    { label: "Registros nuevos", value: (row) => row.quality.new_registration || 0, number: true, render: (row) => qualityTag(row.quality.new_registration) },
    { label: "Duplicados", key: "duplicates", number: true, render: (row) => qualityTag(row.duplicates, "danger") },
    { label: "No completas", value: (row) => row.quality.incomplete || 0, number: true, render: (row) => qualityTag(row.quality.incomplete) },
    { label: "No individual", value: (row) => row.quality.admin_deviation || 0, number: true, render: (row) => qualityTag(row.quality.admin_deviation) },
    { label: "Conocía grupo", value: (row) => row.quality.knew_arm || 0, number: true, render: (row) => qualityTag(row.quality.knew_arm) },
    { label: "Muy corta", value: (row) => row.quality.short_duration || 0, number: true, render: (row) => qualityTag(row.quality.short_duration, "danger") },
  ], enumerators, "No hay aplicadores de estudiantes para los filtros actuales.");
  state.exportRows = schoolExport;
  state.exportName = "calidad_por_escuela";

  app.innerHTML = `<section class="view">
    <div class="metric-grid">
      ${followUpCard(filteredSchools())}
      ${metricCard(labels.new_registration, int(q.new_registration || 0), `${pct(q.new_registration || 0)}. Revisar si son personas de línea de base no encontradas`)}
      ${metricCard(labels.knew_arm, int(q.knew_arm || 0), `${pct(q.knew_arm || 0)}. La aplicación debe ser ciega al grupo`)}
      ${metricCard(labels.short_duration, int(q.short_duration || 0), `Estudiantes con menos de ${int(studentLimits.short_minutes || 0)} minutos; adultos con menos de ${int((thresholds.teachers || {}).short_minutes || 0)}`)}
    </div>
    <section class="panel">${panelHeader("Seguimiento y alertas por escuela", "Vinculados = personas encuestadas en línea de base y encontradas en el cierre con el mismo ID", true)}${schoolTable}
      <p class="data-note">Alertas: registros nuevos (no encontrados en el marco), envíos duplicados del mismo ID, entrevistas no completas, aplicación grupal o en aula, aplicador que sabía si la escuela participa, y duraciones fuera de rango (más de ${int(studentLimits.very_long_minutes || 0)} minutos suele indicar un formulario abierto sin terminar).</p>
    </section>
    <section class="panel">${panelHeader("Alertas por aplicador (estudiantes)", "Docentes y directivos responden en forma autoadministrada")}${enumeratorTable}</section>
  </section>`;
}

function renderDuplicates() {
  const rows = state.data.duplicates.filter(recordMatches);
  state.exportName = "posibles_duplicados";
  const content = table([
    { label: "Caso", key: "case_ref", render: (row) => `<strong>${escapeHtml(row.case_ref)}</strong>` },
    { label: "Población", value: (row) => ROLE_LABELS[row.role] },
    { label: "Provincia", key: "province" },
    { label: "Escuela", value: (row) => `${row.school_name} (${row.school_id})`, render: (row) => `<span class="cell-title">${escapeHtml(row.school_name)}</span><span class="cell-subtitle">${escapeHtml(row.school_id || "Sin código")}</span>` },
    { label: "Grado", value: (row) => (row.grades || []).join(", ") || "No aplica" },
    { label: "Envíos", key: "submission_count", number: true },
    { label: "Extras", key: "extra_submissions", number: true, render: (row) => statusTag(int(row.extra_submissions), "danger") },
    { label: "Primero", value: (row) => row.timestamps[0] || "", render: (row) => dateTime(row.timestamps[0]) },
    { label: "Último", value: (row) => row.timestamps.at(-1) || "", render: (row) => dateTime(row.timestamps.at(-1)) },
  ], rows, "No se detectaron identificadores repetidos con los filtros actuales.");
  app.innerHTML = `<section class="view"><section class="panel">${panelHeader("Casos con más de un envío", `${int(rows.length)} grupos para revisar. Un duplicado es una señal, no una eliminación automática.`, true)}${content}<p class="data-note">Docentes y directivos se comparan dentro de la misma escuela. Estudiantes se comparan por identificador en todo el operativo.</p></section></section>`;
}

function renderEnumerators() {
  const query = state.filters.search.toLocaleLowerCase("es");
  const rows = state.data.enumerators.filter((row) => {
    const roleMatch = !state.filters.role || (row[state.filters.role] || 0) > 0;
    const provinceMatch = !state.filters.province || (row.provinces || []).includes(state.filters.province);
    const schoolMatch = !state.filters.school || (row.school_ids || []).includes(state.filters.school);
    return roleMatch && provinceMatch && schoolMatch && (!query || matches(row.enumerator_ref, query));
  });
  state.exportName = "carga_por_aplicador";
  const content = table([
    { label: "Aplicador", key: "enumerator_ref", render: (row) => `<strong>${escapeHtml(row.enumerator_ref)}</strong>` },
    { label: "Envíos", key: "submissions", number: true },
    { label: "Estudiantes", key: "students", number: true },
    { label: "Docentes", key: "teachers", number: true },
    { label: "Directivos", key: "directors", number: true },
    { label: "Escuelas", key: "schools", number: true },
    { label: "Duplicados", key: "duplicates", number: true, render: (row) => row.duplicates ? statusTag(int(row.duplicates), "warning") : statusTag("0") },
    { label: "Sin ID", key: "missing_id", number: true },
    { label: "Último envío", key: "last_submission_time", render: (row) => dateTime(row.last_submission_time) },
  ], rows, "No hay aplicadores que coincidan con los filtros.");
  app.innerHTML = `<section class="view"><section class="panel">${panelHeader("Carga y alertas por aplicador", "Los códigos están seudonimizados para la publicación web", true)}${content}</section></section>`;
}

function recordAlerts(row) {
  const alerts = [];
  if (row.missing_id) alerts.push(["Sin ID", "danger"]);
  if (row.duplicate) alerts.push(["Duplicado", "danger"]);
  if (row.short_duration) alerts.push(["Muy corta", "danger"]);
  if (row.new_registration) alerts.push(["Registro nuevo", "warning"]);
  if (row.incomplete) alerts.push(["No completa", "warning"]);
  if (row.admin_deviation) alerts.push(["No individual", "warning"]);
  if (row.knew_arm) alerts.push(["Conocía grupo", "warning"]);
  if (row.very_long_duration || row.duration_flag_long) alerts.push(["Duración alta", "warning"]);
  return alerts;
}

function renderSubmissions() {
  const rows = state.data.recent_records.filter(recordMatches);
  state.exportName = "envios_recientes";
  const content = table([
    { label: "Fecha", key: "timestamp", render: (row) => dateTime(row.timestamp) },
    { label: "Población", value: (row) => ROLE_LABELS[row.role] },
    { label: "Caso", key: "case_ref", render: (row) => `<strong>${escapeHtml(row.case_ref)}</strong>` },
    { label: "Escuela", value: (row) => `${row.school_name} (${row.school_id})`, render: (row) => `<span class="cell-title">${escapeHtml(row.school_name)}</span><span class="cell-subtitle">${escapeHtml(row.school_id || "Sin código")}</span>` },
    { label: "Grado", value: (row) => (row.grades || []).join(", ") || "No aplica" },
    { label: "Aplicador", key: "enumerator_ref" },
    { label: "Minutos", key: "duration_minutes", number: true, render: (row) => decimal(row.duration_minutes) },
    { label: "Control", value: (row) => recordAlerts(row).map((item) => item[0]).join(" | ") || "Sin alertas", render: (row) => {
      const alerts = recordAlerts(row);
      return alerts.length ? alerts.map(([label, tone]) => statusTag(label, tone)).join(" ") : statusTag("Sin alertas");
    } },
  ], rows, "No hay envíos recientes que coincidan con los filtros.");
  app.innerHTML = `<section class="view"><section class="panel">${panelHeader("Últimos 250 envíos", "Vista operativa sin DNI ni respuestas del cuestionario", true)}${content}</section></section>`;
}

function render() {
  if (!state.data) return;
  state.exportRows = [];
  document.querySelector("#view-title").textContent = VIEW_TITLES[state.view];
  document.querySelectorAll(".nav-item").forEach((button) => {
    const active = button.dataset.view === state.view;
    button.classList.toggle("is-active", active);
    if (active) button.setAttribute("aria-current", "page");
    else button.removeAttribute("aria-current");
  });
  ({
    overview: renderOverview,
    schools: renderSchools,
    grades: renderGrades,
    duplicates: renderDuplicates,
    enumerators: renderEnumerators,
    quality: renderQuality,
    submissions: renderSubmissions,
  })[state.view]();
  app.setAttribute("aria-busy", "false");
}

function populateFilters() {
  const provinces = [...new Set(state.data.schools.map((row) => row.province))].sort((a, b) => a.localeCompare(b, "es"));
  provinceFilter.insertAdjacentHTML("beforeend", provinces.map((province) => `<option value="${escapeHtml(province)}">${escapeHtml(province)}</option>`).join(""));
  updateSchoolOptions();
}

function updateSchoolOptions() {
  const current = state.filters.school;
  const rows = state.data.schools.filter((row) => !state.filters.province || row.province === state.filters.province);
  schoolFilter.innerHTML = '<option value="">Todas</option>' + rows.map((row) => `<option value="${escapeHtml(row.school_id)}">${escapeHtml(row.school_name)}</option>`).join("");
  if (rows.some((row) => row.school_id === current)) schoolFilter.value = current;
  else state.filters.school = "";
}

function csvValue(value) {
  const normalized = Array.isArray(value) ? value.join(" | ") : String(value ?? "");
  return `"${normalized.replaceAll('"', '""')}"`;
}

function downloadCsv() {
  if (!state.exportRows.length) return;
  const headers = Object.keys(state.exportRows[0]);
  const csv = "\ufeff" + [headers.map(csvValue).join(","), ...state.exportRows.map((row) => headers.map((header) => csvValue(row[header])).join(","))].join("\r\n");
  const link = document.createElement("a");
  link.href = URL.createObjectURL(new Blob([csv], { type: "text/csv;charset=utf-8" }));
  link.download = `${state.exportName}.csv`;
  link.click();
  URL.revokeObjectURL(link.href);
}

function installEvents() {
  document.querySelector("#primary-nav").addEventListener("click", (event) => {
    const button = event.target.closest("[data-view]");
    if (!button) return;
    state.view = button.dataset.view;
    history.replaceState(null, "", `#${state.view}`);
    render();
  });
  provinceFilter.addEventListener("change", () => {
    state.filters.province = provinceFilter.value;
    updateSchoolOptions();
    render();
  });
  schoolFilter.addEventListener("change", () => { state.filters.school = schoolFilter.value; render(); });
  roleFilter.addEventListener("change", () => { state.filters.role = roleFilter.value; render(); });
  searchFilter.addEventListener("input", () => { state.filters.search = searchFilter.value.trim(); render(); });
  document.querySelector("#clear-filters").addEventListener("click", () => {
    state.filters = { province: "", school: "", role: "", search: "" };
    provinceFilter.value = "";
    roleFilter.value = "";
    searchFilter.value = "";
    updateSchoolOptions();
    render();
  });
  app.addEventListener("click", (event) => {
    if (event.target.closest("[data-download]")) downloadCsv();
  });
}

function installTheme() {
  const stored = localStorage.getItem("hfc-theme");
  if (stored) document.documentElement.dataset.theme = stored;
  document.querySelector("#theme-toggle").addEventListener("click", () => {
    const next = document.documentElement.dataset.theme === "dark" ? "light" : "dark";
    document.documentElement.dataset.theme = next;
    localStorage.setItem("hfc-theme", next);
  });
}

async function loadDashboard() {
  try {
    const source = document.body.dataset.source || "data/dashboard.json";
    const response = await fetch(source, { cache: "no-store" });
    if (!response.ok) throw new Error(`No se pudo leer ${source} (HTTP ${response.status}).`);
    state.data = await response.json();
    if (state.data.wave?.label) document.querySelector(".context-label").textContent = state.data.wave.label;
    document.querySelectorAll("[data-requires-quality]").forEach((element) => { element.hidden = !hasQuality(); });
    state.view = VIEW_TITLES[location.hash.slice(1)] ? location.hash.slice(1) : "overview";
    document.querySelector("#nav-total").textContent = int(state.data.summary.submissions);
    document.querySelector("#sync-status").textContent = state.data.is_sample ? "Datos de muestra" : "Datos actualizados";
    document.querySelector("#sync-time").textContent = dateTime(state.data.generated_at);
    populateFilters();
    render();
  } catch (error) {
    document.querySelector(".sync-meta").classList.add("is-error");
    document.querySelector("#sync-status").textContent = "Error de carga";
    document.querySelector("#sync-time").textContent = "Revise la publicación";
    app.setAttribute("aria-busy", "false");
    app.innerHTML = `<div class="error-state"><div><h2>No se pudieron cargar los datos</h2><p>Genere el archivo de datos o revise la ruta de publicación.</p><code>${escapeHtml(error.message)}</code></div></div>`;
  }
}

installTheme();
installEvents();
loadDashboard();
