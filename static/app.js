'use strict';

(() => {
  const $ = (selector, root = document) => root.querySelector(selector);
  const $$ = (selector, root = document) => Array.from(root.querySelectorAll(selector));
  const state = { cases: [], tasks: [], stats: {}, csrf: '', loaded: false, loading: false, view: 'resumen', detail: null, detailId: null, detailTab: 'datos', detailRequest: 0, actor: '', submitting: false, taskBusy: new Set() };
  const labels = { activo: 'Activo', cerrado: 'Cerrado', pendiente: 'Por hacer', en_curso: 'En curso', completada: 'Completada', alta: 'Alta', media: 'Media', baja: 'Baja', actuacion: 'Actuación', llamada: 'Llamada', audiencia: 'Audiencia', documento: 'Documento', nota: 'Nota' };
  let toastTimer;
  let resumeAction = null;

  const node = (tag, className, content) => {
    const element = document.createElement(tag);
    if (className) element.className = className;
    if (content !== undefined && content !== null) element.textContent = String(content);
    return element;
  };
  const actionButton = (label, className, action) => {
    const button = node('button', className, label);
    button.type = 'button';
    button.addEventListener('click', action);
    return button;
  };
  const text = value => value === undefined || value === null ? '' : String(value);
  const normalized = value => text(value).normalize('NFD').replace(/[\u0300-\u036f]/g, '').toLocaleLowerCase('es');
  const initials = name => text(name).trim().split(/\s+/).slice(0, 2).map(part => part.charAt(0)).join('').toUpperCase() || '?';
  const today = () => {
    const parts = new Intl.DateTimeFormat('en', { timeZone: 'America/Lima', year: 'numeric', month: '2-digit', day: '2-digit' }).formatToParts(new Date());
    const part = name => parts.find(item => item.type === name).value;
    return `${part('year')}-${part('month')}-${part('day')}`;
  };
  const dateLabel = (value, full = false) => {
    if (!value) return 'Sin fecha límite';
    const valueText = text(value);
    const date = /^\d{4}-\d{2}-\d{2}$/.test(valueText) ? new Date(`${valueText}T12:00:00Z`) : new Date(valueText);
    if (Number.isNaN(date.getTime())) return valueText;
    return new Intl.DateTimeFormat('es-PE', { timeZone: 'America/Lima', day: 'numeric', month: full ? 'long' : 'short', ...(full ? { year: 'numeric' } : {}) }).format(date);
  };
  const isOverdue = task => task.status !== 'completada' && task.due_date && task.due_date < today();
  const taskSort = (a, b) => {
    const completed = Number(a.status === 'completada') - Number(b.status === 'completada');
    return completed || text(a.due_date || '9999').localeCompare(text(b.due_date || '9999')) || ({ alta: 0, media: 1, baja: 2 }[a.priority] ?? 1) - ({ alta: 0, media: 1, baja: 2 }[b.priority] ?? 1) || b.id - a.id;
  };
  const badge = (value, kind = '') => node('span', `badge ${kind}`, value);
  const emptyInline = message => node('div', 'empty-inline', message);

  function notify(message, error = false) {
    const toast = $('#toast');
    toast.textContent = message;
    toast.classList.toggle('error', error);
    toast.hidden = false;
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => { toast.hidden = true; }, error ? 7000 : 4300);
  }

  function setConnection(online) {
    $('#connection-dot').classList.toggle('online', online);
    $('#connection-label').textContent = online ? 'Espacio conectado' : 'Sin conexión reciente';
  }

  function syncWarning(message) {
    const banner = $('#status-message');
    banner.replaceChildren(node('span', '', message), actionButton('Reintentar', '', () => loadData({ quiet: true })));
    banner.hidden = false;
  }

  async function request(path, options = {}) {
    const headers = { ...(options.headers || {}) };
    if (options.method && options.method !== 'GET') {
      headers['X-CSRF-Token'] = state.csrf;
      if (!(options.body instanceof FormData)) headers['Content-Type'] = 'application/json';
    }
    let response;
    try {
      response = await fetch(path, { ...options, headers, credentials: 'same-origin', cache: 'no-store' });
    } catch (_) {
      throw new Error('No pudimos conectar con el servidor. Comprueba tu conexión y vuelve a intentar.');
    }
    const contentType = response.headers.get('Content-Type') || '';
    const data = contentType.includes('application/json') ? await response.json() : null;
    if (!response.ok) throw new Error(data?.error || (response.status === 413 ? 'El archivo es demasiado grande.' : 'No se pudo completar la operación. Vuelve a intentar.'));
    if (!data) throw new Error('El servidor no devolvió una respuesta válida. Vuelve a intentar.');
    return data;
  }

  async function loadData({ quiet = false, refreshDetail = true } = {}) {
    if (state.loading) return false;
    state.loading = true;
    if (!state.loaded) {
      $('#loading-state').hidden = false;
      $('#error-state').hidden = true;
    }
    try {
      const data = await request('/api/bootstrap');
      state.cases = data.cases || [];
      state.tasks = data.tasks || [];
      state.stats = data.stats || {};
      state.csrf = data.csrf_token || '';
      state.loaded = true;
      $('#loading-state').hidden = true;
      $('#error-state').hidden = true;
      $('#status-message').hidden = true;
      $('#content').hidden = false;
      setConnection(true);
      $('#sync-time').textContent = `Actualizado a las ${new Intl.DateTimeFormat('es-PE', { timeZone: 'America/Lima', hour: '2-digit', minute: '2-digit' }).format(new Date())} · Lima`;
      updateCategoryFilter();
      renderMain();
      renderImportNote(data.import_summary);
      if (refreshDetail && state.detailId !== null && $('#case-dialog').open && !$('#form-dialog').open) await refreshCase();
      return true;
    } catch (error) {
      setConnection(false);
      $('#loading-state').hidden = true;
      if (state.loaded) syncWarning('No se han podido actualizar los datos. Estás viendo la última versión recibida.');
      else {
        $('#error-state').hidden = false;
        $('#load-error-text').textContent = error.message;
      }
      if (!quiet && state.loaded) notify(error.message, true);
      return false;
    } finally {
      state.loading = false;
    }
  }

  function setView(view) {
    if (!['resumen', 'casos', 'pendientes'].includes(view)) view = 'resumen';
    state.view = view;
    const headings = {
      resumen: ['TU ESPACIO COMPARTIDO', 'Todo, en un solo lugar.', 'Casos, actuaciones y próximos pasos de Mario.'],
      casos: ['EXPEDIENTES COMPARTIDOS', 'Cada caso, al día.', 'Consulta el expediente y registra cada actuación.'],
      pendientes: ['PRÓXIMOS PASOS', 'Lo que queda por hacer.', 'Organiza tareas, responsables y fechas límite.']
    };
    $('#page-eyebrow').textContent = headings[view][0];
    $('#page-title').textContent = headings[view][1];
    $('#page-subtitle').textContent = headings[view][2];
    $('#breadcrumb').textContent = { resumen: 'Resumen', casos: 'Casos', pendientes: 'Pendientes' }[view];
    document.title = `${$('#breadcrumb').textContent} · Mario Casos`;
    $$('.nav-item').forEach(button => {
      button.classList.toggle('active', button.dataset.view === view);
      if (button.dataset.view === view) button.setAttribute('aria-current', 'page');
      else button.removeAttribute('aria-current');
    });
    $('#summary-view').hidden = view !== 'resumen';
    $('#cases-view').hidden = view !== 'casos';
    $('#tasks-view').hidden = view !== 'pendientes';
    if (location.hash !== `#${view}`) history.replaceState(null, '', `#${view}`);
  }

  function renderMain() {
    $('#nav-case-count').textContent = state.cases.length;
    $('#nav-task-count').textContent = state.tasks.filter(task => task.status !== 'completada').length;
    renderStats();
    renderDashboard();
    renderCases();
    renderTasks();
  }

  function renderStats() {
    const definitions = [
      ['Casos activos', state.stats.active_cases ?? state.cases.filter(item => item.status === 'activo').length, `${state.cases.length} casos registrados`, '▱', ''],
      ['Tareas pendientes', state.stats.pending_tasks ?? state.tasks.filter(item => item.status !== 'completada').length, 'Próximos pasos del equipo', '✓', ''],
      ['Plazos vencidos', state.stats.overdue_tasks ?? state.tasks.filter(isOverdue).length, 'Requieren seguimiento', '◷', 'alert'],
      ['Actuados registrados', state.stats.activities_count ?? 0, 'Historial de los casos', '≡', '']
    ];
    $('#stats-grid').replaceChildren(...definitions.map(([label, number, note, symbol, className]) => {
      const card = node('article', `stat-card ${className}`);
      const top = node('div', 'stat-top');
      top.append(node('span', '', label), node('span', 'stat-symbol', symbol));
      card.append(top, node('p', 'stat-value', number), node('p', 'stat-note', note));
      return card;
    }));
  }

  function renderDashboard() {
    const tasks = state.tasks.filter(task => task.status !== 'completada').sort(taskSort).slice(0, 5);
    $('#dashboard-tasks').replaceChildren(...(tasks.length ? tasks.map(renderDashboardTask) : [emptyInline('Sin tareas pendientes. Agrega el próximo paso del equipo.')]));
    const cases = [...state.cases].sort((a, b) => text(b.updated_at).localeCompare(text(a.updated_at)) || b.id - a.id).slice(0, 5);
    $('#dashboard-cases').replaceChildren(...(cases.length ? cases.map((item, index) => {
      const button = actionButton('', 'dashboard-case', () => openCase(item.id));
      const avatar = node('span', `avatar ${['', 'warm', 'blue'][index % 3]}`, initials(item.name));
      avatar.setAttribute('aria-hidden', 'true');
      const body = node('div', 'dashboard-case-body');
      body.append(node('h3', '', item.name), node('p', '', [item.category || 'Sin categoría', item.a_number ? `A ${item.a_number}` : item.country].filter(Boolean).join(' · ')));
      button.append(avatar, body, badge(labels[item.status] || item.status, item.status === 'cerrado' ? 'closed' : ''), node('span', 'chevron', '›'));
      return button;
    }) : [emptyInline('Tu espacio está listo para el primer caso.')]));
  }

  function completeButton(task, className = 'task-checkbox') {
    const completed = task.status === 'completada';
    const button = actionButton(completed ? '✓' : '', `${className}${completed ? ' done' : ''}`, () => updateTaskStatus(task, completed ? 'pendiente' : 'completada'));
    button.setAttribute('aria-label', `${completed ? 'Reabrir' : 'Completar'} tarea: ${task.title}`);
    button.disabled = state.taskBusy.has(task.id);
    return button;
  }

  function renderDashboardTask(task) {
    const item = node('div', 'dashboard-task');
    const body = node('div', 'dashboard-task-body');
    body.append(node('h3', 'task-title', task.title));
    const meta = node('div', 'task-meta');
    if (task.case_id) meta.append(actionButton(task.case_name || 'Ver caso', 'case-link', () => openCase(task.case_id)));
    else meta.append(node('span', '', 'Tarea general'));
    if (task.assignee) meta.append(node('span', '', `· ${task.assignee}`));
    if (task.priority === 'alta') meta.append(badge('Alta', 'high'));
    body.append(meta);
    const due = node('span', `task-date${isOverdue(task) ? ' overdue' : ''}`, task.due_date ? dateLabel(task.due_date) : 'Sin plazo');
    if (isOverdue(task)) due.setAttribute('aria-label', `Vencida: ${dateLabel(task.due_date, true)}`);
    item.append(completeButton(task), body, due);
    return item;
  }

  function updateCategoryFilter() {
    const select = $('#case-category');
    const previous = select.value;
    const options = [...new Set(state.cases.map(item => item.category).filter(Boolean))].sort((a, b) => a.localeCompare(b, 'es'));
    select.replaceChildren(new Option('Todas las categorías', ''), ...options.map(item => new Option(item, item)));
    if (options.includes(previous)) select.value = previous;
  }

  function renderCases() {
    const search = normalized($('#case-search').value);
    const status = $('#case-status').value;
    const category = $('#case-category').value;
    const cases = state.cases.filter(item => (!search || normalized([item.name, item.a_number, item.reference, item.country, item.owner, item.category].join(' ')).includes(search)) && (!status || item.status === status) && (!category || item.category === category));
    $('#case-results-count').textContent = cases.length;
    $('#cases-empty').hidden = cases.length > 0;
    $('.table-wrap').hidden = cases.length === 0;
    $('#cases-table-body').replaceChildren(...cases.map(item => {
      const row = node('tr');
      const name = node('td');
      name.append(actionButton(item.name, 'table-case', () => openCase(item.id)), node('p', 'table-case-meta', item.reference || (item.owner ? `Responsable: ${item.owner}` : 'Sin referencia')));
      const number = node('td', 'table-number', item.a_number || '—');
      const categoryCell = node('td', '', item.category || '—');
      const country = node('td', 'country-cell', item.country || '—');
      const statusCell = node('td');
      statusCell.append(badge(labels[item.status] || item.status, item.status === 'cerrado' ? 'closed' : ''));
      const taskCount = node('td', 'pending-count', item.task_count ? `${item.task_count} pendiente${item.task_count === 1 ? '' : 's'}` : 'Al día');
      const openCell = node('td');
      const open = actionButton('›', 'open-case-button', () => openCase(item.id));
      open.setAttribute('aria-label', `Abrir caso ${item.name}`);
      openCell.append(open);
      row.append(name, number, categoryCell, country, statusCell, taskCount, openCell);
      return row;
    }));
  }

  function renderTasks() {
    const search = normalized($('#task-search').value);
    const status = $('#task-status').value;
    const priority = $('#task-priority').value;
    const tasks = state.tasks.filter(task => (!search || normalized([task.title, task.description, task.case_name, task.assignee].join(' ')).includes(search)) && (!status || (status === 'abiertas' ? task.status !== 'completada' : task.status === status)) && (!priority || task.priority === priority)).sort(taskSort);
    $('#task-results-count').textContent = tasks.length;
    $('#tasks-empty').hidden = tasks.length > 0;
    $('#task-list').replaceChildren(...tasks.map(task => renderTaskCard(task)));
  }

  function renderTaskCard(task, detail = false) {
    const card = node('article', `task-card${task.status === 'completada' ? ' completed' : ''}`);
    const body = node('div', 'task-card-body');
    body.append(node('h3', 'task-title', task.title));
    if (task.description) body.append(node('p', 'task-description', task.description));
    const meta = node('div', 'task-meta');
    if (!detail) {
      if (task.case_id) meta.append(actionButton(task.case_name || 'Ver caso', 'case-link', () => openCase(task.case_id)));
      else meta.append(node('span', '', 'Tarea general'));
    }
    meta.append(node('span', '', task.assignee || 'Sin responsable'));
    const date = node('span', isOverdue(task) ? 'task-date overdue' : 'task-date', `${isOverdue(task) ? 'Venció el ' : ''}${dateLabel(task.due_date)}`);
    meta.append(date, badge(`Prioridad ${labels[task.priority] || task.priority}`, { alta: 'high', media: 'medium', baja: 'low' }[task.priority] || ''), badge(labels[task.status] || task.status, task.status === 'completada' ? 'closed' : task.status === 'en_curso' ? 'progress' : ''));
    body.append(meta);
    const actions = node('div', 'task-card-actions');
    if (task.status !== 'completada') {
      const next = task.status === 'en_curso' ? 'pendiente' : 'en_curso';
      const statusButton = actionButton(next === 'en_curso' ? 'Iniciar' : 'Por hacer', 'button secondary tiny', () => updateTaskStatus(task, next));
      statusButton.disabled = state.taskBusy.has(task.id);
      actions.append(statusButton);
    }
    actions.append(actionButton('Editar', 'text-button', () => requireActor(() => openTaskForm(task))));
    card.append(completeButton(task), body, actions);
    return card;
  }

  function renderImportNote(summary) {
    const note = $('#import-note');
    if (!summary) { note.replaceChildren(); return; }
    const count = summary.sources_imported ?? summary.source_count ?? summary.sources;
    note.replaceChildren(node('p', '', count ? `${count} registros originales conservados. Puedes consultar los datos del Excel dentro de cada caso.` : 'Los datos originales importados se conservan en cada expediente.'));
    const skipped = summary.skipped_rows || [];
    const identity = summary.identity_issues || [];
    const incomplete = summary.incomplete_names || [];
    if (!skipped.length && !identity.length && !incomplete.length) return;
    const details = node('details', 'import-audit');
    details.append(node('summary', '', `Detalle de la importación${identity.length ? ` · ${identity.length} coincidencias para revisar` : ''}`));
    if (skipped.length) {
      const structural = skipped.filter(item => /encabezado|título|sección/.test(text(item.reason))).length;
      details.append(node('p', '', `${structural} encabezados o títulos omitidos${skipped.length > structural ? `; ${skipped.length - structural} filas sin identificación suficiente` : ''}.`));
    }
    if (identity.length) details.append(node('p', '', `${identity.length} registros se mantuvieron en casos separados porque sus identificadores o nombres son ambiguos. Los datos originales se conservaron.`));
    if (incomplete.length) details.append(node('p', '', `${incomplete.length} registros tienen un nombre incompleto en el Excel. Puedes completar sus datos al revisar el expediente.`));
    const list = node('ul');
    [...skipped.filter(item => !/encabezado|título|sección/.test(text(item.reason))), ...identity, ...incomplete].forEach(item => list.append(node('li', '', `${item.worksheet || 'Hoja'} · fila ${item.row_number ?? '—'}: ${item.reason || 'Revisar nombre incompleto'}`)));
    if (list.childElementCount) details.append(list);
    note.append(details);
  }

  async function openCase(id) {
    state.detailId = id;
    state.detailTab = 'datos';
    const dialog = $('#case-dialog');
    const loading = node('div', 'detail-loading');
    const loadingTitle = node('h2', '', 'Cargando expediente…');
    loadingTitle.id = 'case-dialog-title';
    loading.append(loadingTitle);
    $('#case-detail').replaceChildren(loading);
    if (!dialog.open) dialog.showModal();
    await refreshCase();
  }

  async function refreshCase() {
    const id = state.detailId;
    if (id === null) return;
    const serial = ++state.detailRequest;
    try {
      const item = await request(`/api/cases/${id}`);
      if (serial !== state.detailRequest || id !== state.detailId) return;
      state.detail = item;
      renderCaseDetail();
    } catch (error) {
      if (serial !== state.detailRequest) return;
      const container = node('div', 'detail-error');
      const title = node('h2', '', 'No pudimos cargar el expediente');
      title.id = 'case-dialog-title';
      container.append(title, node('p', 'form-error', error.message), actionButton('Volver a intentar', 'button secondary', refreshCase));
      $('#case-detail').replaceChildren(container);
    }
  }

  function renderCaseDetail() {
    const item = state.detail;
    if (!item) return;
    const header = node('div', 'detail-header');
    const title = node('h2', '', item.name);
    title.id = 'case-dialog-title';
    const meta = node('div', 'detail-header-meta');
    meta.append(badge(labels[item.status] || item.status, item.status === 'cerrado' ? 'closed' : ''), node('span', '', item.a_number ? `Número A: ${item.a_number}` : 'Sin número A'), node('span', '', item.category || 'Sin categoría'));
    const actions = node('div', 'detail-header-actions');
    actions.append(actionButton('+ Registrar actuado', 'button primary', () => requireActor(() => openActivityForm(item))), actionButton('+ Nueva tarea', 'button secondary', () => requireActor(() => openTaskForm(null, item.id))), actionButton('Editar caso', 'text-button', () => requireActor(() => openCaseForm(item))));
    header.append(title, meta, actions);
    const tabs = node('div', 'detail-tabs');
    tabs.setAttribute('role', 'tablist');
    tabs.setAttribute('aria-label', 'Contenido del expediente');
    const activityCount = item.activities?.length || 0;
    const pendingCount = (item.tasks || []).filter(task => task.status !== 'completada').length;
    const tabList = [['datos', 'Datos del caso'], ['actuados', `Actuados (${activityCount})`], ['tareas', `Tareas (${pendingCount})`]];
    tabList.forEach(([key, label], index) => {
      const button = actionButton(label, `detail-tab${state.detailTab === key ? ' active' : ''}`, () => { state.detailTab = key; renderCaseDetail(); $(`[data-detail-tab="${key}"]`).focus(); });
      button.dataset.detailTab = key;
      button.id = `tab-${key}`;
      button.setAttribute('role', 'tab');
      button.setAttribute('aria-selected', state.detailTab === key ? 'true' : 'false');
      button.setAttribute('aria-controls', 'detail-tab-panel');
      button.tabIndex = state.detailTab === key ? 0 : -1;
      button.addEventListener('keydown', event => {
        if (!['ArrowLeft', 'ArrowRight', 'Home', 'End'].includes(event.key)) return;
        event.preventDefault();
        const next = event.key === 'Home' ? 0 : event.key === 'End' ? tabList.length - 1 : (index + (event.key === 'ArrowRight' ? 1 : -1) + tabList.length) % tabList.length;
        state.detailTab = tabList[next][0];
        renderCaseDetail();
        $(`[data-detail-tab="${state.detailTab}"]`).focus();
      });
      tabs.append(button);
    });
    const section = node('section', 'detail-section');
    section.id = 'detail-tab-panel';
    section.setAttribute('role', 'tabpanel');
    section.setAttribute('aria-labelledby', `tab-${state.detailTab}`);
    if (state.detailTab === 'datos') renderCaseData(item, section);
    if (state.detailTab === 'actuados') renderActivities(item, section);
    if (state.detailTab === 'tareas') {
      const top = node('div', 'section-top');
      top.append(node('h3', '', 'Próximos pasos del caso'), actionButton('+ Agregar tarea', 'text-button', () => requireActor(() => openTaskForm(null, item.id))));
      const list = node('div', 'detail-task-list');
      const tasks = [...(item.tasks || [])].sort(taskSort);
      list.append(...(tasks.length ? tasks.map(task => renderTaskCard(task, true)) : [emptyInline('Este caso no tiene tareas. Registra el siguiente paso.')]));
      section.append(top, list);
    }
    $('#case-detail').replaceChildren(header, tabs, section);
  }

  function renderCaseData(item, section) {
    const fields = [['Número A', item.a_number], ['Referencia / expediente', item.reference], ['Categoría', item.category], ['Responsable del caso', item.owner], ['País', item.country], ['Juzgado / corte', item.court], ['Fecha de audiencia', item.hearing_date ? dateLabel(item.hearing_date, true) : ''], ['Última actualización', item.updated_at ? dateLabel(item.updated_at, true) : '']];
    const grid = node('dl', 'detail-grid');
    fields.forEach(([label, value]) => {
      const field = node('div', 'detail-field');
      field.append(node('dt', '', label.toLocaleUpperCase('es')), node('dd', '', value || 'Sin registrar'));
      grid.append(field);
    });
    section.append(grid);
    const notes = node('div', 'detail-notes');
    notes.append(node('h3', '', 'Notas del caso'), node('p', '', item.notes || 'Sin notas adicionales.'));
    section.append(notes);
    if (item.sources?.length) {
      const details = node('details', 'source-details');
      details.append(node('summary', '', `Datos originales del Excel (${item.sources.length} registros)`));
      item.sources.forEach(source => {
        const block = node('div', 'source-item');
        block.append(node('h4', '', `${source.worksheet || 'Hoja'} · fila ${source.row_number ?? '—'}`));
        const values = node('dl', 'source-grid');
        Object.entries(source.data || {}).forEach(([label, value]) => {
          values.append(node('dt', '', label), node('dd', '', typeof value === 'object' && value !== null ? JSON.stringify(value) : text(value)));
        });
        block.append(values);
        details.append(block);
      });
      section.append(details);
    }
  }

  function renderActivities(item, section) {
    const top = node('div', 'section-top');
    top.append(node('h3', '', 'Historial de actuaciones'), actionButton('+ Registrar actuado', 'text-button', () => requireActor(() => openActivityForm(item))));
    section.append(top);
    if (!item.activities?.length) { section.append(emptyInline('Aún no hay actuados registrados. Deja constancia de la primera actuación.')); return; }
    const timeline = node('div', 'timeline');
    item.activities.forEach(activity => {
      const entry = node('article', 'timeline-item');
      const dot = node('span', 'timeline-dot');
      dot.setAttribute('aria-hidden', 'true');
      entry.append(dot, node('p', 'timeline-date', dateLabel(activity.occurred_on, true)), node('h3', 'timeline-title', activity.title));
      if (activity.description) entry.append(node('p', 'timeline-description', activity.description));
      const footer = node('div', 'timeline-footer');
      footer.append(badge(labels[activity.type] || activity.type, 'closed'), node('span', '', `Registró: ${activity.author}`));
      entry.append(footer);
      timeline.append(entry);
    });
    section.append(timeline);
  }

  function updateActor() {
    $('#actor-name').textContent = state.actor || 'Indicar mi nombre';
    $('#actor-initial').textContent = initials(state.actor);
    $('#actor-button').setAttribute('aria-label', state.actor ? `Registras como ${state.actor}. Cambiar nombre` : 'Indicar tu nombre para registrar cambios');
  }

  function requireActor(action) {
    if (!state.loaded) { notify('Espera a que se cargue el espacio para registrar cambios.', true); return; }
    if (state.actor.trim()) action();
    else { resumeAction = action; openActorForm(); }
  }

  function field(form, name, label, options = {}) {
    const wrap = node('div', `form-field${options.full ? ' full' : ''}${options.mobileFull ? ' mobile-full' : ''}`);
    const labelNode = node('label', '', label);
    const id = `field-${name}`;
    labelNode.htmlFor = id;
    if (options.required) labelNode.append(node('span', 'required-star', '*'));
    let input;
    if (options.choices) {
      input = node('select');
      options.choices.forEach(([value, title]) => input.append(new Option(title, String(value))));
    } else if (options.textarea) input = node('textarea');
    else {
      input = node('input');
      input.type = options.type || 'text';
      if (options.accept) input.accept = options.accept;
    }
    input.name = name;
    input.id = id;
    input.required = Boolean(options.required);
    if (options.value !== undefined && options.type !== 'file') input.value = text(options.value);
    if (options.placeholder) input.placeholder = options.placeholder;
    if (options.maxLength) input.maxLength = options.maxLength;
    if (options.autocomplete) input.autocomplete = options.autocomplete;
    wrap.append(labelNode, input);
    if (options.hint) {
      const hint = node('p', 'form-hint', options.hint);
      hint.id = `${id}-hint`;
      input.setAttribute('aria-describedby', hint.id);
      wrap.append(hint);
    }
    $('.form-fields', form).append(wrap);
    return input;
  }

  function createForm(title, submitLabel, handler, { intro = '', author = true } = {}) {
    const dialog = $('#form-dialog');
    if (state.submitting) return null;
    $('#form-dialog-title').textContent = title;
    const form = node('form', 'form-body');
    if (intro) form.append(node('p', 'form-intro', intro));
    form.append(node('div', 'form-fields'));
    if (author) {
      const row = node('div', 'form-author');
      const label = node('span');
      label.append(document.createTextNode('Registrará: '), node('strong', '', state.actor));
      row.append(label, actionButton('Cambiar nombre', '', () => {
        const actorInput = $('#field-author', form);
        if (actorInput) { actorInput.focus(); return; }
        const input = field(form, 'author', 'Tu nombre para este registro', { required: true, value: state.actor, full: true, maxLength: 120 });
        input.focus();
      }));
      form.append(row);
    }
    const error = node('div', 'form-error');
    error.hidden = true;
    error.setAttribute('role', 'alert');
    const actions = node('div', 'form-actions');
    const cancel = actionButton('Cancelar', 'button secondary', () => dialog.close());
    const submit = node('button', 'button primary', submitLabel);
    submit.type = 'submit';
    actions.append(cancel, submit);
    form.append(error, actions);
    form.addEventListener('submit', async event => {
      event.preventDefault();
      if (state.submitting || !form.reportValidity()) return;
      const data = Object.fromEntries(new FormData(form).entries());
      if (author) {
        data.author = text(data.author || state.actor).trim();
        if (!data.author) { error.textContent = 'Indica el nombre de quien registra este cambio.'; error.hidden = false; return; }
      }
      state.submitting = true;
      error.hidden = true;
      submit.textContent = 'Guardando…';
      $$('.button, .text-button, button', form).forEach(button => { button.disabled = true; });
      $$('.form-dialog-header button', dialog).forEach(button => { button.disabled = true; });
      try {
        await handler(data, form);
      } catch (failure) {
        error.textContent = failure.message;
        error.hidden = false;
        error.scrollIntoView({ block: 'nearest' });
      } finally {
        state.submitting = false;
        submit.textContent = submitLabel;
        $$('button', form).forEach(button => { button.disabled = false; });
        $$('.form-dialog-header button', dialog).forEach(button => { button.disabled = false; });
      }
    });
    $('#form-dialog-body').replaceChildren(form);
    if (!dialog.open) dialog.showModal();
    return form;
  }

  function persistActor(name) {
    state.actor = name.trim();
    try { localStorage.setItem('mario-casos-actor', state.actor); } catch (_) { /* The name still works for this session. */ }
    updateActor();
  }

  function openActorForm() {
    const form = createForm('¿Quién está registrando?', 'Guardar nombre', async data => {
      const name = text(data.name).trim();
      if (!name) throw new Error('Escribe tu nombre.');
      persistActor(name);
      $('#form-dialog').close();
      const next = resumeAction;
      resumeAction = null;
      state.submitting = false;
      if (next) next();
      else notify('Tu nombre está listo para los próximos registros.');
    }, { author: false, intro: 'Cada registro deja constancia de quién lo realizó. Este nombre se guarda en este navegador; puedes cambiarlo cuando lo necesites.' });
    if (!form) return;
    field(form, 'name', 'Tu nombre', { required: true, value: state.actor, full: true, maxLength: 120, autocomplete: 'name', placeholder: 'Nombre de quien registra' }).focus();
  }

  async function afterMutation(message, caseId = null, closeForm = true) {
    if (closeForm) $('#form-dialog').close();
    notify(message);
    const synced = await loadData({ quiet: true, refreshDetail: false });
    if (!synced) syncWarning('El cambio se guardó, pero no pudimos actualizar la lista. Reintenta para ver la versión reciente.');
    if (caseId !== null && state.detailId === caseId && $('#case-dialog').open && !$('#form-dialog').open) await refreshCase();
  }

  function openCaseForm(item = null) {
    const form = createForm(item ? 'Editar caso' : 'Registrar nuevo caso', item ? 'Guardar cambios' : 'Crear caso', async data => {
      if (data.author !== state.actor) persistActor(data.author);
      const created = await request(item ? `/api/cases/${item.id}` : '/api/cases', { method: item ? 'PATCH' : 'POST', body: JSON.stringify(data) });
      await afterMutation(item ? 'Los datos del caso se actualizaron.' : 'Caso registrado. Ya puedes agregar actuados y tareas.', item?.id ?? null);
      if (!item) await openCase(created.id);
    });
    if (!form) return;
    const values = item || {};
    field(form, 'name', 'Nombre del cliente / caso', { required: true, value: values.name, full: true, maxLength: 300, placeholder: 'Nombre completo o identificación del caso' });
    field(form, 'a_number', 'Número A', { value: values.a_number, maxLength: 120, placeholder: 'Si corresponde' });
    field(form, 'reference', 'Referencia / expediente', { value: values.reference, maxLength: 300 });
    field(form, 'category', 'Categoría', { value: values.category, maxLength: 120, placeholder: 'Por ejemplo, trámite migratorio' });
    field(form, 'status', 'Estado', { choices: [['activo', 'Activo'], ['cerrado', 'Cerrado']], value: values.status || 'activo' });
    field(form, 'owner', 'Responsable del caso', { value: values.owner ?? 'Mario', maxLength: 120 });
    field(form, 'country', 'País', { value: values.country, maxLength: 120 });
    field(form, 'court', 'Juzgado / corte', { value: values.court, maxLength: 300 });
    field(form, 'hearing_date', 'Fecha de audiencia', { type: 'date', value: values.hearing_date });
    field(form, 'notes', 'Notas del caso', { textarea: true, value: values.notes, full: true, maxLength: 20000, placeholder: 'Información relevante para el equipo' });
  }

  function openActivityForm(item) {
    const form = createForm('Registrar actuado', 'Guardar actuado', async data => {
      if (data.author !== state.actor) persistActor(data.author);
      await request(`/api/cases/${item.id}/activities`, { method: 'POST', body: JSON.stringify(data) });
      state.detailTab = 'actuados';
      await afterMutation('Actuado registrado en el historial del caso.', item.id);
    }, { intro: `Caso: ${item.name}. Registra la gestión realizada y su fecha.` });
    if (!form) return;
    field(form, 'title', 'Título del actuado', { required: true, full: true, maxLength: 300, placeholder: 'Por ejemplo, presentación de escrito' });
    field(form, 'type', 'Tipo de actuación', { choices: [['actuacion', 'Actuación'], ['llamada', 'Llamada'], ['audiencia', 'Audiencia'], ['documento', 'Documento'], ['nota', 'Nota']], value: 'actuacion' });
    field(form, 'occurred_on', 'Fecha de la actuación', { required: true, type: 'date', value: today() });
    field(form, 'description', 'Detalle', { full: true, textarea: true, maxLength: 20000, placeholder: 'Qué se realizó, resultado y próximos pasos' });
  }

  function openTaskForm(task = null, caseId = null) {
    const form = createForm(task ? 'Editar tarea' : 'Agregar tarea pendiente', task ? 'Guardar cambios' : 'Crear tarea', async data => {
      if (data.author !== state.actor) persistActor(data.author);
      if (!task) data.case_id = data.case_id ? Number(data.case_id) : null;
      await request(task ? `/api/tasks/${task.id}` : '/api/tasks', { method: task ? 'PATCH' : 'POST', body: JSON.stringify(data) });
      await afterMutation(task ? 'La tarea se actualizó.' : 'Tarea agregada a los pendientes del equipo.', task?.case_id ?? data.case_id ?? null);
    });
    if (!form) return;
    const values = task || {};
    field(form, 'title', '¿Qué queda por hacer?', { required: true, value: values.title, full: true, maxLength: 300, placeholder: 'Describe el próximo paso' });
    if (!task) field(form, 'case_id', 'Caso relacionado', { full: true, choices: [['', 'Tarea general (sin caso)'], ...state.cases.map(item => [item.id, item.name])], value: caseId ?? '', hint: 'También puedes registrar tareas generales del equipo.' });
    else if (task.case_id) $('.form-fields', form).append(node('p', 'form-field full inline-note', `Caso: ${task.case_name || state.cases.find(item => item.id === task.case_id)?.name || 'Caso vinculado'}`));
    field(form, 'assignee', 'Responsable de la tarea', { value: values.assignee, maxLength: 120, placeholder: '¿Quién se encargará?' });
    field(form, 'due_date', 'Fecha límite', { type: 'date', value: values.due_date, hint: 'Opcional. Las fechas se muestran en hora de Lima.' });
    field(form, 'priority', 'Prioridad', { choices: [['media', 'Media'], ['alta', 'Alta'], ['baja', 'Baja']], value: values.priority || 'media' });
    if (task) field(form, 'status', 'Estado', { choices: [['pendiente', 'Por hacer'], ['en_curso', 'En curso'], ['completada', 'Completada']], value: task.status });
    field(form, 'description', 'Detalle de la tarea', { textarea: true, value: values.description, full: true, maxLength: 20000, placeholder: 'Información para realizar la tarea' });
  }

  function updateTaskStatus(task, status) {
    requireActor(async () => {
      if (state.taskBusy.has(task.id)) return;
      state.taskBusy.add(task.id);
      renderMain();
      if (state.detail && $('#case-dialog').open) renderCaseDetail();
      try {
        await request(`/api/tasks/${task.id}`, { method: 'PATCH', body: JSON.stringify({ status, author: state.actor }) });
        await afterMutation({ completada: 'Tarea completada. Un pendiente menos.', en_curso: 'Tarea marcada en curso.', pendiente: 'Tarea marcada como pendiente.' }[status], task.case_id ?? null, false);
      } catch (error) {
        notify(error.message, true);
      } finally {
        state.taskBusy.delete(task.id);
        renderMain();
        if (state.detail && $('#case-dialog').open) renderCaseDetail();
      }
    });
  }

  function openImportForm() {
    const form = createForm('Importar casos desde Excel', 'Importar archivo', async (data, activeForm) => {
      const file = $('#field-file', activeForm).files[0];
      if (!file) throw new Error('Selecciona un archivo Excel.');
      if (!file.name.toLowerCase().endsWith('.xlsx')) throw new Error('Selecciona un archivo Excel con extensión .xlsx.');
      if (file.size > 10 * 1024 * 1024) throw new Error('El archivo debe pesar como máximo 10 MB.');
      const body = new FormData();
      body.append('file', file);
      body.append('author', data.author);
      const report = await request('/api/import', { method: 'POST', body });
      if (data.author !== state.actor) persistActor(data.author);
      await afterMutation(report.already_imported ? 'Este archivo ya estaba importado. No se duplicaron registros.' : `Importación completada: ${report.cases_created ?? 0} casos, ${report.tasks_created ?? 0} tareas y ${report.activities_created ?? 0} actuados.`);
      const omissions = (report.skipped_rows || []).filter(item => !/encabezado|título|sección/.test(text(item.reason)));
      if (omissions.length || report.identity_issues?.length) {
        setView('resumen');
        notify('Importación guardada. Consulta el detalle de la importación para revisar las filas sin identificación o las coincidencias ambiguas.');
      }
    }, { intro: 'Selecciona un archivo .xlsx (máximo 10 MB). La importación conserva los campos originales y no borra los casos, actuados ni tareas registrados manualmente. Volver a importar el mismo archivo no duplica registros.' });
    if (!form) return;
    field(form, 'file', 'Archivo Excel', { type: 'file', required: true, accept: '.xlsx,application/vnd.openxmlformats-officedocument.spreadsheetml.sheet', full: true });
  }

  function wireEvents() {
    $$('.nav-item').forEach(button => button.addEventListener('click', () => setView(button.dataset.view)));
    $$('[data-go]').forEach(button => button.addEventListener('click', () => setView(button.dataset.go)));
    $$('[data-new-task]').forEach(button => button.addEventListener('click', () => requireActor(() => openTaskForm())));
    $('#new-case-button').addEventListener('click', () => requireActor(() => openCaseForm()));
    $('#import-button').addEventListener('click', () => requireActor(openImportForm));
    $('#actor-button').addEventListener('click', () => { resumeAction = null; openActorForm(); });
    $('#retry-button').addEventListener('click', () => loadData());
    $('#case-search').addEventListener('input', renderCases);
    $('#case-status').addEventListener('change', renderCases);
    $('#case-category').addEventListener('change', renderCases);
    $('#task-search').addEventListener('input', renderTasks);
    $('#task-status').addEventListener('change', renderTasks);
    $('#task-priority').addEventListener('change', renderTasks);
    $$('[data-close]').forEach(button => button.addEventListener('click', () => { if (!state.submitting) document.getElementById(button.dataset.close).close(); }));
    $('#case-dialog').addEventListener('close', () => { state.detailId = null; state.detail = null; state.detailRequest++; });
    $('#form-dialog').addEventListener('cancel', event => { if (state.submitting) event.preventDefault(); });
    $('#form-dialog').addEventListener('close', () => { resumeAction = null; });
    $('#copy-link-button').addEventListener('click', async () => {
      const url = `${location.origin}${location.pathname}`;
      try {
        if (navigator.clipboard?.writeText) await navigator.clipboard.writeText(url);
        else {
          const input = node('textarea');
          input.value = url;
          input.setAttribute('aria-label', 'Enlace del espacio');
          input.style.position = 'fixed';
          input.style.opacity = '0';
          document.body.append(input);
          input.select();
          const success = document.execCommand('copy');
          input.remove();
          if (!success) throw new Error('clipboard');
        }
        notify('Enlace copiado. Compártelo con las personas que colaboran contigo.');
      } catch (_) { notify('No se pudo copiar. Puedes compartir la dirección de esta página desde tu navegador.', true); }
    });
    window.addEventListener('hashchange', () => setView(location.hash.slice(1)));
    window.addEventListener('focus', () => { if (!state.submitting) loadData({ quiet: true }); });
    window.addEventListener('online', () => loadData({ quiet: true }));
    window.addEventListener('storage', event => {
      if (event.key === 'mario-casos-actor') { state.actor = event.newValue || ''; updateActor(); }
    });
    setInterval(() => { if (!document.hidden && !state.submitting) loadData({ quiet: true }); }, 30000);
  }

  try { state.actor = localStorage.getItem('mario-casos-actor') || ''; } catch (_) { state.actor = ''; }
  updateActor();
  wireEvents();
  setView(location.hash.slice(1));
  loadData();
})();
