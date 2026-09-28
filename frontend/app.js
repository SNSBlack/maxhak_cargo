/*
  Cargo Optimizer Mini App.

  Приложение открывается внутри MAX: объект window.WebApp даёт initData, которую
  backend проверяет по подписи. Вне MAX (обычный браузер) приложение работает
  в режиме отладки: подписи нет, backend с AUTH_MODE=dev пускает запросы.
*/

const MAX = window.WebApp || null;
const INIT_DATA = MAX && MAX.initData ? MAX.initData : '';

const state = {
  meta: null,
  catalog: null,
  loaded: { trips: false, routes: false, edo: false, cargo: false },
  // Годовой объём данных не грузится целиком: список рейсов постраничный,
  // сводка считается за выбранный период.
  trips: { filter: '', query: '', offset: 0, limit: 50, total: 0, rows: [] },
  summaryPeriod: 30,
  edo: { offset: 0, limit: 40, total: 0 },
};

/* --- утилиты --- */

const $ = (sel, root = document) => root.querySelector(sel);
const $$ = (sel, root = document) => Array.from(root.querySelectorAll(sel));

const money = (value, opts = {}) => {
  if (value === null || value === undefined || Number.isNaN(value)) return 'нет данных';
  const sign = opts.sign && value > 0 ? '+' : '';
  return sign + Math.round(value).toLocaleString('ru-RU').replace(/ /g, ' ') + ' ₽';
};

const liters = (value) =>
  value === null || value === undefined ? 'нет данных' : `${Number(value).toFixed(0)} л`;

const km = (value) =>
  value === null || value === undefined ? 'нет данных' : `${Math.round(value).toLocaleString('ru-RU')} км`;

const esc = (text) =>
  String(text ?? '').replace(/[&<>"']/g, (c) =>
    ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]),
  );

const LEVEL_ICON = { ok: 'ph-check-circle', warn: 'ph-warning', danger: 'ph-x-circle', info: 'ph-info' };

async function api(path, options = {}) {
  const headers = { 'Content-Type': 'application/json', ...(options.headers || {}) };
  if (INIT_DATA) headers['X-Max-Init-Data'] = INIT_DATA;
  const resp = await fetch(path, { ...options, headers });
  const data = await resp.json().catch(() => ({}));
  if (!resp.ok) throw new Error(data.detail || `Запрос не прошёл: ${resp.status}`);
  return data;
}

function skeleton(lines = 3) {
  return `<div class="skeleton">${'<div class="skeleton__line"></div>'.repeat(lines)}</div>`;
}

function emptyState(text) {
  return `<div class="empty"><i class="ph ph-tray" aria-hidden="true"></i><p>${esc(text)}</p></div>`;
}

function errorState(text, retryAction) {
  return `
    <div class="error">
      <i class="ph ph-warning-circle" aria-hidden="true"></i>
      <p>${esc(text)}</p>
      ${retryAction ? `<button class="btn" type="button" data-retry="${esc(retryAction)}">Повторить</button>` : ''}
    </div>`;
}

/* --- навигация и тема --- */

function showScreen(name) {
  $$('.screen').forEach((el) => {
    const active = el.id === `screen-${name}`;
    el.hidden = !active;
    el.classList.toggle('is-active', active);
  });
  $$('.tab').forEach((tab) => {
    const active = tab.dataset.screen === name;
    tab.classList.toggle('is-active', active);
    tab.setAttribute('aria-selected', String(active));
  });
  loadScreen(name);
}

function initTheme() {
  const saved = (() => {
    try {
      return localStorage.getItem('cargo-theme');
    } catch (err) {
      return null;
    }
  })();
  // По умолчанию светлая: основа бренда белая. Раньше тема шла за системной, и
  // на телефоне с тёмным оформлением белой основы никто не видел.
  document.documentElement.dataset.theme = saved === 'dark' ? 'dark' : 'light';

  $('#theme-toggle').addEventListener('click', () => {
    const current = document.documentElement.dataset.theme;
    const next = current === 'dark' ? 'light' : 'dark';
    document.documentElement.dataset.theme = next;
    try {
      localStorage.setItem('cargo-theme', next);
    } catch (err) {
      /* приватный режим: тема просто не запомнится */
    }
  });
}

