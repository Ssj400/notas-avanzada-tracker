import {
  MY_ID, DEFAULT_ALIASES, SOURCE_URL,
  validateStudentId, validateAliases, displayName, rankStudents,
  fetchGrades, validateSnapshot, compareSnapshots,
} from './core.js';

const PREFIX = 'notas-avanzada-tracker:v1:';
const REFRESH_INTERVAL = 5 * 60 * 1000;
const REQUEST_TIMEOUT = 35 * 1000;
const MAX_IMPORT_SIZE = 1024 * 1024;
const CARD_METRICS = [
  ['final', 'Final · provisional'],
  ['catedra', 'Cátedra · provisional'],
  ['activities', 'Actividades · puntos'],
  ['i1', 'Interrogación 1'],
];
const $ = (id) => document.getElementById(id);
const ui = Object.fromEntries([
  'profile-name', 'profile-id', 'change-profile', 'my-position', 'import-aliases',
  'export-aliases', 'aliases-file', 'source-link', 'connection-status', 'refresh',
  'auto-refresh', 'fetched-at', 'changed-at', 'network-message', 'storage-message',
  'action-message', 'source-warnings', 'warnings-summary', 'warnings-list',
  'personal-caption', 'metric-cards', 'profile-message', 'ranking-count',
  'ranking-section', 'metric-select', 'search', 'limit-select', 'metric-description',
  'value-heading', 'ranking-body', 'table-caption', 'delta-description',
  'profile-dialog', 'profile-form', 'student-id', 'profile-error', 'cancel-profile',
  'alias-dialog', 'alias-form', 'alias-name', 'alias-student-id', 'alias-error', 'cancel-alias',
].map((id) => [id, $(id)]));

const state = {
  aliases: {}, profile: null, rememberedId: MY_ID, snapshot: null,
  metricKey: 'final', loading: false, auto: true, autoTimer: null,
  networkError: '', storageIssues: new Map(), editingId: null,
  profileOpener: null, aliasOpener: null, importing: false, destroyed: false,
  controller: null,
};

function element(tag, text, className) {
  const node = document.createElement(tag);
  if (text !== undefined) node.textContent = text;
  if (className) node.className = className;
  return node;
}

function message(node, text) {
  node.textContent = text;
  node.hidden = !text;
}

function describeError(error) {
  return error instanceof Error && error.message ? error.message : 'Error desconocido.';
}

function announce(text) {
  message(ui['action-message'], text);
}

function storageIssue(key, detail) {
  state.storageIssues.set(key, detail);
  message(ui['storage-message'], [...state.storageIssues.values()].join(' '));
}

function readStored(key) {
  try {
    const raw = localStorage.getItem(PREFIX + key);
    return raw === null ? null : JSON.parse(raw);
  } catch {
    storageIssue(`read:${key}`, `No se pudo leer «${key}» del almacenamiento local; se continúa en memoria.`);
    return null;
  }
}

function writeStored(key, value) {
  try {
    localStorage.setItem(PREFIX + key, JSON.stringify(value));
    state.storageIssues.delete(`write:${key}`);
    message(ui['storage-message'], [...state.storageIssues.values()].join(' '));
    return true;
  } catch {
    storageIssue(`write:${key}`, `No se pudo guardar «${key}». El cambio se mantiene en esta pestaña, pero puede perderse al cerrarla.`);
    return false;
  }
}

function restoreLocalData() {
  // Read before writing: never replace existing aliases just by opening the app.
  const aliases = readStored('aliases');
  if (aliases !== null) {
    try { state.aliases = validateAliases(aliases); }
    catch { storageIssue('invalid:aliases', 'Los alias guardados no son válidos. No se cargaron ni se sobrescribieron automáticamente.'); }
  }
  const profile = readStored('profile');
  if (profile !== null) {
    try { state.rememberedId = validateStudentId(profile); }
    catch { storageIssue('invalid:profile', 'El último ID guardado no es válido; puedes elegir otro perfil.'); }
  }
  const auto = readStored('auto');
  if (typeof auto === 'boolean') state.auto = auto;
  else if (auto !== null) storageIssue('invalid:auto', 'La preferencia de actualización no era válida; se usó el valor predeterminado.');
  ui['auto-refresh'].checked = state.auto;
  const snapshot = readStored('snapshot');
  if (snapshot !== null) {
    try {
      if (!validateSnapshot(snapshot)) throw new Error('Copia inválida');
      state.snapshot = { ...snapshot, stale: true };
    } catch {
      storageIssue('invalid:snapshot', 'La copia local de notas no es válida y no se mostrará. Se consultará la planilla de nuevo.');
    }
  }
}

