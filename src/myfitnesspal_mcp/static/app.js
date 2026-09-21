const el = id => document.getElementById(id);
let browserOpen = false;
async function refresh() {
  try {
    const response = await fetch('/api/status', {cache: 'no-store'});
    if (!response.ok) throw new Error('Cannot read server status.');
    const s = await response.json();
    el('state').textContent = s.reconnect_required ? 'Reconnect required' : s.connected ? 'Connected' : 'Not connected';
    el('detail').textContent = [s.username, s.busy ? `Working: ${s.busy}` : 'Ready'].filter(Boolean).join(' · ');
    el('message').textContent = s.message;
    el('activity').textContent = (s.activity || []).map(item => `${new Date(item.time).toLocaleTimeString()} ${item.message}`).join('\n') || 'No activity since server startup.';
    el('freshness').textContent = s.today?.retrieved_at ? `Today last retrieved: ${new Date(s.today.retrieved_at).toLocaleString()}` : 'Today has not been archived yet.';
    el('login').hidden = !s.browser_open;
    if (s.browser_open !== browserOpen) {
      el('browser').src = s.browser_open ? '/browser/vnc.html?autoconnect=1&resize=scale&path=browser/websockify' : 'about:blank';
      browserOpen = s.browser_open;
    }
    for (const button of document.querySelectorAll('button')) button.disabled = Boolean(s.busy || s.queued);
  } catch (e) { el('message').textContent = e.message; }
}
async function action(name, data = {}) {
  el('message').textContent = 'Sending request...';
  try {
    const response = await fetch(`/api/${name}`, {
      method: 'POST', headers: {'Content-Type': 'application/json', 'X-MFP-Request': '1'}, body: JSON.stringify(data)
    });
    if (!response.ok) {
      const body = await response.json();
      throw new Error(body.error || 'Request failed.');
    }
    await refresh();
  } catch (e) { el('message').textContent = e.message; }
}
for (const name of ['start', 'cancel', 'sync']) el(name).onclick = () => action(name);
el('finish').onclick = () => action('finish', {username: el('username').value.trim()});
el('paste').onclick = () => {
  const cookie = el('cookie').value;
  el('cookie').value = '';
  action('paste', {cookie, username: el('username').value.trim()});
};
el('disconnect').onclick = () => {
  if (confirm('Disconnect MyFitnessPal? Your archived data will be retained.')) action('disconnect', {confirm: true});
};
refresh();
setInterval(refresh, 2500);