/* --- знакомство при первом открытии --- */

const TOUR_KEY = 'cargo-tour-seen';

const SOURCE_NOTE =
  'Показаны демонстрационные данные: подключение к 1С, оператору ЭДО и движку ' +
  'маршрутов пока смоделировано.';

const TOUR_STEPS = [
  {
    icon: 'ph-truck',
    title: 'Рейсы',
    text: 'Тапните любой рейс, чтобы увидеть себестоимость по 11 статьям: у каждой формула и источник числа. Рядом кнопка своего расчёта.',
  },
  {
    icon: 'ph-path',
    title: 'Маршрут',
    text: 'Выберите направление и машину: варианты сравниваются в рублях, литрах и часах, с разбором, за счёт чего один выгоднее.',
  },
  {
    icon: 'ph-file-text',
    title: 'ЭДО',
    text: 'Готовность компании к обязательным ЭПД и документы, которые горят. По отклонённым видно причину и что чинить.',
  },
  {
    icon: 'ph-package',
    title: 'Груз',
    text: 'Экран для грузоотправителя: стадия, срок прибытия и ставка, без внутренней кухни перевозчика.',
  },
];

function tourSeen() {
  try {
    return localStorage.getItem(TOUR_KEY) === '1';
  } catch (err) {
    return false; // приватный режим: просто покажем подсказку ещё раз
  }
}

function markTourSeen() {
  try {
    localStorage.setItem(TOUR_KEY, '1');
  } catch (err) {
    /* не критично */
  }
}

function showTour() {
  const items = TOUR_STEPS.map(
    (step) => `
      <div class="check">
        <i class="ph ${step.icon} check__icon check__icon--accent" aria-hidden="true"></i>
        <span>
          <span class="check__title">${esc(step.title)}</span>
          <span class="check__detail">${esc(step.text)}</span>
        </span>
      </div>`,
  ).join('');

  openSheet(
    'Четыре экрана',
    `<p class="card__text" style="margin-bottom:12px">Коротко, что где лежит. Показываю один раз.</p>
     <div class="rows">${items}</div>
     ${state.meta && state.meta.data_is_mock ? `<p class="source-note">${esc(SOURCE_NOTE)}</p>` : ''}
     <div class="more" style="margin-top:14px">
       <button class="btn btn--primary" type="button" id="tour-done">Понятно, начать</button>
     </div>`,
  );
}

/* --- лист --- */

function openSheet(title, html) {
  $('#sheet-title').textContent = title;
  $('#sheet-body').innerHTML = html;
  $('#sheet').hidden = false;
  document.body.style.overflow = 'hidden';
}

function closeSheet() {
  $('#sheet').hidden = true;
  document.body.style.overflow = '';
}

/* --- разбивка себестоимости --- */