function formatNumber(value, signed = false) {
  if (typeof value !== 'number' || !Number.isFinite(value)) return '—';
  const formatted = new Intl.NumberFormat('es-CL', {
    minimumFractionDigits: 2, maximumFractionDigits: 2,
    signDisplay: signed ? 'exceptZero' : 'auto',
  }).format(value);
  return formatted;
}

function formatDate(value) {
  if (!value) return 'sin información';
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? 'sin información' : new Intl.DateTimeFormat('es-CL', {
    dateStyle: 'medium', timeStyle: 'short',
  }).format(date);
}

function nameFor(id) {
  // The core ties the default name to its student ID, never to the current profile.
  return displayName(id, state.aliases);
}

function selectedMetric() {
  return state.snapshot?.metrics.find((metric) => metric.key === state.metricKey) ?? null;
}

function rankedFor(metric) {
  return metric?.available && state.snapshot ? rankStudents(state.snapshot.students, metric.key) : [];
}

function renderSync() {
  const snapshot = state.snapshot;
  ui.refresh.disabled = state.loading;
  ui.refresh.textContent = state.loading ? 'Consultando…' : '↻ Actualizar';
  ui['ranking-section'].setAttribute('aria-busy', String(state.loading));
  const badge = ui['connection-status'];
  badge.classList.remove('is-current', 'is-stale');
  if (snapshot?.stale) {
    badge.textContent = state.loading ? 'DESACTUALIZADA · consultando' : 'DESACTUALIZADA · copia local';
    badge.classList.add('is-stale');
  } else if (state.loading) {
    badge.textContent = snapshot ? 'Actualizando' : 'Consultando Google';
  } else if (snapshot) {
    badge.textContent = 'Consulta completada';
    badge.classList.add('is-current');
  } else {
    badge.textContent = state.networkError ? 'Sin conexión · sin datos' : 'Sin datos';
    if (state.networkError) badge.classList.add('is-stale');
  }
  ui['fetched-at'].textContent = snapshot ? `Última consulta exitosa: ${formatDate(snapshot.fetched_at)}.` : 'Última consulta: todavía no realizada con éxito.';
  ui['changed-at'].textContent = snapshot ? `Último cambio detectado: ${formatDate(snapshot.changed_at)}.` : 'Último cambio detectado: sin información.';
  message(ui['network-message'], state.networkError);
}

function renderMetrics() {
  const metrics = state.snapshot?.metrics ?? [];
  const previous = state.metricKey;
  ui['metric-select'].replaceChildren();
  if (!metrics.length) {
    ui['metric-select'].append(element('option', 'Sin categorías disponibles'));
    ui['metric-select'].disabled = true;
    ui.search.disabled = true;
    return;
  }
  const groups = new Map();
  for (const metric of metrics) {
    if (!groups.has(metric.group)) {
      const group = element('optgroup');
      group.label = metric.group;
      groups.set(metric.group, group);
      ui['metric-select'].append(group);
    }
    const unit = metric.unit === 'points' ? 'puntos' : 'nota';
    const provisional = ['final', 'banner', 'presentation', 'catedra', 'written'].includes(metric.key) ? ' · provisional' : '';
    const option = element('option', `${metric.label} · ${unit}${provisional}${metric.available ? '' : ' · sin resultados'}`);
    option.value = metric.key;
    groups.get(metric.group).append(option);
  }
  state.metricKey = metrics.some((metric) => metric.key === previous) ? previous : (metrics.find((metric) => metric.key === 'final')?.key ?? metrics[0].key);
  ui['metric-select'].value = state.metricKey;
  ui['metric-select'].disabled = false;
  ui.search.disabled = false;
}

function renderWarnings() {
  const warnings = state.snapshot?.warnings ?? [];
  ui['source-warnings'].hidden = !warnings.length;
  ui['warnings-summary'].textContent = `Observaciones de la planilla (${warnings.length})`;
  ui['warnings-list'].replaceChildren(...warnings.map((warning) => element('li', warning)));
}

