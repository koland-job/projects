/**
 * Fleet Ledger menu: runs the recalculation on the server and shows a clear result in the sheet.
 * Script properties: RECALC_URL (HTTPS address of /api/recalc), RECALC_TOKEN.
 * @OnlyCurrentDoc
 */
const POLL_SECONDS = 10;
const WAIT_MINUTES = 5;
const REQUEST_TIMEOUT_SECONDS = 20;

function onOpen() {
  SpreadsheetApp.getUi().createMenu('Fleet Ledger')
    .addItem('Recalculate reports', 'recalc')
    .addItem('Last recalculation status', 'showStatus')
    .addToUi();
}

function recalc() {
  const ss = SpreadsheetApp.getActive();
  const deadline = Date.now() + WAIT_MINUTES * 60 * 1000;
  try {
    let state = callServer_('post');
    const run = runKey_(state);
    if (state.running) {
      ss.toast('Recalculation in progress. If it was already running, waiting for its result.', 'Fleet Ledger', 30);
    }
    // Leave time for the last HTTP request before the wait limit.
    while (state.running && Date.now() + (POLL_SECONDS + REQUEST_TIMEOUT_SECONDS) * 1000 < deadline) {
      Utilities.sleep(POLL_SECONDS * 1000);
      state = callServer_('get');
      if (run && run !== runKey_(state)) {
        throw new Error('The server is already showing another run. The result of your run is not confirmed. Check "Fleet Ledger → Last recalculation status" and ask an administrator for the run journal.');
      }
    }
    if (state.running) {
      ss.toast('The wait is over, but the calculation on the server is still running. Check Fleet Ledger → Last recalculation status.', 'Fleet Ledger', 20);
      return;
    }
    report_(state);
  } catch (error) {
    showError_(error);
  }
}

function showStatus() {
  try {
    const state = callServer_('get');
    if (state.running) {
      SpreadsheetApp.getUi().alert('Recalculation running since ' + formatTime_(state.started_at) + '.\nNo need to start it again.');
      return;
    }
    report_(state);
  } catch (error) {
    showError_(error);
  }
}

function runKey_(state) {
  return state.last && (state.last.output || state.last.started_at);
}

function report_(state) {
  const ss = SpreadsheetApp.getActive();
  const last = state.last;
  const success = state.last_success_at
    ? '\n\nLast successful recalculation: ' + formatTime_(state.last_success_at) + '.' : '';
  // An unfinished write matters more than an old successful result in the journal.
  if (state.needs_review || (last && last.status === 'needs_review')) {
    ss.toast('The sheet needs checking before running again.', 'Fleet Ledger', 10);
    SpreadsheetApp.getUi().alert('The recalculation needs review.\n\n' +
      (state.detail || (last && last.error) || 'The result of the last write is not confirmed.') +
      '\n\nAsk an administrator to reconcile the sheet and the journal. Do not run the recalculation again until this is checked.' + success);
  } else if (state.busy) {
    showError_(new Error(state.detail || 'The server is busy with maintenance. Try again later.'));
  } else if (!last) {
    SpreadsheetApp.getUi().alert('The recalculation has never been run.');
  } else if (last.status === 'ok') {
    ss.toast('Done: reports recalculated ' + formatTime_(last.finished_at) + '.', 'Fleet Ledger', 10);
  } else if (last.status === 'error') {
    ss.toast('The recalculation failed. Details are in the message.', 'Fleet Ledger', 10);
    SpreadsheetApp.getUi().alert('The recalculation did not complete (' + formatTime_(last.finished_at || last.started_at) + ').' +
      '.\n\n' + (last.error || 'The reason was not recorded. Ask an administrator to check the journal.') + success);
  } else {
    showError_(new Error('The server returned a contradictory status. A successful recalculation is not confirmed. Ask an administrator to check the journal.'));
  }
}

function showError_(error) {
  SpreadsheetApp.getActive().toast('Could not confirm the result. Details are in the message.', 'Fleet Ledger', 10);
  SpreadsheetApp.getUi().alert('Fleet Ledger — report recalculation\n\n' +
    (error && error.message ? error.message : 'Unknown error. Check the recalculation status later.'));
}

function callServer_(method) {
  const props = PropertiesService.getScriptProperties();
  const url = (props.getProperty('RECALC_URL') || '').trim();
  const token = (props.getProperty('RECALC_TOKEN') || '').trim();
  if (!url || !token) {
    throw new Error('RECALC_URL and RECALC_TOKEN are not set in "Project Settings → Script properties".');
  }
  if (!/^https:\/\/[^\s/?#]+\/api\/recalc$/.test(url)) {
    throw new Error('Check RECALC_URL: it must be the server\'s HTTPS address ending in /api/recalc, with no spaces or parameters.');
  }
  let response;
  try {
    response = UrlFetchApp.fetch(url, {
      method: method,
      headers: {'X-Recalc-Token': token},
      muteHttpExceptions: true,
      followRedirects: false,
      timeoutSeconds: REQUEST_TIMEOUT_SECONDS,
    });
  } catch (error) {
    // The POST may have been accepted before the connection dropped. Never retry it automatically.
    throw new Error('No response from the server: connection failure or timeout.\n\n' +
      (method === 'post' ? 'The request may have started the recalculation. ' : 'The recalculation on the server may have continued. ') +
      'First check "Fleet Ledger → Last recalculation status". If the connection is still down later, tell an administrator.');
  }
  const code = response.getResponseCode();
  let body = null;
  try { body = JSON.parse(response.getContentText()); } catch (error) { /* Server error HTML is not shown. */ }
  const detail = body && typeof body.detail === 'string' ? body.detail.slice(0, 3000) : '';
  if (code === 401 || code === 403) {
    throw new Error('The server denied access. Check RECALC_TOKEN in the script properties and on the server.');
  }
  if (code === 404) throw new Error('The recalculation address was not found. Check RECALC_URL and the installed server version.');
  if (code >= 300 && code < 400) throw new Error('The server redirects the request. Set the final HTTPS address of /api/recalc in RECALC_URL.');
  if (code === 429) throw new Error('Too many requests. Wait a minute and check the recalculation status.');
  if (code !== 200) {
    throw new Error((detail || 'The server is temporarily unavailable or answered with HTTP ' + code + '.') +
      '\n\nIf the run was already submitted, check its status first. It is never retried automatically.');
  }
  if (!body || typeof body !== 'object' || Array.isArray(body) || typeof body.running !== 'boolean' ||
      !Object.prototype.hasOwnProperty.call(body, 'last') ||
      (body.last !== null && (typeof body.last !== 'object' || Array.isArray(body.last) ||
       !['running', 'ok', 'error', 'needs_review'].includes(body.last.status)))) {
    throw new Error('The server returned an unexpected response instead of the recalculation status. Check RECALC_URL and tell an administrator. The result of the run is not confirmed.');
  }
  return body;
}

function formatTime_(iso) {
  if (typeof iso !== 'string' || !iso) return 'time not specified';
  const date = new Date(iso.replace(/\.(\d{3})\d*/, '.$1'));
  if (!Number.isFinite(date.getTime())) return 'time not specified';
  return Utilities.formatDate(date, SpreadsheetApp.getActive().getSpreadsheetTimeZone(), 'MMM d, HH:mm');
}