function renderCost(cost, trip) {
  const items = cost.items
    .filter((item) => item.amount_rub > 0 || item.code === 'fuel')
    .map(
      (item) => `
      <div class="item">
        <div class="item__head">
          <span class="item__title">${esc(item.title)}</span>
          <span class="item__amount num">${money(item.amount_rub)}</span>
        </div>
        <div class="item__bar" style="width:${Math.max(item.share_pct, 0.6)}%"></div>
        <div class="item__formula">${esc(item.formula)}</div>
        <div class="item__source">Источник: ${esc(item.source)}</div>
      </div>`,
    )
    .join('');

  const notes = cost.warnings
    .map(
      (w) => `
      <div class="note note--${esc(w.level)}">
        <i class="ph ${LEVEL_ICON[w.level] || 'ph-info'}" aria-hidden="true"></i>
        <span>${esc(w.text)}</span>
      </div>`,
    )
    .join('');

  const marginLine =
    cost.margin_rub === null
      ? '<span class="total__sub">Ставка не задана, маржа не считается</span>'
      : `<span class="total__sub">Ставка ${money(cost.revenue_rub)}, маржа ${money(cost.margin_rub)}${
          cost.margin_pct === null ? '' : ` (${cost.margin_pct}%)`
        }</span>`;

  return `
    ${trip ? `<p class="card__text">${esc(trip.number)}, ${esc(trip.route)}, ${esc(trip.vehicle.plate || '')}</p>` : ''}
    <div class="total" style="margin-top:10px">
      <div>
        <div class="total__value num">${money(cost.total_rub)}</div>
        ${marginLine}
      </div>
      <div style="text-align:right">
        <div class="num" style="font-weight:600">${cost.per_km_rub.toFixed(2)} ₽/км</div>
        <div class="total__sub">${km(cost.distance_km)}, норма ${liters(cost.fuel_norm_l)}</div>
      </div>
    </div>
    ${notes ? `<div class="notes">${notes}</div>` : ''}
    <div class="breakdown">${items}</div>
    <p class="hint">Цена литра в расчёте: ${cost.inputs.fuel_price_rub_per_l} ₽${
      cost.inputs.winter ? ', применена зимняя надбавка к норме' : ''
    }.</p>`;
}

/* --- экран: рейсы --- */

function tripRow(trip) {
  const level = trip.status === 'завершён' ? 'neutral' : trip.status === 'в пути' ? 'ok' : 'warn';
  return `
    <button class="row" type="button" data-trip="${esc(trip.id)}">
      <span class="row__main">
        <span class="row__title">${esc(trip.route)}</span>
        <span class="row__sub">${esc(trip.number)}, ${esc(trip.vehicle.plate || '')}, ${esc(
          trip.driver.name || '',
        )}</span>
      </span>
      <span class="row__side">
        <span class="chip chip--${level}">${esc(trip.status)}</span>
        <span class="row__value num">${money(trip.revenue_rub)}</span>
      </span>
    </button>`;
}

async function loadTripPage(append = false) {
  const list = $('#trips-list');
  const more = $('#trips-more');
  if (!append) {
    state.trips.offset = 0;
    list.innerHTML = skeleton(4);
    more.innerHTML = '';
  }

  const params = new URLSearchParams({
    limit: String(state.trips.limit),
    offset: String(state.trips.offset),
  });
  if (state.trips.filter) params.set('status', state.trips.filter);
  if (state.trips.query) params.set('q', state.trips.query);

  try {
    const data = await api(`/api/trips?${params.toString()}`);
    state.trips.total = data.total;
    const html = data.trips.map(tripRow).join('');

    if (!data.total) {
      list.innerHTML = emptyState(
        state.trips.query
          ? 'Ничего не нашлось. Попробуйте другой номер, город или госномер.'
          : 'Рейсов нет. Как только они появятся в 1С, они попадут сюда.',
      );
      more.innerHTML = '';
      return;
    }

    if (append) list.insertAdjacentHTML('beforeend', html);
    else list.innerHTML = html;

    const shown = Math.min(state.trips.offset + data.trips.length, data.total);
    more.innerHTML =
      `<p class="counter">Показано ${shown} из ${data.total}</p>` +
      (data.has_more
        ? `<div class="more"><button class="btn" type="button" id="trips-more-btn">Показать ещё</button></div>`
        : '');
    list.dataset.state = 'ready';
  } catch (err) {
    list.innerHTML = errorState(err.message, 'trips');
  }
}

