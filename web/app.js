const $ = id => document.getElementById(id);
const QBER_LIMIT = 0.11;
const GENERIC_ACTION_ERROR = 'The action did not finish. Check that the local coordinator and node processes are running, then refresh.';
const PRESETS = {
  normal: {distance: 10, noise: 1, eve: 0, label: 'Normal'},
  eve: {distance: 10, noise: 1, eve: 75, label: 'Eavesdropper present'},
  distance: {distance: 50, noise: 1, eve: 0, label: 'Long distance'},
  noise: {distance: 10, noise: 11, eve: 0, label: 'Noisy channel'}
};
let currentState = null;
let currentInboxUrl = '';
let inboxMessages = [];
let lastDelivery = null;
let statusOverride = '';
let requestBusy = false;
let selectedLinkId = '';
let unexpectedError = '';

async function request(path, body) {
  let response;
  try {
    response = await fetch(path, body === undefined ? {} : {
      method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(body)
    });
  } catch (_) {
    throw new Error('The coordinator is not responding.');
  }
  let data;
  try { data = await response.json(); } catch (_) { data = {}; }
  if (!response.ok) throw new Error(data.error || `Request failed (${response.status})`);
  return data;
}

function escapeHtml(value) {
  return String(value).replace(/[&<>"']/g, ch => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[ch]));
}
function percent(value, digits = 1) { return `${(Number(value) * 100).toFixed(digits)}%`; }
function timeLabel(timestamp) {
  return new Date(timestamp * 1000).toLocaleTimeString([], {hour: '2-digit', minute: '2-digit'});
}
function displayName(id) {
  const value = String(id);
  if (value.toLowerCase() === 'alice') return 'Alice';
  if (value.toLowerCase() === 'bob') return 'Bob';
  if (/^r\d+$/i.test(value)) return `Relay ${Number(value.slice(1))}`;
  return value.replace(/[-_]+/g, ' ').replace(/\b\w/g, char => char.toUpperCase());
}
function say(message, kind = 'info') {
  $('status-text').textContent = message;
  $('status-banner').className = `status-banner ${kind}`;
  $('status-symbol').textContent = kind === 'error' ? '!' : kind === 'success' ? '✓' : 'i';
}

function helpfulError(error) {
  const raw = String(error?.message || error || '').replace(/^HTTP \d+:\s*/i, '');
  const text = raw.toLowerCase();
  if (text.includes('coordinator is not responding')) return 'The coordinator is not responding. Start the coordinator process, then choose “Refresh status”.';
  if (text.includes('both alice-to-bob relay paths') || text.includes('no available alice-to-bob relay path')) return 'There is no complete route with keys available. Restore a link or create keys on the open links.';
  if (text.includes('both key pools are full') || text.includes('no shared capacity')) return 'The key pools are full. Send a message to use one key, then create more.';
  if (text.includes('no available key') || text.includes('pool is empty') || text.includes('key pool is empty')) return 'There are no keys available on this link. Create more keys, or use another open route.';
  if (text.includes('unknown peer_id') || text.includes('unknown key') || text.includes('key not found')) return 'The requested node or key could not be found. Refresh the network state, then create a new key if needed.';
  if (text.includes('already reserved') || text.includes('already consumed') || text.includes('reservation')) return 'That one-time key is already in use or has been used. Refresh the page and try with an available key.';
  if (text.includes('link is down') || text.includes('link down') || text.includes('link is unavailable')) return 'This link is down. Restore it or send the message through another open route.';
  if (text.includes('rejected the message')) return 'Bob could not verify the message. The reserved key was released; try sending again.';
  if (text.includes('outcome is unknown')) return 'Delivery could not be confirmed. The key is held safely while the nodes reconcile; refresh status before trying again.';
  if (text.includes('unknown topology link')) return 'That link is not in the loaded network map. Refresh the dashboard to reload its links.';
  if (text.includes('distance must')) return 'Distance must be between 1 and 50 km. Adjust the distance slider and save again.';
  if (text.includes('channel noise must')) return 'Channel noise must be between 0% and 15%. Adjust the noise slider and save again.';
  if (text.includes('eve interception must')) return 'Eavesdropper interception must be between 0% and 100%. Adjust the slider and save again.';
  if (text.includes('settings must be numeric') || text.includes('invalid types or values')) return 'One of the settings is not a valid number. Check the sliders and try again.';
  if (text.includes('message must contain')) return 'Enter a message between 1 and 2,000 characters.';
  if (text.includes('import count must')) return 'The requested key count is outside the allowed range. Refresh the page and try again.';
  if (text.includes('valid provisioning token')) return 'The local nodes rejected the coordinator token. Set the same QKD_PROVISION_TOKEN for every process, then restart them.';
  if (text.includes('request failed (404)') || text.includes('endpoint not found')) return 'The requested action is not available from this server version. Restart all project processes, then refresh.';
  if (text.includes('request failed (400)') || text.includes('invalid')) return 'The request was not accepted. Check the message and settings, then try again.';
  for (const role of ['alice', 'bob', 'r1', 'r2', 'relay']) {
    if (text.includes(`${role} node unavailable`) || text.includes(`${role} node is unreachable`)) {
      const name = role === 'r1' ? 'Relay 1' : role === 'r2' ? 'Relay 2' : role === 'relay' ? 'A relay' : role[0].toUpperCase() + role.slice(1);
      return `${name}${name.endsWith('y') ? ' is' : "'s node is"} not responding. Start ${name === 'A relay' ? 'the relay' : name}'s process, then refresh.`;
    }
  }
  if (text.includes('timed out') || text.includes('unreachable') || text.includes('connection refused')) return 'A node did not answer in time. Check that Alice, both relays, and Bob are running, then refresh.';
  if (text.includes('request failed (503)') || text.includes('unavailable')) return 'A network service is unavailable. Start the coordinator, Alice, both relays, and Bob, then refresh.';
  return 'The action did not finish. Check that the local coordinator and node processes are running, then refresh.';
}

function routeAvailable(state, path) {
  return path.slice(0, -1).every((node, index) => {
    const next = path[index + 1];
    const link = state.links.find(edge => new Set([edge.a, edge.b]).has(node) && new Set([edge.a, edge.b]).has(next));
    return Boolean(link?.up && link.pool_count > 0);
  });
}
function pathDistance(state, path) {
  return path.slice(0, -1).reduce((total, node, index) => {
    const next = path[index + 1];
    const link = state.links.find(edge => new Set([edge.a, edge.b]).has(node) && new Set([edge.a, edge.b]).has(next));
    return total + Number(link?.distance_km || 0);
  }, 0);
}
function stateMessage(state) {
  if (stateOverride()) return statusOverride;
  if (!state.topology) {
    if (!state.alice_online) return "Alice's node is not responding. Start Alice's process, then refresh.";
    if (!state.bob_online) return "Bob's node is not responding. Start Bob's process, then refresh.";
    if (!state.link_up) return 'The Alice–Bob link is down. Restore the link, then create keys or send again.';
    if (!state.pool_count) return 'Alice and Bob are connected, but have no shared keys yet. Create keys to begin.';
    return `${state.pool_count} matching key${state.pool_count === 1 ? '' : 's'} ready. Write a message to Bob, or create more keys.`;
  }
  const openPaths = (state.paths || []).filter(path => path.slice(0, -1).every((node, index) => {
    const next = path[index + 1];
    return state.links.some(link => link.up && new Set([link.a, link.b]).has(node) && new Set([link.a, link.b]).has(next));
  }));
  if (!openPaths.length) return 'Every route to Bob has a failed link. Restore a link before creating keys or sending a message.';
  if (lastDelivery) return `Message delivered through ${(lastDelivery.path?.length ? lastDelivery.path : ['alice', 'bob']).map(displayName).join(' → ')}. Bob can read it below; one end-to-end message key was used.`;
  const usablePaths = openPaths.filter(path => routeAvailable(state, path));
  if (usablePaths.length && state.links.some(link => !link.up)) {
    const route = usablePaths.sort((left, right) => pathDistance(state, left) - pathDistance(state, right))[0];
    const failed = state.links.find(link => !link.up);
    return `Link ${displayName(failed.a)}–${displayName(failed.b)} is down. Messages can use ${route.map(displayName).join(' → ')}.`;
  }
  if (!state.links.some(link => link.pool_count > 0 && link.up)) return 'The network is connected, but no link has keys yet. Create keys to begin.';
  if (!usablePaths.length) {
    const openLink = state.links.find(link => link.up && link.pool_count === 0);
    return openLink
      ? `No keys left on ${displayName(openLink.a)}–${displayName(openLink.b)}. Create keys on the open links.`
      : 'No complete route has keys on every link. Create keys on the open links or restore a failed link.';
  }
  return 'The network is ready. Create keys, then write a message to send to Bob.';
}
function stateOverride() { return Boolean(statusOverride); }

function render(state) {
  currentState = state;
  currentInboxUrl = state.bob_inbox_url || '';
  if (!statusOverride) say(stateMessage(state));
  renderNetwork(state);
  renderResult(state);
  renderAdvanced(state);
  renderEvents(state.events || []);
  updateActions(state);
  if (currentInboxUrl) loadInbox(currentInboxUrl);
}

function renderNetwork(state) {
  const links = state.topology ? state.links : [{
    id: 'direct', a: 'alice', b: 'bob', distance_km: state.settings.distance_km,
    up: state.link_up, a_pool: state.pool_count, b_pool: state.bob_pool_count,
    pool_count: Math.min(state.pool_count, state.bob_pool_count), qber: state.last_run?.qber ?? null
  }];
  $('link-setting-wrap').hidden = !state.topology;
  if (state.topology) {
    const select = $('link-setting');
    select.innerHTML = links.map(link => `<option value="${escapeHtml(link.id)}">${escapeHtml(displayName(link.a))}–${escapeHtml(displayName(link.b))}</option>`).join('');
    const firstRelayLink = links.find(link =>
      [link.a, link.b].some(node => node.toLowerCase() === 'alice') &&
      [link.a, link.b].some(node => node.toLowerCase() === 'r1'));
    const selected = links.some(link => link.id === selectedLinkId)
      ? selectedLinkId
      : (firstRelayLink || links.find(link => link.a.toLowerCase() === 'alice' || link.b.toLowerCase() === 'alice') || links[0])?.id;
    selectedLinkId = selected || '';
    select.value = selectedLinkId;
    const link = links.find(item => item.id === selectedLinkId);
    if (link) {
      $('distance').value = link.distance_km;
      $('noise').value = Math.round(Number(link.noise) * 100);
      $('eve').value = Math.round(Number(link.eve) * 100);
      formatSettings();
    }
  }
  const nodes = [...new Set(links.flatMap(link => [link.a, link.b]))];
  const alice = nodes.find(node => node.toLowerCase() === 'alice') || nodes[0];
  const bob = nodes.find(node => node.toLowerCase() === 'bob') || nodes[nodes.length - 1];
  const hops = new Map([[alice, 0]]), queue = [alice];
  while (queue.length) {
    const node = queue.shift();
    for (const link of links) {
      const next = link.a === node ? link.b : link.b === node ? link.a : null;
      if (next !== null && !hops.has(next)) { hops.set(next, hops.get(node) + 1); queue.push(next); }
    }
  }
  const relays = nodes.filter(node => node !== alice && node !== bob);
  const maxHop = Math.max(1, ...relays.map(node => hops.get(node) || 1));
  const layers = [...new Set(relays.map(node => hops.get(node) || maxHop))].sort((a, b) => a - b);
  const columns = layers.map(layer => relays.filter(node => (hops.get(node) || maxHop) === layer)
    .sort((a, b) => displayName(a).localeCompare(displayName(b))));
  const busiestColumn = Math.max(1, ...columns.map(column => column.length));
  const width = Math.max(640, 510 + 130 * maxHop);
  const height = Math.max(260, 120 + 100 * (busiestColumn - 1));
  const marginX = 70;
  const coords = {[alice]: {x: marginX, y: height / 2}, [bob]: {x: width - marginX, y: height / 2}};
  columns.forEach((column, columnIndex) => {
    const x = marginX + (width - 2 * marginX) * layers[columnIndex] / (maxHop + 1);
    column.forEach((node, index) => {
      coords[node] = {x, y: column.length === 1 ? height / 2 : 60 + index * (height - 120) / (column.length - 1)};
    });
  });
  $('network-diagram').setAttribute('viewBox', `0 0 ${width} ${height}`);
  const routeCount = state.topology ? (state.paths || []).length : 1;
  const summary = `${nodes.length} nodes · ${links.length} links · ${routeCount} route${routeCount === 1 ? '' : 's'}`;
  $('header-state').textContent = state.topology ? summary : 'Alice and Bob network';
  $('diagram-title').textContent = `${nodes.length}-node network · ${links.length} links · ${routeCount} route${routeCount === 1 ? '' : 's'}`;
  $('diagram-description').textContent = `A network diagram with ${nodes.length} nodes and ${links.length} links. ${routeCount} route${routeCount === 1 ? '' : 's'} connect ${displayName(alice)} to ${displayName(bob)}.`;

  const bounds = point => ({left: point.x - 48, right: point.x + 48, top: point.y - 22, bottom: point.y + 22});
  const clip = (center, toward) => {
    const dx = toward.x - center.x, dy = toward.y - center.y;
    const scale = Math.min(dx ? 48 / Math.abs(dx) : Infinity, dy ? 22 / Math.abs(dy) : Infinity);
    return {x: center.x + dx * scale, y: center.y + dy * scale};
  };
  const curve = (from, to, offset, samples = 32) => {
    const dx = to.x - from.x, dy = to.y - from.y, length = Math.hypot(dx, dy) || 1;
    const control = {x: (from.x + to.x) / 2 - dy / length * offset,
      y: (from.y + to.y) / 2 + dx / length * offset};
    const start = clip(from, control), end = clip(to, control);
    return Array.from({length: samples + 1}, (_, i) => {
      const t = i / samples, u = 1 - t;
      return {x: u * u * start.x + 2 * u * t * control.x + t * t * end.x,
        y: u * u * start.y + 2 * u * t * control.y + t * t * end.y};
    });
  };
  const hitsBox = (a, b, box) => {
    let low = 0, high = 1;
    const dx = b.x - a.x, dy = b.y - a.y;
    for (const [p, q] of [[-dx, a.x - box.left + 4], [dx, box.right + 4 - a.x],
      [-dy, a.y - box.top + 4], [dy, box.bottom + 4 - a.y]]) {
      if (!p) { if (q < 0) return false; continue; }
      const t = q / p;
      if (p < 0) low = Math.max(low, t); else high = Math.min(high, t);
      if (low > high) return false;
    }
    return true;
  };
  const overlaps = (a, b, c, d) => {
    const ux = b.x - a.x, uy = b.y - a.y, vx = d.x - c.x, vy = d.y - c.y;
    const length = Math.hypot(ux, uy) || 1, otherLength = Math.hypot(vx, vy) || 1;
    if (Math.abs(ux * vy - uy * vx) / (length * otherLength) > .04) return false;
    if (Math.abs(ux * (c.y - a.y) - uy * (c.x - a.x)) / length > 7) return false;
    const first = [0, length];
    const second = [((c.x - a.x) * ux + (c.y - a.y) * uy) / length,
      ((d.x - a.x) * ux + (d.y - a.y) * uy) / length].sort((x, y) => x - y);
    return Math.min(first[1], second[1]) - Math.max(first[0], second[0]) > 5;
  };
  const drawn = [];
  const clear = (points, link) => points.slice(1).every((point, index) => {
    const priorPoint = points[index];
    if (nodes.some(node => node !== link.a && node !== link.b && hitsBox(priorPoint, point, bounds(coords[node])))) return false;
    return drawn.every(prior => {
      return prior.points.slice(1).every((otherPoint, otherIndex) =>
        !overlaps(priorPoint, point, prior.points[otherIndex], otherPoint));
    });
  });
  const edgePoints = link => {
    const from = coords[link.a], to = coords[link.b];
    const direct = [clip(from, to), clip(to, from)];
    if (clear(direct, link)) return direct;
    for (let offset = 28; offset <= Math.max(width, height) * 2; offset += 28) {
      for (const sign of [1, -1]) {
        const bent = curve(from, to, offset * sign);
        if (clear(bent, link)) return bent;
      }
    }
    return curve(from, to, Math.max(width, height) * 2);
  };
  const middle = points => {
    const lengths = points.slice(1).map((point, i) => Math.hypot(point.x - points[i].x, point.y - points[i].y));
    let distance = lengths.reduce((sum, value) => sum + value, 0) / 2;
    for (let i = 0; i < lengths.length; i++) {
      if (distance <= lengths[i]) {
        const t = lengths[i] ? distance / lengths[i] : 0;
        return {x: points[i].x + (points[i + 1].x - points[i].x) * t,
          y: points[i].y + (points[i + 1].y - points[i].y) * t};
      }
      distance -= lengths[i];
    }
    return points[0];
  };
  const used = new Set();
  if (state.topology && state.last_path) {
    state.last_path.slice(0, -1).forEach((node, index) => used.add(pairId(node, state.last_path[index + 1])));
  }
  $('diagram-links').innerHTML = links.map(link => {
    const points = edgePoints(link);
    drawn.push({link, points});
    const path = points.map(point => `${point.x.toFixed(1)},${point.y.toFixed(1)}`).join(' L ');
    const active = used.has(pairId(link.a, link.b));
    const dot = middle(points);
    return `<g class="edge-group ${link.up ? 'is-up' : 'is-down'} ${active ? 'path-used' : ''}">
      <path class="diagram-edge" d="M ${path}"></path>
      <circle class="edge-dot" cx="${dot.x.toFixed(1)}" cy="${dot.y.toFixed(1)}" r="5"></circle>
      ${active ? `<circle class="path-pulse" cx="${dot.x.toFixed(1)}" cy="${dot.y.toFixed(1)}" r="10"></circle>` : ''}
    </g>`;
  }).join('');
  const pointsInView = [...drawn.flatMap(edge => edge.points),
    ...nodes.flatMap(node => {
      const box = bounds(coords[node]);
      return [{x: box.left, y: box.top}, {x: box.right, y: box.bottom}];
    })];
  const minX = Math.min(0, ...pointsInView.map(point => point.x - 18));
  const minY = Math.min(0, ...pointsInView.map(point => point.y - 18));
  const maxX = Math.max(width, ...pointsInView.map(point => point.x + 18));
  const maxY = Math.max(height, ...pointsInView.map(point => point.y + 18));
  $('network-diagram').setAttribute('viewBox', `${minX} ${minY} ${maxX - minX} ${maxY - minY}`);
  $('diagram-nodes').innerHTML = nodes.map(node => {
    const point = coords[node];
    return `<g class="diagram-node"><rect x="${point.x - 48}" y="${point.y - 22}" width="96" height="44" rx="14"></rect><text x="${point.x}" y="${point.y + 5}" text-anchor="middle">${escapeHtml(displayName(node))}</text></g>`;
  }).join('');

  const labelPaths = state.topology && state.last_path ? state.last_path.map(displayName).join(' → ') : '';
  $('last-path').textContent = labelPaths ? `Last message: ${labelPaths}` : 'No message sent yet';
  $('path-note').textContent = labelPaths
    ? `Highlighted route: ${labelPaths}. The bright dots mark the links used.`
    : 'The bright line will show the route used after a message is sent.';
  $('link-list').innerHTML = links.map(link => {
    const qber = link.qber == null ? 'Not measured yet' : `Error rate ${percent(link.qber)} — ${link.qber < QBER_LIMIT ? 'healthy' : 'needs attention'}`;
    const upLabel = link.up ? '✓ Link is up' : '✕ Link is down';
    const action = link.up ? 'fail' : 'restore';
    const buttonText = link.up ? 'Fail link' : 'Restore link';
    return `<article class="link-card ${link.up ? 'link-up' : 'link-down'}">
      <div class="link-card-heading"><h3>${escapeHtml(displayName(link.a))}–${escapeHtml(displayName(link.b))}</h3><span class="link-status">${upLabel}</span></div>
      <div class="link-facts"><span><b>Distance · noise · Eve</b>${Number(link.distance_km).toLocaleString()} km · ${percent(link.noise)} · ${percent(link.eve)}</span><span><b>Available keys</b>${Number(link.pool_count || 0).toLocaleString()}</span><span><b>Error check</b>${escapeHtml(qber)}</span></div>
      <button type="button" class="link-control ${link.up ? 'fail-control' : ''}" data-link-id="${escapeHtml(link.id)}" data-action="${action}">${buttonText}</button>
    </article>`;
  }).join('');
}

function pairId(a, b) { return [String(a), String(b)].sort().join('|'); }

function renderResult(state) {
  const sentence = $('result-sentence');
  if (lastDelivery) {
    const route = (lastDelivery.path || state.last_path || ['alice', 'bob']).map(displayName).join(' → ');
    sentence.textContent = `Message delivered via ${route}. Bob's node verified and decrypted it; one message key was used.`;
    $('network-view').textContent = Number.isFinite(lastDelivery.ciphertext_bytes)
      ? `What the network saw: ${lastDelivery.ciphertext_bytes} encrypted bytes. The browser does not receive the key or ciphertext contents.`
      : 'What the network saw: an encrypted message. The browser does not receive key material.';
    return;
  }
  $('network-view').textContent = 'What the network saw: no encrypted message yet.';
  if (!state.topology && state.last_run) {
    const run = state.last_run;
    if (run.accepted) {
      sentence.textContent = `The error rate was ${percent(run.qber)}. ${Number(run.pool_added)} matching key${run.pool_added === 1 ? '' : 's'} were accepted and can be used once each.`;
    } else if (Number(run.qber) >= Number(state.qber_limit || QBER_LIMIT)) {
      sentence.textContent = `The error rate was ${percent(run.qber)}, at or above the 11% limit, so these keys were rejected and not used.`;
    } else {
      sentence.textContent = 'The error check was below the limit, but no matching key was produced.';
    }
    return;
  }
  if (state.topology && state.links.some(link => link.qber != null)) {
    const measured = state.links.filter(link => link.qber != null);
    const highest = measured.reduce((best, link) => link.qber > best.qber ? link : best, measured[0]);
    if (highest.qber >= QBER_LIMIT) {
      sentence.textContent = `${displayName(highest.a)}–${displayName(highest.b)} had an error rate of ${percent(highest.qber)}, at or above the 11% limit. Keys from that link were rejected.`;
    } else {
      sentence.textContent = `The highest measured link error rate is ${percent(highest.qber)}, below the 11% limit. Accepted link keys are ready for a message.`;
    }
    return;
  }
  sentence.textContent = 'The result will appear here after you create keys or send a message.';
}

async function loadInbox(url) {
  try {
    const response = await fetch(url);
    if (!response.ok) throw new Error('Bob inbox request failed');
    const data = await response.json();
    inboxMessages = Array.isArray(data.messages) ? data.messages.slice(-5) : [];
    $('inbox-messages').innerHTML = inboxMessages.length
      ? inboxMessages.map(message => `<article class="inbox-message"><p class="inbox-label">What Bob sees</p><blockquote>${escapeHtml(message.text)}</blockquote></article>`).join('')
      : '<p class="empty-note">No messages received yet.</p>';
  } catch (_) {
    $('inbox-messages').innerHTML = '<p class="empty-note">Bob’s inbox is not responding. Start Bob’s process, then refresh.</p>';
    if (!statusOverride) {
      statusOverride = "Bob's node is not responding. Start Bob's process, then refresh.";
      say(statusOverride, 'error');
    }
  }
}

function renderAdvanced(state) {
  const values = [];
  if (state.last_run) {
    const run = state.last_run;
    values.push(['Run ID', run.seed ?? '—']);
    values.push(['Signals sent', Number(run.signals_sent || 0).toLocaleString()]);
    values.push(['Signals intercepted', Number(run.signals_intercepted || 0).toLocaleString()]);
    values.push(['Matching keys from run', Number(run.matching_cascade_keys || 0).toLocaleString()]);
    values.push(['Distance', `${state.settings.distance_km} km`]);
    values.push(['Channel noise', percent(state.settings.channel_noise)]);
    values.push(['Eavesdropper interception', percent(state.settings.eve_fraction)]);
    if (Array.isArray(state.key_ids) && state.key_ids.length) values.push(['Available key IDs', state.key_ids.join(', ')]);
  }
  if (state.topology) {
    values.push(['Last route IDs', state.last_path?.join(' → ') || 'No route used yet']);
    for (const link of state.links) {
      values.push([`${link.id} settings`, `${link.distance_km} km · noise ${percent(link.noise)} · interception ${percent(link.eve)}`]);
      values.push([`${link.id} pool counts`, `${link.a_pool} / ${link.b_pool}`]);
    }
  } else {
    values.push(['Alice key pool', `${state.pool_count} / ${state.pool_limit}`]);
    values.push(['Bob key pool', `${state.bob_pool_count} / ${state.pool_limit}`]);
  }
  if (lastDelivery) {
    values.push(['Message ID', lastDelivery.message_id || '—']);
    values.push(['Key ID', lastDelivery.key_id || '—']);
    values.push(['Encrypted payload length', `${lastDelivery.ciphertext_bytes} bytes`]);
  }
  if (unexpectedError) values.push(['Unexpected error', unexpectedError]);
  $('advanced-content').innerHTML = values.map(([label, value]) => {
    const idValue = /\bID(s)?\b/.test(label);
    return `<div class="advanced-item"><span>${escapeHtml(label)}</span><span class="${idValue ? 'technical-id' : ''}">${escapeHtml(value)}</span></div>`;
  }).join('') || '<p class="empty-note">No technical details yet.</p>';
}

function renderEvents(events) {
  $('event-list').innerHTML = events.length ? events.slice(0, 8).map(event => {
    // Older event text can contain a key identifier; keep those details in the collapsed section.
    const plain = String(event.message).replace(/QK-[A-F0-9]+/gi, 'a one-time key').replace(/E2E-[A-F0-9]+/gi, 'an end-to-end key');
    const level = ['success', 'warning', 'error'].includes(event.level) ? event.level : 'info';
    return `<article class="event-row ${level}"><span class="event-symbol" aria-hidden="true">${level === 'success' ? '✓' : level === 'warning' ? '!' : '·'}</span><time>${timeLabel(event.time)}</time><p>${escapeHtml(plain)}</p></article>`;
  }).join('') : '<p class="empty-note">No events yet.</p>';
}

function updateActions(state) {
  let canGenerate = false;
  let generateReason = '';
  let availableCount = 0;
  if (!state.topology) {
    availableCount = Number(state.pool_count || 0);
    canGenerate = Boolean(state.link_up && availableCount < Number(state.pool_limit));
    if (!state.alice_online) generateReason = "Alice's node is not responding.";
    else if (!state.bob_online) generateReason = "Bob's node is not responding.";
    else if (!state.link_up) generateReason = 'Restore the link first.';
    else if (!canGenerate) generateReason = 'The key pools are full. Send a message to make room.';
  } else {
    const eligible = state.links.filter(link => link.up && (link.a_pool < 24 || link.b_pool < 24));
    availableCount = state.links.filter(link => link.up).reduce((sum, link) => sum + Number(link.pool_count || 0), 0);
    canGenerate = eligible.length > 0;
    if (!canGenerate) generateReason = state.links.some(link => link.up) ? 'Open links are full. Send a message to use keys first.' : 'Restore at least one link first.';
  }
  $('generate-keys').disabled = requestBusy || !canGenerate;
  $('key-action-reason').textContent = requestBusy ? 'A request is in progress…' : canGenerate ? 'Creates keys on available links.' : generateReason;
  $('pool-summary').textContent = state.topology
    ? `${availableCount} matching link keys available across the open links.`
    : `${state.pool_count} key${state.pool_count === 1 ? '' : 's'} ready at Alice; ${state.bob_pool_count} at Bob.`;

  const messageReady = Boolean($('message').value.trim());
  const hasRoute = state.topology
    ? (state.paths || []).some(path => routeAvailable(state, path))
    : Boolean(state.link_up && state.alice_online && state.bob_online && state.pool_count > 0);
  const canSend = messageReady && hasRoute;
  $('send-message').disabled = requestBusy || !canSend;
  if (requestBusy) $('send-action-reason').textContent = 'A request is in progress…';
  else if (!messageReady) $('send-action-reason').textContent = 'Write a message to enable sending.';
  else if (!state.topology && !state.alice_online) $('send-action-reason').textContent = "Alice's node is not responding.";
  else if (!state.topology && !state.bob_online) $('send-action-reason').textContent = "Bob's node is not responding.";
  else if (!hasRoute) $('send-action-reason').textContent = state.topology ? 'Create keys on a complete open route first.' : 'Create keys first.';
  else $('send-action-reason').textContent = 'Your message will be encrypted before delivery.';
}

async function refresh({clearError = false} = {}) {
  if (clearError) statusOverride = '';
  const state = await request('/api/state');
  render(state);
  return state;
}

async function runAction(action, successMessage) {
  statusOverride = '';
  requestBusy = true;
  if (currentState) updateActions(currentState);
  try {
    const result = await action();
    if (result?.state) render(result.state);
    else await refresh();
    say(successMessage(result), 'success');
    return result;
  } catch (error) {
    statusOverride = helpfulError(error);
    if (statusOverride === GENERIC_ACTION_ERROR) {
      unexpectedError = String(error?.message || error);
      console.error(error);
      if (currentState) renderAdvanced(currentState);
      $('advanced-details').open = true;
    }
    say(statusOverride, 'error');
    return null;
  } finally {
    requestBusy = false;
    if (currentState) updateActions(currentState);
  }
}

async function generateKeys() {
  lastDelivery = null;
  await runAction(async () => request('/api/keys/generate', {count: 24}), result =>
    result.generated ? `Created ${result.generated} matching key${result.generated === 1 ? '' : 's'}. You can now send a message.` : 'No keys were accepted. Check the error rate and try different settings.');
}

async function sendMessage() {
  const text = $('message').value;
  if (!text.trim()) return;
  lastDelivery = null;
  const result = await runAction(() => request('/api/messages', {text}), data =>
    `Bob received your message. One key was used; you can send another if a route still has keys.`);
  if (!result) return;
  lastDelivery = {
    message_id: result.message_id,
    key_id: result.key_id,
    ciphertext_bytes: Number(result.ciphertext_bytes),
    path: result.path || currentState?.last_path || ['alice', 'bob']
  };
  $('message').value = '';
  updateCharCount();
  try { await refresh(); } catch (_) { /* Keep the delivery receipt visible if a later refresh fails. */ }
  if (currentState) renderAdvanced(currentState);
  if (currentInboxUrl) await loadInbox(currentInboxUrl);
  say(`Message delivered. Bob can read it below; ${lastDelivery.ciphertext_bytes} encrypted bytes crossed the network.`, 'success');
}

async function changeLink(linkId, action) {
  const friendly = action === 'fail' ? 'failed' : 'restored';
  const endpoint = currentState?.topology
    ? `/api/links/${encodeURIComponent(linkId)}/${action}`
    : `/api/link/${action}`;
  const state = await runAction(() => request(endpoint, {}), () => {
    const link = currentState?.links.find(item => item.id === linkId);
    return link ? `${displayName(link.a)}–${displayName(link.b)} is ${friendly}. ${friendly === 'failed' ? 'The other route can still carry messages if it has keys.' : 'You can create keys on this route.'}` : `Link ${friendly}.`;
  });
  if (state?.topology) statusOverride = $('status-text').textContent;
}

function formatSettings() {
  $('distance-output').textContent = `${$('distance').value} km`;
  $('noise-output').textContent = `${$('noise').value}%`;
  $('eve-output').textContent = `${$('eve').value}%`;
}
function updateCharCount() {
  $('char-count').textContent = `${$('message').value.length.toLocaleString()} / 2,000`;
  if (currentState) updateActions(currentState);
}

async function saveSettings(presetName = '') {
  const settings = {
    distance_km: Number($('distance').value),
    channel_noise: Number($('noise').value) / 100,
    eve_fraction: Number($('eve').value) / 100
  };
  const endpoint = currentState?.topology
    ? `/api/links/${encodeURIComponent(selectedLinkId)}/settings`
    : '/api/settings';
  const result = await runAction(() => request(endpoint, settings), () => {
    const prefix = presetName ? `${presetName} preset saved.` : 'Experiment settings saved.';
    return currentState?.topology
      ? `${prefix} ${displayName(selectedLinkId)} will use them the next time keys are created.`
      : `${prefix} They will be used the next time you create keys.`;
  });
  if (result) $('customize-section').open = true;
}

function loadSelectedLinkSettings() {
  selectedLinkId = $('link-setting').value;
  const link = currentState?.links?.find(item => item.id === selectedLinkId);
  if (!link) return;
  $('distance').value = link.distance_km;
  $('noise').value = Math.round(Number(link.noise) * 100);
  $('eve').value = Math.round(Number(link.eve) * 100);
  formatSettings();
}

function setPreset(name) {
  const preset = PRESETS[name];
  if (!preset) return;
  $('distance').value = preset.distance;
  $('noise').value = preset.noise;
  $('eve').value = preset.eve;
  document.querySelectorAll('[data-preset]').forEach(button => button.classList.toggle('selected', button.dataset.preset === name));
  formatSettings();
  saveSettings(preset.label);
}

$('refresh-state').addEventListener('click', async () => {
  try { await refresh({clearError: true}); }
  catch (error) { statusOverride = helpfulError(error); say(statusOverride, 'error'); }
});
$('generate-keys').addEventListener('click', generateKeys);
$('send-message').addEventListener('click', sendMessage);
$('message').addEventListener('input', updateCharCount);
$('apply-settings').addEventListener('click', () => saveSettings());
$('link-setting').addEventListener('change', loadSelectedLinkSettings);
['distance', 'noise', 'eve'].forEach(id => $(id).addEventListener('input', formatSettings));
document.querySelectorAll('[data-preset]').forEach(button => button.addEventListener('click', () => setPreset(button.dataset.preset)));
$('link-list').addEventListener('click', event => {
  const button = event.target.closest('[data-link-id]');
  if (button) changeLink(button.dataset.linkId, button.dataset.action);
});
$('reset-demo').addEventListener('click', async () => {
  lastDelivery = null;
  inboxMessages = [];
  $('message').value = '';
  updateCharCount();
  const result = await runAction(() => request('/api/reset', {}), () => 'Experiment reset. Create keys to start again.');
  if (result?.topology) await loadInbox(result.bob_inbox_url);
});

formatSettings();
refresh().catch(error => {
  statusOverride = helpfulError(error);
  say(statusOverride, 'error');
});
