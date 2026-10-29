// Shared, dependency-free data rules for the static GitHub Pages application.
export const MY_ID = "26663279";
export const DEFAULT_ALIASES = Object.freeze({ [MY_ID]: "José Garrillo" });
export const SOURCE_URL = "https://docs.google.com/spreadsheets/d/1xOywnk0h-9JFxPDQKpZmB0jL-rQOKxdeBnGg3V4KBEA/edit?gid=579970152";
export const CSV_URL = "https://docs.google.com/spreadsheets/d/1xOywnk0h-9JFxPDQKpZmB0jL-rQOKxdeBnGg3V4KBEA/export?format=csv&gid=579970152";
const MAX_BYTES = 5 * 1024 * 1024;
const ID_PATTERN = /^[0-9A-Za-z]{5,20}$/;
const own = (object, key) => Object.hasOwn(object, key);
const record = (value) => value !== null && typeof value === "object" && !Array.isArray(value);

export function validateStudentId(value) {
  if (typeof value !== "string") throw new Error("El número de alumno debe ser texto.");
  const id = value.trim().toUpperCase();
  if (!ID_PATTERN.test(id)) throw new Error("Ingresa un número de alumno de 5 a 20 caracteres alfanuméricos.");
  return id;
}

export function validateAliases(value) {
  if (!record(value) || Object.keys(value).length > 2000) {
    throw new Error("Los nombres deben ser un objeto JSON con un máximo de 2000 alumnos.");
  }
  const clean = {};
  for (const [id, original] of Object.entries(value)) {
    if (!ID_PATTERN.test(id) || typeof original !== "string") throw new Error("Número de alumno o nombre inválido.");
    const name = original.trim();
    if (!name || [...name].length > 80 || /\p{C}/u.test(name)) {
      throw new Error("Cada nombre debe tener entre 1 y 80 caracteres, sin caracteres de control.");
    }
    clean[id] = name;
  }
  return clean;
}

export function displayName(id, aliases = {}) {
  return own(aliases, id) ? aliases[id] : (own(DEFAULT_ALIASES, id) ? DEFAULT_ALIASES[id] : id);
}

export function rankStudents(students, metricKey) {
  const rows = students.filter((student) => Number.isFinite(student.grades[metricKey]))
    .map((student) => ({ student, value: student.grades[metricKey] }))
    .sort((a, b) => b.value - a.value || (a.student.id < b.student.id ? -1 : a.student.id > b.student.id ? 1 : 0));
  let previous;
  let rank = 0;
  return rows.map((entry, index) => {
    if (index === 0 || entry.value !== previous) rank = index + 1;
    previous = entry.value;
    return { ...entry, rank };
  });
}

export function parseCSV(text) {
  if (typeof text !== "string" || text.length > MAX_BYTES) throw new Error("El CSV es demasiado grande o inválido.");
  text = text.replace(/^\uFEFF/, "");
  const rows = [];
  let row = [], field = "", quoted = false, closed = false;
  const endField = () => { row.push(field); field = ""; closed = false; };
  const endRow = () => {
    endField(); rows.push(row); row = [];
    if (rows.length > 10010) throw new Error("La planilla supera el límite de alumnos.");
  };
  for (let index = 0; index < text.length; index += 1) {
    const char = text[index];
    if (quoted) {
      if (char === '"') {
        if (text[index + 1] === '"') { field += '"'; index += 1; }
        else { quoted = false; closed = true; }
      } else field += char;
      continue;
    }
    if (char === ",") { endField(); continue; }
    if (char === "\n" || char === "\r") {
      endRow();
      if (char === "\r" && text[index + 1] === "\n") index += 1;
      continue;
    }
    if (closed) throw new Error("El CSV contiene texto después de una comilla de cierre.");
    if (char === '"') {
      if (field) throw new Error("El CSV contiene comillas inesperadas.");
      quoted = true;
    } else field += char;
  }
  if (quoted) throw new Error("El CSV contiene comillas sin cerrar.");
  if (field || row.length || closed) endRow();
  return rows;
}

function normalize(value) {
  return value.normalize("NFKD").replace(/\p{M}/gu, "").toLowerCase().trim().replace(/\s+/g, " ");
}

function number(value, row, column) {
  value = value.trim();
  if (!value) return null;
  // Google CSV uses ordinary decimal values, never locale thousands separators.
  if (!/^\+?\d+(?:[.,]\d{1,12})?$/.test(value)) {
    throw new Error(`Valor no numérico en fila ${row}, columna ${column}.`);
  }
  const parsed = Number(value.replace(",", "."));
  if (!Number.isFinite(parsed) || parsed < 0 || parsed > 100) throw new Error(`Valor fuera de rango en fila ${row}, columna ${column}.`);
  return parsed;
}