async function loadSummary() {
  const kpi = $('#summary-kpi');
  try {
    const summary = await api(`/api/summary?period_days=${state.summaryPeriod}`);

    // Значение и расшифровка на разных строках: в треть ширины телефона
    // моноширинное «488 л / 35 917 ₽» не влезало и рвалось посреди числа.
    const sub = (name, text) => {
      $(`[data-kpi-sub="${name}"]`, kpi).textContent = text;
    };
    $('[data-kpi="revenue"]', kpi).textContent = money(summary.revenue_rub);
    sub('revenue', `${summary.done_trips} рейсов`);

    const marginEl = $('[data-kpi="margin"]', kpi);
    marginEl.textContent = money(summary.margin_rub);
    marginEl.classList.toggle('kpi__value--ok', (summary.margin_rub || 0) > 0);
    marginEl.classList.toggle('kpi__value--danger', (summary.margin_rub || 0) < 0);
    sub('margin', summary.margin_pct === null ? '' : `${summary.margin_pct}% от выручки`);

    if (summary.fuel_overrun_l) {
      $('[data-kpi="fuel"]', kpi).textContent = liters(summary.fuel_overrun_l);
      sub('fuel', money(summary.fuel_overrun_rub));
    } else {
      $('[data-kpi="fuel"]', kpi).textContent = 'в норме';
      sub('fuel', 'топливо');
    }
    kpi.dataset.state = 'ready';

    $('#summary-hint').textContent =
      `Цена литра ${summary.fuel_price_rub_per_l} ₽, ${summary.fuel_price_source}. ` +
      `В работе ${summary.active_trips}, завершено за период ${summary.done_trips}.`;

    const losing = $('#losing-block');
    if (summary.losing_trips_total) {
      const rows = summary.losing_trips
        .map(
          (t) => `
          <button class="row" type="button" data-trip="${esc(t.id)}">
            <span class="row__main">
              <span class="row__title">${esc(t.route)}</span>
              <span class="row__sub">${esc(t.number)}</span>
            </span>
            <span class="row__side"><span class="row__value num" style="color:var(--danger)">${money(
              t.margin_rub,
            )}</span></span>
          </button>`,
        )
        .join('');
      losing.innerHTML = `
        <div class="section-head">
          <h2 class="section-title">Убыточные рейсы</h2>
          <span class="chip chip--danger">${summary.losing_trips_total} за период</span>
        </div>
        <div class="rows">${rows}</div>`;
    } else {
      losing.innerHTML = '';
    }
  } catch (err) {
    $('#summary-hint').textContent = err.message;
    kpi.dataset.state = 'error';
  }
}

async function loadTrips() {
  await Promise.all([loadSummary(), loadTripPage(false)]);
}

async function openTrip(tripId) {
  openSheet('Расчёт рейса', skeleton(5));
  try {
    const data = await api(`/api/trips/${encodeURIComponent(tripId)}`);
    $('#sheet-body').innerHTML = renderCost(data.cost, data.trip);
  } catch (err) {
    $('#sheet-body').innerHTML = errorState(err.message);
  }
}

function calcFormHtml() {
  const vehicles = state.catalog.vehicles
    .map((v) => `<option value="${esc(v.id)}">${esc(v.plate)}, ${esc(v.model)}</option>`)
    .join('');
  const drivers = state.catalog.drivers
    .map((d) => `<option value="${esc(d.id)}">${esc(d.name)}</option>`)
    .join('');
  return `
    <form class="form" id="calc-form">
      <div class="field-row">
        <div class="field">
          <label for="calc-distance">Пробег, км</label>
          <input id="calc-distance" class="num" type="number" min="1" max="20000" value="411" required>
        </div>
        <div class="field">
          <label for="calc-days">Дней в рейсе</label>
          <input id="calc-days" class="num" type="number" min="1" max="60" value="2" required>
        </div>
      </div>
      <div class="field">
        <label for="calc-vehicle">Транспорт</label>
        <select id="calc-vehicle" required>${vehicles}</select>
      </div>
      <div class="field">
        <label for="calc-driver">Водитель</label>
        <select id="calc-driver" required>${drivers}</select>
      </div>
      <div class="field-row">
        <div class="field">
          <label for="calc-federal">Доля федеральных трасс, %</label>
          <input id="calc-federal" class="num" type="number" min="0" max="100" value="90" required>
          <span class="field__help">От неё считается Платон</span>
        </div>
        <div class="field">
          <label for="calc-toll">Платные участки, ₽</label>
          <input id="calc-toll" class="num" type="number" min="0" step="100" value="0">
        </div>
      </div>
      <div class="field-row">
        <div class="field">
          <label for="calc-cargo">Стоимость груза, ₽</label>
          <input id="calc-cargo" class="num" type="number" min="0" step="10000" value="3120000">
        </div>
        <div class="field">
          <label for="calc-revenue">Ставка клиента, ₽</label>
          <input id="calc-revenue" class="num" type="number" min="0" step="100" value="32400">
        </div>
      </div>
      <p class="field__error" id="calc-error" hidden></p>
      <button class="btn btn--primary" type="submit">Посчитать</button>
    </form>
    <div id="calc-result"></div>`;
}

