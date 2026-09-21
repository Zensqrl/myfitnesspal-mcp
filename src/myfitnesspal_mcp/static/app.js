const el = id => document.getElementById(id);
let browserOpen = false;
let archiveData = null;

function localIsoDate(value = new Date()) {
  const year = value.getFullYear();
  const month = String(value.getMonth() + 1).padStart(2, '0');
  const day = String(value.getDate()).padStart(2, '0');
  return `${year}-${month}-${day}`;
}

function inclusiveCount(start, end) {
  if (!start || !end) return null;
  const first = Date.parse(start + 'T00:00:00Z');
  const last = Date.parse(end + 'T00:00:00Z');
  return Number.isFinite(first) && Number.isFinite(last) && last >= first
    ? Math.round((last - first) / 86400000) + 1 : null;
}

function renderJob(job) {
  const panel = el('job');
  panel.hidden = !job;
  if (!job) return;
  el('job-progress').max = Math.max(job.total || 1, 1);
  el('job-progress').value = job.processed || 0;
  const current = job.current_day ? ` Current date: ${job.current_day}.` : '';
  el('job-detail').textContent =
    `${job.state}: ${job.processed}/${job.total} processed; ` +
    `${job.refreshed} refreshed, ${job.skipped} skipped, ${job.failed} failed.${current}`;
  el('cancel-utility').disabled = !['queued', 'running'].includes(job.state) || job.cancel_requested;
}

async function refresh() {
  try {
    const response = await fetch('/api/status', {cache: 'no-store'});
    if (!response.ok) throw new Error('Cannot read server status.');
    const s = await response.json();
    el('state').textContent = s.reconnect_required ? 'Reconnect required' : s.connected ? 'Connected' : 'Not connected';
    el('detail').textContent = [s.username, s.busy ? `Working: ${s.busy}` : 'Ready'].filter(Boolean).join(' · ');
    el('message').textContent = s.message;
    el('activity').textContent = (s.activity || []).map(item =>
      `${new Date(item.time).toLocaleTimeString()} ${item.message}`
    ).join('\n') || 'No activity since server startup.';
    el('freshness').textContent = s.today?.retrieved_at
      ? `Today last retrieved: ${new Date(s.today.retrieved_at).toLocaleString()}`
      : 'Today has not been archived yet.';
    renderJob(s.job);
    el('login').hidden = !s.browser_open;
    if (s.browser_open !== browserOpen) {
      el('browser').src = s.browser_open
        ? '/browser/vnc.html?autoconnect=1&resize=scale&path=browser/websockify'
        : 'about:blank';
      browserOpen = s.browser_open;
    }
    const working = Boolean(s.busy || s.queued);
    for (const button of document.querySelectorAll('.mutating,.utility-mutating')) {
      button.disabled = working;
    }
  } catch (error) {
    el('message').textContent = error.message;
  }
}

async function action(name, data = {}) {
  el('message').textContent = 'Sending request...';
  try {
    const response = await fetch(`/api/${name}`, {
      method: 'POST',
      headers: {'Content-Type': 'application/json', 'X-MFP-Request': '1'},
      body: JSON.stringify(data)
    });
    const body = await response.json();
    if (!response.ok) throw new Error(body.error || 'Request failed.');
    await refresh();
    return true;
  } catch (error) {
    el('message').textContent = error.message;
    return false;
  }
}

function confirmRange(kind, start, end, force) {
  const days = inclusiveCount(start, end);
  if (days === null) {
    el('message').textContent = 'Choose a valid date range.';
    return false;
  }
  if (force || days > 365) {
    return confirm(
      `${kind} ${days} inclusive dates from ${start} through ${end}` +
      (force ? ', forcing every date' : '') + '?'
    );
  }
  return true;
}

function textElement(tag, text, className) {
  const node = document.createElement(tag);
  node.textContent = text;
  if (className) node.className = className;
  return node;
}

function displayValue(value, unit = '') {
  return typeof value === 'number' ? `${value.toLocaleString()} ${unit}`.trim() : 'Missing';
}