function renderProfile() {
  ui['profile-name'].textContent = state.profile ? nameFor(state.profile) : 'Vista general';
  ui['profile-id'].textContent = state.profile ? `ID ${state.profile}` : 'Sin perfil seleccionado';
  ui['change-profile'].textContent = state.profile ? 'Cambiar usuario' : 'Elegir usuario';
  ui['my-position'].disabled = !state.profile || !state.snapshot;
  ui['personal-caption'].textContent = state.profile ? `${nameFor(state.profile)} · ${state.profile}` : 'Elige un perfil para ver tus posiciones.';
  const present = state.snapshot?.students.some((student) => student.id === state.profile);
  ui['profile-message'].textContent = !state.profile
    ? 'Seleccionar un perfil no requiere contraseña ni verifica identidad.'
    : !state.snapshot
      ? 'Tu perfil está listo. Las posiciones aparecerán cuando tengamos datos.'
      : !present
        ? `Sin dato: el ID ${state.profile} no aparece en esta copia de la planilla. Puedes usar el ranking general o cambiar de usuario.`
        : 'Tus posiciones usan el curso completo, independientemente de los filtros de la tabla.';
  const cards = CARD_METRICS.map(([key, title]) => {
    const card = element('article', undefined, 'metric-card');
    const heading = element('h3', title, 'card-title');
    const metric = state.snapshot?.metrics.find((item) => item.key === key);
    const ranking = rankedFor(metric);
    const own = state.profile ? ranking.find((entry) => entry.student.id === state.profile) : null;
    const value = element('p', own ? `#${own.rank}` : '—', 'card-value');
    const detail = element('p', undefined, 'card-detail');
    if (own) {
      card.classList.add('has-value');
      detail.append(element('strong', `${formatNumber(own.value)} ${metric.unit === 'points' ? 'puntos' : 'nota'}`));
      detail.append(document.createTextNode(` · entre ${ranking.length} estudiantes`));
    } else {
      detail.textContent = !state.profile ? 'Elige tu perfil' : !state.snapshot ? 'Esperando datos' : !metric?.available ? 'Sin resultados disponibles' : 'Sin dato para tu ID';
    }
    card.append(heading, value, detail);
    return card;
  });
  ui['metric-cards'].replaceChildren(...cards);
}

function normalizedSearch(value) {
  return value.normalize('NFD').replace(/[\u0300-\u036f]/g, '').toLocaleLowerCase('es');
}

function appendNumber(row, value, className) {
  row.append(element('td', formatNumber(value), className));
}

