// v0.1.1: same-origin, authenticated HA -> MCP integration -> local archive.
// No credentials, diary entries, or nutrition snapshots are persisted.
export function unpack(value) {
  for (let i = 0; i < 8; i++) {
    if (!value || typeof value !== 'object') throw new Error('Invalid MCP response');
    if (value.error || value.isError) throw new Error('MCP tool reported an error');
    if (value.jsonrpc) { value = value.result; continue; }
    if (value.structuredContent) { value = value.structuredContent; continue; }
    if (Array.isArray(value.content)) {
      const text = value.content.find(item => item.type === 'text')?.text;
      if (!text) throw new Error('MCP response contains no data');
      value = JSON.parse(text); continue;
    }
    return value;
  }
  throw new Error('Unexpected MCP nesting');
}

export function localDay(zone, now = new Date()) {
  const parts = new Intl.DateTimeFormat('en-US', {
    timeZone: zone, year: 'numeric', month: '2-digit', day: '2-digit'
  }).formatToParts(now);
  const part = name => parts.find(p => p.type === name).value;
  return `${part('year')}-${part('month')}-${part('day')}`;
}

const finite = value => typeof value === 'number' && Number.isFinite(value) && value >= 0;
export function summarize(day, expectedDay, now = Date.now()) {
  const n = day.data?.nutrition || {};
  const totals = n.nutrients || {};
  const goals = n.goals || {};
  const definitions = [
    ['Calories', 'calories', 'calories', 'kcal'],
    ['Protein', 'protein', 'protein', 'g'],
    ['Carbohydrates', 'carbohydrates', 'carbs', 'g'],
    ['Fat', 'fat', 'fat', 'g']
  ];
  const rows = definitions.map(([label, key, scalar, unit]) => {
    const total = totals[key] ?? totals[scalar] ?? n[scalar];
    const target = goals[key] ?? goals[scalar] ?? (key === 'calories' ? n.goal_calories : null);
    return {label, unit, total: finite(total) ? total : null,
      target: finite(target) ? target : null,
      remaining: finite(total) && finite(target) ? target - total : null};
  });
  const age = (now - Date.parse(day.retrieved_at)) / 1000;
  const date = day.data?.day ?? day.day ?? null;
  const complete = rows.every(row => row.total !== null && row.target !== null);
  const fresh = day.status === 'cached' && day.stale === false && date === expectedDay
    && Number.isFinite(age) && age >= 0 && age <= 900;
  return {rows, date, complete, fresh, age, passed: complete && fresh};
}