function decimalMean(rawValues) {
  // Sum decimal points as integers so equivalent sums always share a rank.
  const parts = rawValues.map((v) => v.trim().replace(",", ".").replace(/^\+/, "").split("."));
  const places = Math.max(...parts.map((p) => (p[1] || "").length));
  const total = parts.reduce((sum, [integer, fraction = ""]) => sum + BigInt(integer + fraction.padEnd(places, "0")), 0n);
  return Number(total) / Number((10n ** BigInt(places)) * BigInt(parts.length));
}

export function parseSheet(text) {
  if (typeof text !== "string" || text.replace(/^\uFEFF/, "").trimStart().startsWith("<")) {
    throw new Error("Google no devolvió un CSV público. Revisa el acceso a la planilla.");
  }
  const rows = parseCSV(text);
  const headerIndex = rows.slice(0, 10).findIndex((row) => row.length && normalize(row[0]) === "n° alumno");
  if (headerIndex < 0) throw new Error("No se encontró el encabezado N° Alumno. La estructura puede haber cambiado.");
  const headers = rows[headerIndex].map(normalize);
  const named = headers.filter(Boolean);
  if (new Set(named).size !== named.length) throw new Error("Hay encabezados repetidos en la planilla.");
  const definitions = [
    ["final", "Nota final", "Totales", "nota final"],
    ["banner", "Nota Banner", "Totales", "nota banner"],
    ["presentation", "Presentación", "Totales", "nota presentacion"],
    ["catedra", "Cátedra", "Categorías", "nota catedra"],
    ["written", "Evaluaciones escritas", "Categorías", "nota e. escritas"],
    ["i1", "Interrogación 1", "Evaluaciones escritas", "i1"],
    ["i2", "Interrogación 2", "Evaluaciones escritas", "i2"],
    ["exam", "Examen", "Evaluaciones escritas", "examen"],
    ["bonus", "Bonificación ECA", "Otros", "bonificacion eca"],
  ];
  const activities = headers.filter((h) => /^ac\d+$/.test(h)).sort((a, b) => Number(a.slice(2)) - Number(b.slice(2)));
  if (!activities.length) throw new Error("La planilla no contiene columnas de actividades.");
  definitions.push(...activities.map((key) => [key, key.toUpperCase(), "Actividades", key]));
  if (definitions.length > 199) throw new Error("La planilla tiene demasiadas métricas.");
  const missing = definitions.filter((d) => !headers.includes(d[3]));
  if (missing.length) throw new Error("Faltan columnas esperadas: " + missing.map((d) => d[3]).join(", "));
  const indices = Object.fromEntries(definitions.map(([key, , , header]) => [key, headers.indexOf(header)]));
  const students = [], rawById = new Map(), seen = new Set();
  rows.slice(headerIndex + 1).forEach((row, offset) => {
    if (!row.some((cell) => cell.trim())) return;
    const line = headerIndex + offset + 2;
    if (row.length !== headers.length) throw new Error(`La fila ${line} tiene una cantidad inesperada de columnas.`);
    const id = row[0].trim();
    if (!ID_PATTERN.test(id)) throw new Error(`Identificador inválido en fila ${line}.`);
    if (seen.has(id)) throw new Error(`Identificador duplicado en fila ${line}.`);
    seen.add(id);
    rawById.set(id, row);
    students.push({ id, grades: Object.fromEntries(Object.entries(indices).map(([key, index]) => [key, number(row[index], line, headers[index])])), changes: {} });
  });
  if (!students.length) throw new Error("La planilla no contiene alumnos.");
  const active = activities.filter((key) => students.some((s) => s.grades[key] > 0));
  for (const student of students) {
    student.grades.activities = active.length && active.every((key) => student.grades[key] !== null)
      ? decimalMean(active.map((key) => rawById.get(student.id)[indices[key]])) : null;
  }
  const metrics = definitions.map(([key, label, group]) => ({
    key, label, group, unit: activities.includes(key) || key === "bonus" ? "points" : "grade",
    available: students.some((s) => s.grades[key] > 0),
    description: activities.includes(key) ? "Puntos publicados en la planilla; no convertidos a nota." : "Valor publicado por la planilla; no recalculado por esta aplicación.",
  }));
  metrics.splice(5, 0, {
    key: "activities", label: "Promedio actividades", group: "Categorías", unit: "points", available: active.length > 0,
    description: "Media simple de puntos de " + (active.map((k) => k.toUpperCase()).join(", ") || "ninguna actividad") + ". Incluye ceros individuales; excluye columnas sin valores positivos. No es la nota oficial de Cátedra.",
  });
  const warnings = [
    "Los totales y categorías son los publicados por la planilla y pueden ser provisionales. No se infieren ponderaciones ni aprobación.",
    "Sin resultados detectados significa que no hay valores positivos: puede ser una evaluación pendiente o con todos sus puntajes en cero. Los ceros individuales sí cuentan en columnas disponibles.",
    "El promedio de actividades usa puntos, no notas. Los vacíos no participan del ranking; un promedio con algún dato vacío no se calcula.",
  ];
  const inactive = metrics.filter((m) => !m.available).map((m) => m.label);
  if (inactive.length) warnings.push("Sin resultados positivos detectados: " + inactive.join(", ") + ".");
  return { source_url: SOURCE_URL, students, metrics, warnings };
}