function renderRanking() {
  const focusedEditId = document.activeElement?.dataset?.editId;
  const focusedRowId = document.activeElement?.dataset?.studentId;
  const metric = selectedMetric();
  const ranking = rankedFor(metric);
  const query = normalizedSearch(ui.search.value.trim());
  const limit = ui['limit-select'].value === 'all' ? Infinity : Number(ui['limit-select'].value);
  // Apply the cutoff to original competition ranks, not array positions or filtered indices.
  const visible = ranking.filter((entry) => entry.rank <= limit && (!query || normalizedSearch(`${entry.student.id} ${nameFor(entry.student.id)}`).includes(query)));
  const unit = metric?.unit === 'points' ? 'puntos' : 'nota';
  ui['value-heading'].textContent = `Valor · ${unit}`;
  ui['metric-description'].textContent = metric
    ? `${metric.description}${metric.available ? '' : ' Sin resultados disponibles: esta categoría no participa aún del ranking (por ejemplo, una columna completa en cero).'}`
    : state.loading ? 'Consultando la planilla pública…' : 'No hay datos disponibles. Puedes volver a consultar con Actualizar.';
  ui['ranking-count'].textContent = metric ? `${visible.length} de ${ranking.length} con dato` : 'Sin datos';
  ui['table-caption'].textContent = metric ? `Ranking de ${metric.label}, en ${unit}. Orden descendente. Posiciones originales del curso completo. Totales provisionales.` : 'Ranking del curso, sin datos disponibles.';
  const previousAt = state.snapshot?.previous_at;
  ui['delta-description'].textContent = `Δ en ${unit}: diferencia respecto de la consulta anterior al último cambio detectado${previousAt ? ` (${formatDate(previousAt)})` : ''}. Las consultas sin cambios conservan esos deltas. Sin comparación previa o valor comparable se muestra —.`;
  const fragment = document.createDocumentFragment();
  if (!visible.length) {
    const row = element('tr');
    const text = !state.snapshot
      ? state.loading ? 'Cargando la planilla pública…' : 'No hay una copia disponible. Reintenta con Actualizar.'
      : !metric?.available ? 'Sin resultados disponibles en esta categoría.'
        : !ranking.length ? 'Ningún estudiante tiene un valor publicado en esta categoría.'
          : 'No hay coincidencias con estos filtros. Prueba «Todos» o limpia la búsqueda.';
    const cell = element('td', text, 'empty-state');
    cell.colSpan = 9;
    row.append(cell);
    fragment.append(row);
  }
  for (const entry of visible) {
    const { student, rank, value } = entry;
    const row = element('tr');
    row.dataset.studentId = student.id;
    row.tabIndex = -1;
    if (student.id === state.profile) row.classList.add('is-own');
    const rankCell = element('td', `#${rank}`, `rank-cell${rank <= 3 ? ' rank-leading' : ''}`);
    const nameCell = element('td');
    const studentName = element('span', nameFor(student.id), 'student-name');
    studentName.title = nameFor(student.id);
    nameCell.append(studentName);
    if (student.id === state.profile) nameCell.append(element('span', 'Tú', 'own-label'));
    row.append(rankCell, nameCell, element('td', student.id, 'mono'));
    appendNumber(row, value, 'value-cell');
    appendNumber(row, student.grades.final);
    appendNumber(row, student.grades.catedra);
    appendNumber(row, student.grades.activities);
    const delta = student.changes?.[metric.key];
    const deltaClass = typeof delta === 'number' && delta !== 0 ? delta > 0 ? 'delta-positive' : 'delta-negative' : 'muted';
    row.append(element('td', formatNumber(delta, true), deltaClass));
    const actionCell = element('td');
    const edit = element('button', 'Editar', 'edit-name');
    edit.type = 'button';
    edit.dataset.editId = student.id;
    edit.setAttribute('aria-label', `Editar nombre local de ${nameFor(student.id)}, ID ${student.id}`);
    actionCell.append(edit);
    row.append(actionCell);
    fragment.append(row);
  }
  ui['ranking-body'].replaceChildren(fragment);
  // Background refreshes must not strand keyboard focus on a removed row/button.
  if (focusedEditId) {
    const replacement = [...ui['ranking-body'].querySelectorAll('button[data-edit-id]')].find((button) => button.dataset.editId === focusedEditId);
    if (replacement) replacement.focus({ preventScroll: true });
    else ui.search.focus({ preventScroll: true });
  } else if (focusedRowId) {
    const replacement = [...ui['ranking-body'].rows].find((row) => row.dataset.studentId === focusedRowId);
    if (replacement) replacement.focus({ preventScroll: true });
    else ui.search.focus({ preventScroll: true });
  }
}

function renderAll() {
  renderSync();
  renderMetrics();
  renderWarnings();
  renderProfile();
  renderRanking();
}

async function refresh() {
  if (state.loading || state.destroyed) return;
  state.loading = true;
  state.networkError = '';
  renderSync();
  if (!state.snapshot) renderRanking();
  const controller = new AbortController();
  state.controller = controller;
  let timedOut = false;
  const timeout = window.setTimeout(() => {
    timedOut = true;
    controller.abort();
  }, REQUEST_TIMEOUT);
  try {
    const fresh = await fetchGrades(controller.signal);
    if (!validateSnapshot(fresh)) throw new Error('La respuesta de la planilla no tiene un formato válido.');
    const compared = compareSnapshots(fresh, state.snapshot);
    if (!validateSnapshot(compared)) throw new Error('No se pudo validar la comparación con la copia anterior.');
    state.snapshot = { ...compared, stale: false, error: null };
    writeStored('snapshot', state.snapshot);
  } catch (error) {
    if (!state.destroyed) {
      const detail = timedOut ? 'La consulta superó los 35 segundos.' : describeError(error);
      state.networkError = `No se pudo actualizar. ${detail} ${state.snapshot ? 'Se conserva la última copia válida, marcada DESACTUALIZADA.' : 'No hay una copia válida para mostrar.'} Puedes reintentar con Actualizar.`;
      if (state.snapshot) state.snapshot = { ...state.snapshot, stale: true };
    }
  } finally {
    window.clearTimeout(timeout);
    state.controller = null;
    state.loading = false;
    if (!state.destroyed) {
      renderAll();
      scheduleAuto();
    }
  }
}

function scheduleAuto() {
  window.clearTimeout(state.autoTimer);
  state.autoTimer = null;
  if (!state.auto || state.destroyed) return;
  state.autoTimer = window.setTimeout(() => {
    void refresh();
  }, REFRESH_INTERVAL);
}

