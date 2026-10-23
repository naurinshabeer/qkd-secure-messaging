const $ = (id) => document.getElementById(id);
let toastTimer;
let linkIsUp = true;

async function request(path, body) {
  const response = await fetch(path, body === undefined ? {} : {
    method: 'POST', headers: {'Content-Type':'application/json'}, body: JSON.stringify(body)
  });
  const data = await response.json();
  if (!response.ok) throw new Error(data.error || `Request failed (${response.status})`);
  return data;
}

function notify(message, isError = false) {
  const toast = $('toast');
  toast.textContent = message;
  toast.className = `toast show${isError ? ' error' : ''}`;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => toast.className = 'toast', 3800);
}
function showError(error) { notify(error.message || 'Something went wrong.', true); }
function pct(value) { return `${(value * 100).toFixed(2)}%`; }
function timeLabel(timestamp) { return new Date(timestamp * 1000).toLocaleTimeString([], {hour:'2-digit', minute:'2-digit', second:'2-digit'}); }
function escapeHtml(value) { return String(value).replace(/[&<>"']/g, ch => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[ch])); }

function render(state) {
  const up = state.link_up;
  linkIsUp = up;
  $('header-status').textContent = up ? 'Alice + Bob online' : 'Link or node unavailable';
  $('header-dot').style.background = up ? 'var(--mint-strong)' : 'var(--red)';
  $('link-tag').classList.toggle('down', !up);
  $('link-tag').querySelector('span').textContent = up ? 'LINK READY' : 'LINK FAILED';
  $('network-visual').classList.toggle('down', !up);
  $('link-detail').textContent = up ? 'SeQUeNCe · BB84 + Cascade' : 'No key exchange while link is down';
  $('distance-label').textContent = `${state.settings.distance_km} km`;

  const last = state.last_run;
  if (last) {
    const high = last.qber >= state.qber_limit;
    $('run-id').textContent = `#${last.seed}`;
    $('run-state').textContent = last.accepted ? 'Key batch accepted' : 'Key batch rejected';
    $('qber-value').textContent = pct(last.qber);
    $('qber-value').classList.toggle('bad', high);
    $('qber-value').classList.toggle('good', !high);
    $('qber-inline').textContent = `QBER ${pct(last.qber)}`;
    $('qber-note').textContent = `${last.qber_samples} BB84 error sample${last.qber_samples === 1 ? '' : 's'} · ${high ? 'above cutoff' : 'below cutoff'}`;
    $('detected-value').textContent = last.matching_cascade_keys.toLocaleString();
    $('signals-note').textContent = `matching Cascade keys · ${last.signals_intercepted.toLocaleString()} intercepted`;
    $('acceptance-value').textContent = last.accepted ? 'ACCEPT' : 'REJECT';
    $('acceptance-value').classList.toggle('bad', !last.accepted);
    $('acceptance-value').classList.toggle('good', last.accepted);
    $('acceptance-note').textContent = last.accepted ? `${last.pool_added} added to pool` : 'No key material imported';
    const throughput = last.cascade_privacy_throughput_bps;
    $('rate-value').textContent = throughput == null ? '—' : `${(throughput / 1000).toFixed(2)} kb/s`;
  } else {
    $('run-id').textContent = 'IDLE'; $('run-state').textContent = 'Awaiting experiment';
    $('qber-value').textContent = '—'; $('qber-inline').textContent = 'QBER —';
    $('qber-note').textContent = 'No experiment data';
    $('detected-value').textContent = '—'; $('signals-note').textContent = 'Available after run';
    $('acceptance-value').textContent = '—'; $('acceptance-note').textContent = 'Cutoff: 11% teaching rule';
    $('rate-value').textContent = '—';
  }

  const count = state.pool_count;
  $('pool-number').textContent = count;
  $('pool-count').textContent = `A ${String(count).padStart(2,'0')}/${state.pool_limit} · B ${String(state.bob_pool_count || 0).padStart(2,'0')}/${state.pool_limit}`;
  $('bob-pool-count').textContent = `Bob: ${state.bob_pool_count || 0} ready`;
  $('pool-fill').style.width = `${count / state.pool_limit * 100}%`;
  $('pool-caption').textContent = count
    ? `${count} SeQUeNCe key${count === 1 ? '' : 's'} ready for one-time message use.`
    : 'Run a SeQUeNCe experiment to populate the pool.';
  $('key-list-count').textContent = String(state.key_ids.length).padStart(2, '0');
  $('key-list').innerHTML = state.key_ids.length
    ? state.key_ids.map(id => `<span class="key-id">${escapeHtml(id)}</span>`).join('')
    : '<span class="empty-keys">No keys in memory</span>';
  $('generate-keys').disabled = !up || count >= state.pool_limit;
  $('send-message').disabled = !up || !count || !$('message').value.trim();
  $('fail-link').hidden = !up;
  $('restore-link').hidden = up;
  $('event-list').innerHTML = state.events.map(event => `
    <div class="event ${event.level}"><span class="event-time">${timeLabel(event.time)}</span><span class="event-message"><i class="event-marker"></i>${escapeHtml(event.message)}</span><span class="event-result">${event.level.toUpperCase()}</span></div>
  `).join('');
}

async function refresh() { render(await request('/api/state')); }

$('distance').addEventListener('input', e => $('distance-output').textContent = `${e.target.value} km`);
$('noise').addEventListener('input', e => $('noise-output').textContent = `${e.target.value}%`);
$('eve').addEventListener('input', e => $('eve-output').textContent = `${e.target.value}%`);
$('message').addEventListener('input', e => {
  $('char-count').textContent = `${e.target.value.length.toLocaleString()} / 2,000`;
  $('send-message').disabled = !$('message').value.trim() || !Number($('pool-number').textContent) || !linkIsUp;
});

$('apply-settings').addEventListener('click', async () => {
  try {
    await request('/api/settings', {
      distance_km: Number($('distance').value),
      channel_noise: Number($('noise').value) / 100,
      eve_fraction: Number($('eve').value) / 100
    });
    await refresh();
    notify('Settings saved for the next SeQUeNCe run.');
  } catch (error) { showError(error); }
});

$('generate-keys').addEventListener('click', async () => {
  const button = $('generate-keys');
  button.disabled = true;
  $('generate-label').textContent = 'SeQUeNCe is running…';
  $('run-state').textContent = 'BB84 + Cascade in progress';
  try {
    const result = await request('/api/keys/generate', {count:24 - Number($('pool-number').textContent)});
    render(result.state);
    notify(`${result.generated} key${result.generated === 1 ? '' : 's'} imported from SeQUeNCe.`);
  } catch (error) { showError(error); await refresh(); }
  finally { $('generate-label').textContent = 'Run SeQUeNCe experiment'; await refresh(); }
});

$('send-message').addEventListener('click', async () => {
  $('send-message').disabled = true;
  try {
    const result = await request('/api/messages', {text:$('message').value});
    $('delivery-result').innerHTML = `<div class="receipt"><div class="side-label"><span class="side-avatar bob">B</span><div><b>Bob</b><small>MESSAGE VERIFIED</small></div></div><div class="receipt-head"><span>AUTHENTICATED · ${escapeHtml(result.message_id)}</span></div><p>${escapeHtml(result.bob_text)}</p><div class="receipt-meta"><span>KEY</span><span>${escapeHtml(result.key_id)} · consumed</span><span>CIPHER</span><span>AES-256-GCM · ${result.ciphertext_bytes} bytes</span></div><p class="ciphertext">${escapeHtml(result.ciphertext)}</p></div>`;
    $('message').value = '';
    $('char-count').textContent = '0 / 2,000';
    await refresh();
    notify('Message authenticated and delivered. One key consumed.');
  } catch (error) { showError(error); await refresh(); }
});

$('fail-link').addEventListener('click', async () => { try { render(await request('/api/link/fail', {})); notify('Link marked unavailable.'); } catch (e) { showError(e); } });
$('restore-link').addEventListener('click', async () => { try { render(await request('/api/link/restore', {})); notify('Link restored.'); } catch (e) { showError(e); } });
$('reset-demo').addEventListener('click', async () => {
  try {
    render(await request('/api/reset', {}));
    $('message').value = ''; $('char-count').textContent = '0 / 2,000';
    $('delivery-result').innerHTML = '<div class="side-label"><span class="side-avatar bob">B</span><div><b>Bob</b><small>DESTINATION</small></div></div><div class="received-empty"><span class="empty-mark">···</span><p>Decrypted message will appear here after delivery.</p></div>';
    notify('Session reset.');
  } catch (e) { showError(e); }
});
$('method-toggle').addEventListener('click', e => {
  const note = $('method-note'), open = note.hidden;
  note.hidden = !open;
  e.currentTarget.setAttribute('aria-expanded', String(open));
  e.currentTarget.innerHTML = `Model notes <span>${open ? '−' : '＋'}</span>`;
});

refresh().catch(showError);