export function validateSnapshot(value) {
  if (!record(value) || value.source_url !== SOURCE_URL) return false;
  for (const key of ["fetched_at", "changed_at", "previous_at"]) {
    if (key === "previous_at" && value[key] === null) continue;
    if (typeof value[key] !== "string" || !Number.isFinite(Date.parse(value[key]))) return false;
  }
  if (typeof value.stale !== "boolean" || (value.error !== null && typeof value.error !== "string")) return false;
  if (!Array.isArray(value.warnings) || value.warnings.length > 30 || !value.warnings.every((w) => typeof w === "string" && w.length <= 5000)) return false;
  if (!Array.isArray(value.metrics) || !value.metrics.length || value.metrics.length > 200) return false;
  const keys = new Set();
  for (const metric of value.metrics) {
    if (!record(metric) || typeof metric.key !== "string" || !/^[a-z][a-z0-9_]{0,30}$/.test(metric.key) || keys.has(metric.key)) return false;
    if (!["label", "group", "description"].every((k) => typeof metric[k] === "string" && metric[k].length <= 3000)) return false;
    if (!["grade", "points"].includes(metric.unit) || typeof metric.available !== "boolean") return false;
    keys.add(metric.key);
  }
  if (!Array.isArray(value.students) || !value.students.length || value.students.length > 10000) return false;
  const ids = new Set();
  for (const student of value.students) {
    if (!record(student) || typeof student.id !== "string" || !ID_PATTERN.test(student.id) || ids.has(student.id)) return false;
    ids.add(student.id);
    if (!record(student.grades) || Object.keys(student.grades).length !== keys.size || !record(student.changes)) return false;
    for (const [key, grade] of Object.entries(student.grades)) {
      if (!keys.has(key) || (grade !== null && (!Number.isFinite(grade) || grade < 0 || grade > 100))) return false;
    }
    for (const [key, delta] of Object.entries(student.changes)) {
      if (!keys.has(key) || !Number.isFinite(delta) || delta < -100 || delta > 100) return false;
    }
  }
  return true;
}

export function compareSnapshots(fresh, old) {
  if (!validateSnapshot(fresh)) throw new Error("Los datos nuevos no tienen el formato esperado.");
  if (!validateSnapshot(old)) old = null;
  const previous = new Map((old?.students || []).map((s) => [s.id, s]));
  let changed = !old || old.students.length !== fresh.students.length || JSON.stringify(fresh.metrics) !== JSON.stringify(old.metrics) || JSON.stringify(fresh.warnings) !== JSON.stringify(old.warnings);
  const students = fresh.students.map((student) => {
    const prior = previous.get(student.id);
    if (!prior) changed = true;
    const changes = {};
    if (prior) for (const [key, grade] of Object.entries(student.grades)) {
      if (grade !== prior.grades[key]) {
        changed = true;
        if (grade !== null && Number.isFinite(prior.grades[key])) changes[key] = Number((grade - prior.grades[key]).toFixed(8));
      }
    }
    return { ...student, grades: { ...student.grades }, changes };
  });
  if (!changed) for (const student of students) student.changes = { ...previous.get(student.id).changes };
  return {
    ...fresh, students,
    changed_at: changed ? fresh.fetched_at : old.changed_at,
    previous_at: changed && old ? old.fetched_at : (old?.previous_at ?? null),
    stale: false, error: null,
  };
}

export async function fetchGrades(signal) {
  const response = await fetch(CSV_URL, { signal, credentials: "omit", cache: "no-store", referrerPolicy: "no-referrer" });
  if (!response.ok) throw new Error(`Google Sheets respondió con error ${response.status}.`);
  const text = await response.text();
  if (new TextEncoder().encode(text).byteLength > MAX_BYTES) throw new Error("La respuesta supera el límite de 5 MB.");
  const payload = parseSheet(text);
  const timestamp = new Date().toISOString();
  return { ...payload, fetched_at: timestamp, changed_at: timestamp, previous_at: null, stale: false, error: null };
}