function clearFieldError(input, errorNode) {
  input.removeAttribute('aria-invalid');
  message(errorNode, '');
}

function fieldError(input, errorNode, text) {
  input.setAttribute('aria-invalid', 'true');
  message(errorNode, text);
  input.focus();
}

function openProfile(opener = null) {
  state.profileOpener = opener;
  ui['student-id'].value = state.profile ?? state.rememberedId;
  clearFieldError(ui['student-id'], ui['profile-error']);
  ui['cancel-profile'].textContent = state.profile ? 'Cancelar' : 'Ver sin perfil';
  ui['profile-dialog'].showModal();
  ui['student-id'].focus();
  ui['student-id'].select();
}

function closeProfile() {
  ui['profile-dialog'].close();
}

function openAlias(id, opener) {
  state.editingId = id;
  state.aliasOpener = opener;
  ui['alias-student-id'].textContent = `ID ${id}`;
  ui['alias-name'].value = Object.hasOwn(state.aliases, id) ? state.aliases[id]
    : Object.hasOwn(DEFAULT_ALIASES, id) ? DEFAULT_ALIASES[id] : '';
  clearFieldError(ui['alias-name'], ui['alias-error']);
  ui['alias-dialog'].showModal();
  ui['alias-name'].focus();
  ui['alias-name'].select();
}

function closeAlias() {
  ui['alias-dialog'].close();
}

function myPosition() {
  if (!state.profile) {
    openProfile(ui['my-position']);
    return;
  }
  ui.search.value = '';
  ui['limit-select'].value = 'all';
  renderRanking();
  const row = [...ui['ranking-body'].rows].find((item) => item.dataset.studentId === state.profile);
  if (row) {
    row.focus({ preventScroll: true });
    row.scrollIntoView({ block: 'center', inline: 'nearest', behavior: 'auto' });
    announce(`Tu posición en ${selectedMetric().label}: ${row.cells[0].textContent}. Se limpiaron los filtros.`);
  } else {
    announce(`Se limpiaron los filtros. Sin dato de ranking para tu ID en ${selectedMetric()?.label ?? 'esta categoría'}.`);
    $('ranking-heading').tabIndex = -1;
    $('ranking-heading').focus();
  }
}

async function importAliases(file) {
  if (!file || state.importing) return;
  state.importing = true;
  ui['import-aliases'].disabled = true;
  try {
    if (file.size > MAX_IMPORT_SIZE) throw new Error('El archivo supera el límite de 1 MB.');
    const text = await file.text();
    const imported = validateAliases(JSON.parse(text));
    const merged = validateAliases({ ...state.aliases, ...imported });
    // Validation finishes before any state mutation or storage write.
    state.aliases = merged;
    const saved = writeStored('aliases', merged);
    renderProfile();
    renderRanking();
    announce(`${Object.keys(imported).length} alias importados y combinados. ${saved ? 'Guardados en este navegador.' : 'Solo en memoria; revisa el aviso de almacenamiento.'} No se importan notas ni perfiles.`);
  } catch (error) {
    announce(`No se importaron nombres: ${describeError(error)} Se conservaron los alias anteriores. El JSON debe contener solo un objeto de IDs y nombres.`);
  } finally {
    state.importing = false;
    ui['import-aliases'].disabled = false;
    ui['aliases-file'].value = '';
  }
}

function exportAliases() {
  let url;
  try {
    const aliases = validateAliases(state.aliases);
    const blob = new Blob([`${JSON.stringify(aliases, null, 2)}\n`], { type: 'application/json' });
    url = URL.createObjectURL(blob);
    const link = element('a');
    link.href = url;
    link.download = 'notas-avanzada-aliases.json';
    document.body.append(link);
    link.click();
    link.remove();
    window.setTimeout(() => URL.revokeObjectURL(url), 1000);
    announce('Exportación preparada: solo los alias guardados explícitamente, sin notas ni perfil.');
  } catch (error) {
    if (url) URL.revokeObjectURL(url);
    announce(`No se pudo exportar: ${describeError(error)}`);
  }
}