function renderRich(payload) {
  const root = el('archive-rich');
  root.replaceChildren();
  if (!payload.found) {
    root.append(textElement('p', `No archived database record exists for ${payload.day}.`, 'empty'));
    return;
  }
  root.append(textElement('h3', payload.day));
  const sync = payload.sync || {};
  root.append(textElement(
    'p',
    `Archive status: ${sync.complete ? 'complete' : 'incomplete'}` +
      (sync.last_success_at ? ` · last successful retrieval ${new Date(sync.last_success_at).toLocaleString()}` : '')
  ));
  const data = payload.data || {};
  const nutrition = data.nutrition || {};
  const totals = nutrition.nutrients || {};
  const goals = nutrition.goals || {};
  root.append(textElement('h4', 'Nutrition'));
  const nutritionList = document.createElement('ul');
  for (const [label, key, unit] of [
    ['Calories', 'calories', 'kcal'], ['Protein', 'protein', 'g'],
    ['Carbohydrates', 'carbohydrates', 'g'], ['Fat', 'fat', 'g']
  ]) {
    const total = totals[key] ?? (key === 'carbohydrates' ? totals.carbs : null);
    const target = goals[key] ?? (key === 'carbohydrates' ? goals.carbs : null);
    nutritionList.append(textElement('li',
      `${label}: ${displayValue(total, unit)} · target ${displayValue(target, unit)}`));
  }
  root.append(nutritionList);

  root.append(textElement('h4', 'Diary'));
  const diary = Array.isArray(data.diary) ? data.diary : [];
  if (!diary.length) {
    root.append(textElement('p', 'No food entries archived.', 'empty'));
  } else {
    const meals = new Map();
    for (const entry of diary) {
      const meal = entry.meal || 'Other';
      if (!meals.has(meal)) meals.set(meal, []);
      meals.get(meal).push(entry);
    }
    for (const [meal, entries] of meals) {
      root.append(textElement('h5', meal));
      const list = document.createElement('ul');
      for (const entry of entries) {
        const details = [
          entry.quantity == null ? null : `quantity ${entry.quantity}`,
          entry.serving_description || null,
          typeof entry.calories === 'number' ? `${entry.calories} kcal` : null,
          typeof entry.protein === 'number' ? `${entry.protein} g protein` : null,
          typeof entry.carbs === 'number' ? `${entry.carbs} g carbs` : null,
          typeof entry.fat === 'number' ? `${entry.fat} g fat` : null
        ].filter(Boolean);
        list.append(textElement('li', `${entry.name || 'Unnamed entry'}${details.length ? ' — ' + details.join(', ') : ''}`));
      }
      root.append(list);
    }
  }

  root.append(textElement('h4', 'Notes'));
  root.append(textElement('p', data.note || 'No diary note archived.', data.note ? '' : 'empty'));
  root.append(textElement('h4', 'Local feel'));
  root.append(textElement('p', data.feel || 'No local feel entry archived.', data.feel ? '' : 'empty'));
}

function setArchiveView(raw) {
  el('archive-raw').hidden = !raw;
  el('archive-rich').hidden = raw;
  el('show-raw').setAttribute('aria-pressed', String(raw));
  el('show-rich').setAttribute('aria-pressed', String(!raw));
}

async function loadArchive(day) {
  el('message').textContent = 'Reading local archive...';
  try {
    const response = await fetch(`/api/archive/day?day=${encodeURIComponent(day)}`, {cache: 'no-store'});
    const body = await response.json();
    if (!response.ok) throw new Error(body.error || 'Archive read failed.');
    archiveData = body;
    el('archive-result').hidden = false;
    el('archive-raw').textContent = JSON.stringify(body, null, 2);
    renderRich(body);
    setArchiveView(false);
    el('message').textContent = body.found ? `Loaded local archive for ${day}.` : `No archive exists for ${day}.`;
  } catch (error) {
    el('message').textContent = error.message;
  }
}

const today = localIsoDate();
for (const input of document.querySelectorAll('input[type="date"]')) input.max = today;
el('range-end').value = today;
el('archive-date').value = today;

for (const name of ['start', 'cancel', 'sync']) el(name).onclick = () => action(name);
el('finish').onclick = () => action('finish', {username: el('username').value.trim()});
el('paste').onclick = () => {
  const cookie = el('cookie').value;
  el('cookie').value = '';
  action('paste', {cookie, username: el('username').value.trim()});
};
el('disconnect').onclick = () => {
  if (confirm('Disconnect MyFitnessPal? Your archived data will be retained.')) {
    action('disconnect', {confirm: true});
  }
};
el('cancel-utility').onclick = () => action('cancel-utility');

el('backfill-form').onsubmit = event => {
  event.preventDefault();
  const start = el('backfill-start').value;
  const force = el('backfill-force').checked;
  if (confirmRange('Backfill', start, today, force)) action('backfill', {start, force});
};
el('range-form').onsubmit = event => {
  event.preventDefault();
  const start = el('range-start').value;
  const end = el('range-end').value;
  const force = el('range-force').checked;
  if (confirmRange('Synchronize', start, end, force)) action('sync-range', {start, end, force});
};
function updateRangeCount() {
  const count = inclusiveCount(el('range-start').value, el('range-end').value);
  el('range-count').textContent = count === null ? '' : `${count} inclusive date${count === 1 ? '' : 's'}`;
}
el('range-start').onchange = updateRangeCount;
el('range-end').onchange = updateRangeCount;
el('archive-form').onsubmit = event => {
  event.preventDefault();
  loadArchive(el('archive-date').value);
};
el('show-rich').onclick = () => setArchiveView(false);
el('show-raw').onclick = () => setArchiveView(true);
el('copy-json').onclick = async () => {
  if (!archiveData) return;
  try {
    await navigator.clipboard.writeText(JSON.stringify(archiveData, null, 2));
    el('message').textContent = 'Archive JSON copied.';
  } catch (_error) {
    el('message').textContent = 'Copy failed; select the Raw JSON text manually.';
  }
};

refresh();
setInterval(refresh, 2500);