async function submitCalc(event) {
  event.preventDefault();
  const button = $('#calc-form button[type="submit"]');
  const error = $('#calc-error');
  error.hidden = true;
  button.disabled = true;
  button.textContent = 'Считаю';

  const body = {
    distance_km: Number($('#calc-distance').value),
    days: Number($('#calc-days').value),
    vehicle_id: $('#calc-vehicle').value,
    driver_id: $('#calc-driver').value,
    federal_share: Number($('#calc-federal').value) / 100,
    toll_road_rub: Number($('#calc-toll').value || 0),
    cargo_value_rub: Number($('#calc-cargo').value || 0),
    revenue_rub: Number($('#calc-revenue').value || 0) || null,
  };

  try {
    const data = await api('/api/cost/calc', { method: 'POST', body: JSON.stringify(body) });
    $('#calc-result').innerHTML = renderCost(data.cost, null);
    $('#calc-result').scrollIntoView({ behavior: 'smooth', block: 'start' });
  } catch (err) {
    error.textContent = err.message;
    error.hidden = false;
  } finally {
    button.disabled = false;
    button.textContent = 'Посчитать';
  }
}

/* --- экран: маршруты --- */

async function loadRoutes() {
  const form = $('#route-form');
  try {
    const [directions] = await Promise.all([api('/api/routes/directions'), ensureCatalog()]);
    $('#route-direction').innerHTML = directions.directions
      .map(
        (d) =>
          `<option value="${esc(d.origin)}|${esc(d.destination)}">${esc(d.origin)} - ${esc(
            d.destination,
          )}</option>`,
      )
      .join('');
    $('#route-vehicle').innerHTML = state.catalog.vehicles
      .map((v) => `<option value="${esc(v.id)}">${esc(v.plate)}, ${esc(v.model)}</option>`)
      .join('');
    $('#route-driver').innerHTML = state.catalog.drivers
      .map((d) => `<option value="${esc(d.id)}">${esc(d.name)}</option>`)
      .join('');
    form.dataset.state = 'ready';
  } catch (err) {
    $('#route-result').innerHTML = errorState(err.message, 'routes');
  }
}

async function submitRoute(event) {
  event.preventDefault();
  const result = $('#route-result');
  const button = $('#route-form button[type="submit"]');
  const [origin, destination] = $('#route-direction').value.split('|');
  button.disabled = true;
  result.innerHTML = skeleton(4);

  const params = new URLSearchParams({
    origin,
    destination,
    vehicle_id: $('#route-vehicle').value,
    driver_id: $('#route-driver').value,
    cargo_value_rub: $('#route-cargo').value || '0',
  });
  if ($('#route-revenue').value) params.set('revenue_rub', $('#route-revenue').value);

  try {
    const data = await api(`/api/routes/compare?${params.toString()}`);
    result.innerHTML = renderRoutes(data);
  } catch (err) {
    result.innerHTML = errorState(err.message);
  } finally {
    button.disabled = false;
  }
}