function bindEvents() {
  ui.refresh.addEventListener('click', () => { void refresh(); });
  ui['auto-refresh'].addEventListener('change', () => {
    state.auto = ui['auto-refresh'].checked;
    writeStored('auto', state.auto);
    scheduleAuto();
  });
  ui['change-profile'].addEventListener('click', (event) => openProfile(event.currentTarget));
  ui['cancel-profile'].addEventListener('click', closeProfile);
  ui['profile-dialog'].addEventListener('cancel', (event) => {
    event.preventDefault();
    closeProfile();
  });
  ui['profile-dialog'].addEventListener('close', () => {
    if (state.profileOpener?.isConnected) state.profileOpener.focus();
    else ui['change-profile'].focus();
  });
  ui['student-id'].addEventListener('input', () => clearFieldError(ui['student-id'], ui['profile-error']));
  ui['profile-form'].addEventListener('submit', (event) => {
    event.preventDefault();
    try {
      const id = validateStudentId(ui['student-id'].value);
      state.profile = id;
      state.rememberedId = id;
      const saved = writeStored('profile', id);
      renderProfile();
      renderRanking();
      closeProfile();
      announce(`Perfil local seleccionado: ${nameFor(id)} (${id}). No verifica identidad.${saved ? '' : ' El ID se mantiene solo en memoria.'}`);
    } catch (error) { fieldError(ui['student-id'], ui['profile-error'], describeError(error)); }
  });
  ui['my-position'].addEventListener('click', myPosition);
  ui['metric-select'].addEventListener('change', () => {
    state.metricKey = ui['metric-select'].value;
    renderRanking();
  });
  ui.search.addEventListener('input', renderRanking);
  ui['limit-select'].addEventListener('change', renderRanking);
  ui['ranking-body'].addEventListener('click', (event) => {
    const button = event.target.closest('button[data-edit-id]');
    if (button) openAlias(button.dataset.editId, button);
  });
  ui['cancel-alias'].addEventListener('click', closeAlias);
  ui['alias-dialog'].addEventListener('cancel', (event) => {
    event.preventDefault();
    closeAlias();
  });
  ui['alias-dialog'].addEventListener('close', () => {
    // Rendering may have replaced the opener; recover focus by its stable ID.
    const replacement = [...ui['ranking-body'].querySelectorAll('button[data-edit-id]')].find((button) => button.dataset.editId === state.editingId);
    if (state.aliasOpener?.isConnected) state.aliasOpener.focus();
    else if (replacement) replacement.focus();
    else ui.search.focus();
    state.editingId = null;
  });
  ui['alias-name'].addEventListener('input', () => clearFieldError(ui['alias-name'], ui['alias-error']));
  ui['alias-form'].addEventListener('submit', (event) => {
    event.preventDefault();
    if (!state.editingId) return;
    try {
      const id = state.editingId;
      const next = { ...state.aliases };
      const raw = ui['alias-name'].value;
      if (/[\u0000-\u001f\u007f-\u009f]/u.test(raw)) throw new Error('El nombre no puede contener caracteres de control.');
      const name = raw.trim();
      if (name) next[id] = name;
      else delete next[id];
      state.aliases = validateAliases(next);
      const saved = writeStored('aliases', state.aliases);
      renderProfile();
      renderRanking();
      closeAlias();
      announce(`Nombre local de ${id}: ${nameFor(id)}.${saved ? ' Guardado en este navegador.' : ' Solo en memoria; revisa el aviso de almacenamiento.'}`);
    } catch (error) { fieldError(ui['alias-name'], ui['alias-error'], describeError(error)); }
  });
  ui['import-aliases'].addEventListener('click', () => ui['aliases-file'].click());
  ui['aliases-file'].addEventListener('change', () => { void importAliases(ui['aliases-file'].files[0]); });
  ui['export-aliases'].addEventListener('click', exportAliases);
  window.addEventListener('pagehide', () => {
    state.destroyed = true;
    window.clearTimeout(state.autoTimer);
    state.controller?.abort();
  });
  // A bfcache restoration is another opening: require profile choice again.
  window.addEventListener('pageshow', (event) => {
    if (!event.persisted) return;
    state.destroyed = false;
    state.profile = null;
    if (ui['alias-dialog'].open) closeAlias();
    renderProfile();
    renderRanking();
    if (!ui['profile-dialog'].open) openProfile();
    void refresh();
    scheduleAuto();
  });
}

ui['source-link'].href = SOURCE_URL;
restoreLocalData();
bindEvents();
renderAll();
// A remembered ID prefills the form only; there is deliberately no automatic login.
openProfile();
void refresh();
scheduleAuto();