export class MfpAcceptanceCard extends (globalThis.HTMLElement || class {}) {
  constructor() { super(); this.attachShadow({mode:'open'}); }
  setConfig(config) {
    if (!/^mcp-[A-Za-z0-9]+$/.test(config.api_id || '')) throw new Error('MCP API ID required');
    this.config = config; this.render();
  }
  set hass(hass) {
    this._hass = hass;
    if (!this.started && this.isConnected && this.config) {
      this.started = true; this.refresh();
    }
  }
  connectedCallback() {
    this.timer = setInterval(() => { if (!document.hidden) this.refresh(); }, 60000);
    if (this._hass && this.config) { this.started = true; this.refresh(); }
  }
  disconnectedCallback() { clearInterval(this.timer); this.started = false; }
  getCardSize() { return 9; }
  async rpc(method, params) {
    const response = await this._hass.callApi('POST', `mcp/${this.config.api_id}`, {
      jsonrpc:'2.0', id:Date.now(), method, params
    }, {Accept:'application/json'});
    return unpack(response);
  }
  async refresh() {
    if (!this._hass || !this.config || this.busy) return;
    this.busy = true; this.error = null; this.render();
    try {
      if (!this._hass.user?.is_admin) throw new Error('Sign in with an HA administrator account');
      const zone = this._hass.config.time_zone;
      const requested = localDay(zone);
      const tools = await this.rpc('tools/list', {});
      if (!tools.tools?.some(t => t.name === 'fitness_get_day')) throw new Error('Archive tool not found');
      const data = await this.rpc('tools/call', {name:'fitness_get_day', arguments:{day:requested}});
      // Discard food entries/notes immediately: this card needs aggregates only.
      this.data = {status:data.status, stale:data.stale, retrieved_at:data.retrieved_at,
        refresh_queued:data.refresh_queued, day:data.day,
        data:{day:data.data?.day, nutrition:data.data?.nutrition}};
      this.checked = new Date(); this.zone = zone;
    } catch (error) {
      this.data = null;
      const code = error?.status_code;
      this.error = error?.message === 'Sign in with an HA administrator account'
        ? error.message : `Read failed${Number.isInteger(code) ? ` (HTTP ${code})` : ''}. Check the MCP integration, Ubuntu connection, and administrator access.`;
    } finally { this.busy = false; if (this.isConnected) this.render(); }
  }
  render() {
    if (!this.shadowRoot) return;
    this.shadowRoot.innerHTML = `<style>
      :host{display:block}ha-card{padding:20px;color:var(--primary-text-color)}
      h2{font-size:1.2rem;margin:0 0 12px}p{line-height:1.5;margin:10px 0}
      .status{padding:12px;border:1px solid var(--divider-color);border-radius:12px;font-weight:600}
      table{width:100%;border-collapse:collapse;font-size:.95rem;margin:16px 0}
      th,td{padding:10px 5px;text-align:right;border-bottom:1px solid var(--divider-color)}
      th:first-child,td:first-child{text-align:left}small{color:var(--secondary-text-color)}
      button{font:inherit;min-height:44px;padding:8px 16px;border-radius:8px;
        background:var(--primary-color);color:var(--text-primary-color);border:0;cursor:pointer}
      @media(max-width:420px){ha-card{padding:12px}table{font-size:.82rem}th,td{padding:10px 3px}}
    </style><ha-card><h2>Nutrition · live read test (v0.1.1)</h2>
      <p class="status" role="status"></p><div class="details"></div>
      <button>Read latest archive</button>
      <p><small>Dashboard → Home Assistant → MyFitnessPal MCP → SQLite archive.<br>
      No AI call or native sensors. Missing values are never treated as zero.</small></p></ha-card>`;
    const status = this.shadowRoot.querySelector('.status');
    const details = this.shadowRoot.querySelector('.details');
    const button = this.shadowRoot.querySelector('button');
    button.disabled = this.busy; button.onclick = () => this.refresh();
    if (this.error) { status.textContent = this.error; return; }
    if (!this.data) { status.textContent = this.busy ? 'Reading through Home Assistant…' : 'Waiting for HA…'; return; }
    const summary = summarize(this.data, localDay(this.zone));
    status.textContent = this.busy ? 'Refreshing…' : summary.passed
      ? 'PASS · live HA read, required fields present and fresh'
      : 'INCOMPLETE · missing fields, missing cache, or stale data';
    const paragraph = text => { const p=document.createElement('p'); p.textContent=text; details.append(p); };
    paragraph(`Diary date: ${summary.date || 'Missing'} · ${this.zone}`);
    paragraph(`Source retrieved: ${this.data.retrieved_at ? new Date(this.data.retrieved_at).toLocaleString(undefined, {timeZone:this.zone}) : 'Missing'} · ${summary.fresh ? 'Fresh (≤15 minutes)' : 'Not fresh / unknown'}`);
    const table = document.createElement('table');
    const head = document.createElement('tr');
    for (const label of ['Nutrient / unit','Total','Target','Remaining']) {
      const th=document.createElement('th'); th.scope='col'; th.textContent=label; head.append(th);
    }
    const thead=document.createElement('thead'); thead.append(head); table.append(thead);
    const body=document.createElement('tbody');
    for (const row of summary.rows) {
      const tr=document.createElement('tr');
      for (const value of [`${row.label} (${row.unit})`,row.total,row.target,row.remaining]) {
        const td=document.createElement('td');
        td.textContent = value === null ? 'Missing' : typeof value === 'number'
          ? value.toLocaleString(undefined,{maximumFractionDigits:1}) : value;
        tr.append(td);
      }
      body.append(tr);
    }
    table.append(body); details.append(table);
    paragraph('Units follow the archive contract: calories in kcal; macros in grams. Remaining = target − total (negative means over target).');
    paragraph(`Last HA read: ${this.checked.toLocaleString(undefined,{timeZone:this.zone})}. Refresh queued: ${this.data.refresh_queued ? 'Yes — read again shortly' : 'No'}.`);
  }
}
if (globalThis.customElements && !customElements.get('mfp-acceptance-card')) {
  customElements.define('mfp-acceptance-card', MfpAcceptanceCard);
}