function renderRoutes(data) {
  const ex = data.explanation;
  const drivers = ex.drivers.map((line) => `<li>${esc(line)}</li>`).join('');

  const variants = data.variants
    .map((v) => {
      const marks = [];
      if (v.is_cheapest) marks.push('<span class="chip chip--ok">дешевле всех</span>');
      if (v.is_fastest) marks.push('<span class="chip chip--neutral">быстрее всех</span>');
      const delta =
        v.delta_vs_base_rub === 0
          ? 'базовый вариант'
          : `${v.delta_vs_base_rub > 0 ? 'дороже' : 'дешевле'} базового на ${money(
              Math.abs(v.delta_vs_base_rub),
            )}`;
      return `
      <div class="item">
        <div class="item__head">
          <span class="item__title">${esc(v.name)}</span>
          <span class="item__amount num">${money(v.total_rub)}</span>
        </div>
        <div class="row__sub num">${km(v.distance_km)}, ${esc(v.duration_human)}, ${liters(
          v.fuel_l,
        )}${v.toll_rub ? `, платно ${money(v.toll_rub)}` : ''}</div>
        <div class="row__sub">${esc(delta)}${v.note ? `. ${esc(v.note)}` : ''}</div>
        ${marks.length ? `<div class="stages">${marks.join('')}</div>` : ''}
      </div>`;
    })
    .join('');

  return `
    <div class="card card--accent">
      <h3 class="card__title">${esc(ex.headline)}</h3>
      ${drivers ? `<ul class="card__text" style="margin:6px 0 0; padding-left:18px">${drivers}</ul>` : ''}
    </div>
    <div class="breakdown">${variants}</div>
    <p class="hint">Движок маршрутов: ${esc(data.engine)}. Себестоимость каждого варианта считается тем же калькулятором, что и рейсы.</p>`;
}

/* --- экран: ЭДО --- */

function edoDocHtml(doc) {
  return `
    <div class="item">
      <div class="item__head">
        <span class="item__title">${esc(doc.type)} ${esc(doc.number)}</span>
        <span class="chip chip--${esc(doc.status_level)}">${esc(doc.status_label)}</span>
      </div>
      <div class="row__sub">Рейс ${esc(doc.trip_number || 'не указан')}, ${esc(
        doc.trip_route || '',
      )}</div>
      ${doc.error ? `<div class="note note--danger" style="margin-top:8px"><i class="ph ph-x-circle" aria-hidden="true"></i><span>${esc(doc.error)}</span></div>` : ''}
      ${doc.hint ? `<div class="note note--warn" style="margin-top:8px"><i class="ph ph-lightbulb" aria-hidden="true"></i><span><b>${esc(doc.hint.title)}.</b> ${esc(doc.hint.fix)}</span></div>` : ''}
    </div>`;
}

async function loadEdoPage(append) {
  const box = $('#edo-docs');
  const more = $('#edo-more');
  if (!box) return;
  if (!append) {
    state.edo.offset = 0;
    box.innerHTML = skeleton(3);
  }
  try {
    const data = await api(
      `/api/edo?limit=${state.edo.limit}&offset=${state.edo.offset}&only_action=true`,
    );
    state.edo.total = data.documents_total;
    const html = data.documents.map(edoDocHtml).join('');
    if (!data.documents_total) {
      box.innerHTML = emptyState('Документов, требующих действий, нет.');
      more.innerHTML = '';
      return;
    }
    if (append) box.insertAdjacentHTML('beforeend', html);
    else box.innerHTML = html;
    const shown = Math.min(state.edo.offset + data.documents.length, data.documents_total);
    more.innerHTML =
      `<p class="counter">Показано ${shown} из ${data.documents_total}, всего документов ${data.documents_all}</p>` +
      (data.has_more
        ? `<div class="more"><button class="btn" type="button" id="edo-more-btn">Показать ещё</button></div>`
        : '');
  } catch (err) {
    box.innerHTML = errorState(err.message, 'edo');
  }
}

async function loadEdo() {
  const root = $('#edo-content');
  root.innerHTML = skeleton(5);
  try {
    const data = await api(`/api/edo?limit=${state.edo.limit}&offset=0&only_action=true`);
    const checks = data.checklist
      .map(
        (item) => `
        <div class="check">
          <i class="ph ${LEVEL_ICON[item.state]} check__icon check__icon--${esc(item.state)}" aria-hidden="true"></i>
          <span>
            <span class="check__title">${esc(item.title)}</span>
            <span class="check__detail">${esc(item.detail)}</span>
          </span>
        </div>`,
      )
      .join('');

    const c = data.counters;
    const counters = [
      ['ok', 'Принято', c.accepted],
      ['warn', 'Ждут подписи', c.waiting_signature],
      ['warn', 'Черновики', c.draft],
      ['danger', 'Отклонено', c.rejected],
    ]
      .filter(([, , n]) => n)
      .map(([level, label, n]) => `<span class="chip chip--${level}">${label}: ${n}</span>`)
      .join(' ');

    root.innerHTML = `
      <div class="card">
        <div class="readiness">
          <span class="readiness__value num">${data.readiness_pct}%</span>
          <span class="readiness__text">готовность компании к обязательным ЭПД.<br>${esc(
            data.regulation.text,
          )}</span>
        </div>
      </div>
      <div class="section-head"><h2 class="section-title">Чек-лист готовности</h2></div>
      <div class="rows">${checks}</div>
      <div class="section-head">
        <h2 class="section-title">Документы, требующие действий</h2>
        <span class="chip chip--${data.documents_total ? 'danger' : 'ok'}">${
          data.documents_total ? `${data.documents_total} в работе` : 'всё принято'
        }</span>
      </div>
      <div class="stages" style="margin-bottom:10px">${counters}</div>
      <div class="breakdown" id="edo-docs"></div>
      <div id="edo-more"></div>
      <p class="hint">Источник статусов: ${esc(data.provider)}. Создание и подписание документов из приложения не делается: в MVP только чтение.</p>`;
    root.dataset.state = 'ready';
    state.edo.offset = 0;
    await loadEdoPage(false);
  } catch (err) {
    root.innerHTML = errorState(err.message, 'edo');
  }
}

/* --- экран: грузы --- */

async function loadCargo() {
  const root = $('#cargo-content');
  root.innerHTML = skeleton(4);
  try {
    const data = await api('/api/tracking');
    if (!data.shipments.length) {
      root.innerHTML = emptyState('Сейчас в пути ничего нет.');
      return;
    }
    root.innerHTML = data.shipments
      .map((s) => {
        const stages = s.stages
          .map(
            (stage) =>
              `<span class="stage ${stage.done ? 'stage--done' : ''} ${
                stage.current ? 'stage--current' : ''
              }">${esc(stage.name)}</span>`,
          )
          .join('');
        const docs = s.documents
          .map(
            (d) =>
              `<span class="chip chip--${esc(d.status_level)}">${esc(d.type)}: ${esc(
                d.status_label,
              )}</span>`,
          )
          .join(' ');
        return `
        <div class="card">
          <h3 class="card__title">${esc(s.route)}</h3>
          <p class="card__text">${esc(s.number)}, ${esc(s.customer || '')}, ${esc(
            (s.cargo && s.cargo.name) || '',
          )}</p>
          <div class="progress"><div class="progress__fill" style="width:${s.progress_pct}%"></div></div>
          <p class="card__text num">${s.progress_pct}% пути, осталось ${km(s.distance_left_km)}${
            s.eta_text ? `. ${esc(s.eta_text)}` : ''
          }</p>
          <div class="stages">${stages}</div>
          <div class="total" style="margin-top:12px">
            <div>
              <div class="total__value num">${money(s.price_rub)}</div>
              <span class="total__sub">${esc(s.price_note)}</span>
            </div>
            <div style="text-align:right"><span class="total__sub">ТС ${esc(s.vehicle || '')}</span></div>
          </div>
          ${docs ? `<div class="stages" style="margin-top:10px">${docs}</div>` : ''}
        </div>`;
      })
      .join('');
    root.dataset.state = 'ready';
  } catch (err) {
    root.innerHTML = errorState(err.message, 'cargo');
  }
}

/* --- загрузка экранов --- */

async function ensureCatalog() {
  if (!state.catalog) state.catalog = await api('/api/catalog');
  return state.catalog;
}

function loadScreen(name) {
  if (state.loaded[name]) return;
  state.loaded[name] = true;
  if (name === 'trips') loadTrips();
  if (name === 'routes') loadRoutes();
  if (name === 'edo') loadEdo();
  if (name === 'cargo') loadCargo();
}

function retry(name) {
  state.loaded[name] = false;
  loadScreen(name);
}

/* --- старт --- */

async function boot() {
  initTheme();

  $$('.tab').forEach((tab) => tab.addEventListener('click', () => showScreen(tab.dataset.screen)));
  $$('[data-close]').forEach((el) => el.addEventListener('click', closeSheet));
  document.addEventListener('keydown', (e) => {
    if (e.key === 'Escape' && !$('#sheet').hidden) closeSheet();
  });

  document.addEventListener('click', (event) => {
    const row = event.target.closest('[data-trip]');
    if (row) openTrip(row.dataset.trip);

    const retryBtn = event.target.closest('[data-retry]');
    if (retryBtn) retry(retryBtn.dataset.retry);

    const period = event.target.closest('[data-period]');
    if (period) {
      $$('[data-period]').forEach((b) => b.classList.toggle('is-active', b === period));
      state.summaryPeriod = Number(period.dataset.period);
      loadSummary();
    }

    const tripFilter = event.target.closest('[data-trip-filter]');
    if (tripFilter) {
      $$('[data-trip-filter]').forEach((b) => b.classList.toggle('is-active', b === tripFilter));
      state.trips.filter = tripFilter.dataset.tripFilter;
      loadTripPage(false);
    }

    if (event.target.closest('#trips-more-btn')) {
      state.trips.offset += state.trips.limit;
      loadTripPage(true);
    }

    if (event.target.closest('#edo-more-btn')) {
      state.edo.offset += state.edo.limit;
      loadEdoPage(true);
    }

    if (event.target.closest('#tour-done')) {
      markTourSeen();
      closeSheet();
    }

    if (event.target.closest('#open-tour')) {
      showTour();
    }
  });

  // Поиск с задержкой: на годовом объёме запрос на каждый символ избыточен
  let searchTimer = null;
  $('#trips-search').addEventListener('input', (event) => {
    clearTimeout(searchTimer);
    const value = event.target.value;
    searchTimer = setTimeout(() => {
      state.trips.query = value;
      loadTripPage(false);
    }, 280);
  });

  document.addEventListener('submit', (event) => {
    if (event.target.id === 'calc-form') submitCalc(event);
    if (event.target.id === 'route-form') submitRoute(event);
  });

  $('#open-calc').addEventListener('click', async () => {
    openSheet('Свой расчёт', skeleton(4));
    try {
      await ensureCatalog();
      $('#sheet-body').innerHTML = calcFormHtml();
    } catch (err) {
      $('#sheet-body').innerHTML = errorState(err.message);
    }
  });

  try {
    state.meta = await api('/api/meta');
    $('#company-name').textContent = state.meta.company.name || 'компания не определена';
    // Модельные интеграции нельзя выдавать за настоящие, но и кричащая жёлтая
    // плашка в шапке удешевляла приложение. Спокойная строка внизу экрана.
    const note = $('#source-note');
    if (state.meta.data_is_mock) {
      note.textContent = SOURCE_NOTE;
      note.hidden = false;
    }
  } catch (err) {
    $('#company-name').textContent = 'нет связи с сервером';
  }

  loadScreen('trips');

  // Подсказка показывается один раз и после загрузки данных, чтобы человек
  // видел её поверх заполненного экрана, а не поверх скелетонов.
  if (!tourSeen()) setTimeout(showTour, 600);
}

boot();
