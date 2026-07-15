"""
worker/worker_ui.py
────────────────────────────────────────────────────────────────────────
A local browser control panel for the Icarus ingestion worker — for
anyone who'd rather paste a key into a page than deal with environment
variables and a terminal.

Run it:
    python worker_ui.py

A browser tab opens automatically at http://127.0.0.1:5057. Paste in
your Icarus site's address and the worker key from the "Remote Ingestion
Worker" dashboard panel, click Start, and watch it work. Click Stop
whenever, or just close this terminal window (Ctrl+C) when you're done —
nothing is left running in the background afterward.

Security notes, since this handles a live credential:
  - Binds to 127.0.0.1 only. Nothing outside this computer can reach it.
    There's no login screen because "only reachable from this computer"
    is the access control — the same reasoning as any localhost-only
    dev tool.
  - The worker key lives in memory for as long as this process runs and
    is never written to disk by this script.
  - This key can only authenticate the worker's own upload/download
    endpoints on the Icarus server — it has no path to the admin toggle
    that enables remote ingestion in the first place. That switch has to
    be flipped separately, by a signed-in administrator, in the Wagtail
    dashboard. This tool can't do that for you, on purpose.
"""
import logging
import queue
import threading
import webbrowser

from flask import Flask, Response, jsonify, request

from ingest_worker import WorkerRunner

app = Flask(__name__)

# Quiet the dev server's per-request access log (constant, given the log
# stream and status polling below hold connections open) — the startup
# banner still prints so you can see the address to open.
logging.getLogger("werkzeug").setLevel(logging.WARNING)

PORT = 5057

# ── In-memory state only — nothing here is ever written to disk ──────────
state_lock = threading.Lock()
state = {"running": False, "stop_event": None, "summary": None, "error": None}
log_queue: "queue.Queue[str]" = queue.Queue()


class _QueueLogHandler(logging.Handler):
    """Forwards every log line the worker already produces straight to the
    browser, so nothing about the underlying OCR/embedding pipeline needs
    to change to support this UI."""
    def emit(self, record):
        log_queue.put(self.format(record))


_handler = _QueueLogHandler()
_handler.setFormatter(logging.Formatter("%(asctime)s  %(message)s", datefmt="%H:%M:%S"))
worker_logger = logging.getLogger("ingest_worker")
worker_logger.addHandler(_handler)
worker_logger.setLevel(logging.INFO)


def _run_worker(base_url, token, force, stop_event):
    with state_lock:
        state["running"] = True
        state["summary"] = None
        state["error"] = None
    log_queue.put(f"── Starting — connecting to {base_url} ──")
    try:
        runner = WorkerRunner(base_url, token, stop_event=stop_event)
        summary = runner.run(force=force)
        with state_lock:
            state["summary"] = summary
        if summary["stopped"]:
            log_queue.put(f"── Stopped. Processed {summary['processed']}, failed {summary['failed']}. ──")
        else:
            log_queue.put(f"── Finished. Processed {summary['processed']}, failed {summary['failed']}. ──")
    except Exception as exc:
        with state_lock:
            state["error"] = str(exc)
        log_queue.put(f"── Run failed: {exc} ──")
        # Deliberately a different logger than worker_logger (which has the
        # queue handler attached) — the full traceback goes to the terminal
        # for debugging, not into the browser's friendly log panel, which
        # already got the one-line version above.
        logging.getLogger("worker_ui").exception("Worker UI run failed")
    finally:
        with state_lock:
            state["running"] = False


@app.route("/")
def index():
    return PAGE_HTML


@app.route("/favicon.ico")
def favicon():
    return "", 204


@app.route("/start", methods=["POST"])
def start():
    with state_lock:
        if state["running"]:
            return jsonify({"error": "Already running — check for another open tab, or wait for it to finish."}), 409

        base_url = request.form.get("base_url", "").strip().rstrip("/")
        api_key = request.form.get("api_key", "").strip()
        force = request.form.get("force") == "on"

        if not base_url or not api_key:
            return jsonify({"error": "Enter both the Icarus address and the worker key before starting."}), 400
        if not base_url.startswith(("http://", "https://")):
            return jsonify({"error": "The Icarus address should start with https:// (or http:// for local testing)."}), 400

        stop_event = threading.Event()
        state["stop_event"] = stop_event
        thread = threading.Thread(target=_run_worker, args=(base_url, api_key, force, stop_event), daemon=True)
        thread.start()

    return jsonify({"success": True})


@app.route("/stop", methods=["POST"])
def stop():
    with state_lock:
        if not state["running"] or state["stop_event"] is None:
            return jsonify({"error": "Nothing is running right now."}), 409
        state["stop_event"].set()
    log_queue.put("── Stopping — finishing the current page or batch, then it will stop ──")
    return jsonify({"success": True})


@app.route("/status")
def status():
    with state_lock:
        return jsonify({
            "running": state["running"],
            "summary": state["summary"],
            "error": state["error"],
        })


@app.route("/events")
def events():
    def stream():
        while True:
            try:
                line = log_queue.get(timeout=15)
                yield f"data: {line}\n\n"
            except queue.Empty:
                yield ": keep-alive\n\n"

    return Response(
        stream(),
        mimetype="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


PAGE_HTML = """
<!doctype html>
<html lang="ml">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Icarus Ingestion Worker</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link href="https://fonts.googleapis.com/css2?family=Playfair+Display:wght@600;700&family=Source+Sans+3:wght@400;500;600&display=swap" rel="stylesheet">
<style>
  :root {
    --paper: #f5f0e6;
    --card: #fffdf8;
    --border: #e3d9c2;
    --navy: #1c2b46;
    --navy-soft: #3c4f6e;
    --ink-muted: #6b7280;
    --red: #a6372c;
    --red-dark: #872c23;
    --amber: #c9852b;
    --green: #3f7d4f;
    --console-bg: #12151c;
    --console-text: #cbd3dd;
    --console-dim: #667085;
  }
  * { box-sizing: border-box; }
  body {
    margin: 0;
    padding: 48px 20px;
    background: var(--paper);
    color: var(--navy);
    font-family: 'Source Sans 3', system-ui, -apple-system, sans-serif;
    display: flex;
    justify-content: center;
  }
  main { width: 100%; max-width: 620px; }

  .eyebrow {
    font-size: 12px;
    font-weight: 600;
    letter-spacing: 0.14em;
    text-transform: uppercase;
    color: var(--red);
    margin-bottom: 6px;
  }
  h1 {
    font-family: 'Playfair Display', Georgia, serif;
    font-size: 32px;
    font-weight: 700;
    margin: 0 0 8px;
    color: var(--navy);
  }
  .lede {
    color: var(--ink-muted);
    font-size: 15px;
    line-height: 1.55;
    margin: 0 0 28px;
    max-width: 52ch;
  }

  .card {
    background: var(--card);
    border: 1px solid var(--border);
    border-radius: 10px;
    padding: 28px;
    box-shadow: 0 1px 2px rgba(28, 43, 70, 0.04);
  }

  .field { margin-bottom: 18px; }
  .field:last-of-type { margin-bottom: 0; }
  label {
    display: block;
    font-weight: 600;
    font-size: 14px;
    margin-bottom: 5px;
    color: var(--navy);
  }
  .hint {
    font-size: 12.5px;
    color: var(--ink-muted);
    margin-top: 5px;
    line-height: 1.4;
  }
  input[type="text"], input[type="password"] {
    width: 100%;
    padding: 10px 12px;
    font-size: 15px;
    font-family: inherit;
    border: 1px solid var(--border);
    border-radius: 6px;
    background: #fff;
    color: var(--navy);
  }
  input[type="text"]:focus, input[type="password"]:focus {
    outline: 2px solid var(--navy-soft);
    outline-offset: 1px;
    border-color: var(--navy-soft);
  }
  .key-row { display: flex; gap: 8px; }
  .key-row input { flex: 1; }
  .ghost-btn {
    background: none;
    border: 1px solid var(--border);
    border-radius: 6px;
    padding: 0 14px;
    font-size: 13px;
    color: var(--navy-soft);
    cursor: pointer;
    white-space: nowrap;
  }
  .ghost-btn:hover { border-color: var(--navy-soft); }

  .checkbox-row {
    display: flex;
    align-items: flex-start;
    gap: 10px;
    margin: 22px 0 24px;
  }
  .checkbox-row input { margin-top: 3px; }
  .checkbox-row .checkbox-label { font-weight: 600; font-size: 14px; }

  .actions { display: flex; gap: 10px; margin-top: 4px; }
  button.primary, button.stop {
    font-family: inherit;
    font-size: 15px;
    font-weight: 600;
    padding: 11px 22px;
    border-radius: 6px;
    border: none;
    cursor: pointer;
  }
  button.primary {
    background: var(--red);
    color: #fff;
  }
  button.primary:hover:not(:disabled) { background: var(--red-dark); }
  button.primary:disabled { background: #d7bdb8; cursor: not-allowed; }

  button.stop {
    background: #fff;
    color: var(--navy);
    border: 1px solid var(--border);
  }
  button.stop:hover:not(:disabled) { border-color: var(--navy-soft); }
  button.stop:disabled { color: var(--ink-muted); cursor: not-allowed; }

  button:focus-visible { outline: 2px solid var(--navy-soft); outline-offset: 2px; }

  .form-error {
    display: none;
    background: #fbeceb;
    border: 1px solid #e2b2ac;
    color: var(--red-dark);
    font-size: 13.5px;
    padding: 9px 12px;
    border-radius: 6px;
    margin-top: 14px;
  }

  .status-line {
    display: flex;
    align-items: center;
    gap: 9px;
    margin-top: 20px;
    padding-top: 18px;
    border-top: 1px solid var(--border);
    font-size: 14px;
  }
  .dot {
    width: 9px; height: 9px; border-radius: 50%;
    background: var(--ink-muted);
    flex-shrink: 0;
  }
  .dot.running { background: var(--amber); }
  .dot.done { background: var(--green); }
  .dot.error { background: var(--red); }
  @media (prefers-reduced-motion: no-preference) {
    .dot.running { animation: pulse 1.4s ease-in-out infinite; }
  }
  @keyframes pulse {
    0%, 100% { opacity: 1; }
    50% { opacity: 0.35; }
  }

  details { margin-top: 22px; }
  summary {
    cursor: pointer;
    font-size: 13.5px;
    color: var(--navy-soft);
    font-weight: 600;
  }
  details p {
    font-size: 13.5px;
    color: var(--ink-muted);
    line-height: 1.6;
    margin: 10px 0 0;
  }

  .console {
    margin-top: 22px;
    background: var(--console-bg);
    border-radius: 10px;
    padding: 16px 18px;
    height: 260px;
    overflow-y: auto;
    font-family: ui-monospace, SFMono-Regular, Consolas, 'Liberation Mono', Menlo, monospace;
    font-size: 12.5px;
    line-height: 1.7;
    color: var(--console-text);
  }
  .console .line { white-space: pre-wrap; word-break: break-word; }
  .console .placeholder { color: var(--console-dim); }
  .cursor { display: none; }
  .console.is-running .cursor {
    display: inline-block;
    color: var(--console-text);
  }
  @media (prefers-reduced-motion: no-preference) {
    .console.is-running .cursor { animation: blink 1s steps(1) infinite; }
  }
  @keyframes blink { 50% { opacity: 0; } }
</style>
</head>
<body>
<main>
  <div class="eyebrow">Icarus</div>
  <h1>Ingestion Worker</h1>
  <p class="lede">
    Reads scanned back issues, pulls the text out of them, and sends it to
    the Icarus site so those issues become searchable. Runs on this
    computer only — nothing here is saved anywhere else.
  </p>

  <div class="card">
    <form id="worker-form">
      <div class="field">
        <label for="base_url">Icarus address</label>
        <input type="text" id="base_url" name="base_url" placeholder="https://yoursite.example" autocomplete="off">
        <div class="hint">The web address of your Icarus site.</div>
      </div>

      <div class="field">
        <label for="api_key">Worker key</label>
        <div class="key-row">
          <input type="password" id="api_key" name="api_key" placeholder="icrs_..." autocomplete="off">
          <button type="button" class="ghost-btn" id="toggle-key">Show</button>
        </div>
        <div class="hint">From the "Remote Ingestion Worker" panel in the Wagtail admin dashboard. If you don't have one, ask an administrator to generate one for you.</div>
      </div>

      <div class="checkbox-row">
        <input type="checkbox" id="force" name="force">
        <label for="force" style="margin:0;">
          <span class="checkbox-label">Reprocess everything</span>
          <div class="hint" style="margin-top:2px;">Normally only new issues are processed. Turn this on to redo issues that were already done too — this takes much longer.</div>
        </label>
      </div>

      <div class="actions">
        <button type="submit" class="primary" id="start-btn">Start processing</button>
        <button type="button" class="stop" id="stop-btn" disabled>Stop</button>
      </div>

      <div class="form-error" id="form-error"></div>
    </form>

    <div class="status-line">
      <span class="dot" id="status-dot"></span>
      <span id="status-text">Idle — not doing anything right now.</span>
    </div>

    <details>
      <summary>What does this actually do?</summary>
      <p>
        When you click Start, this computer asks the Icarus site for any
        back issues that haven't been processed yet, downloads each one,
        reads the Malayalam and English text off every page, and sends
        the results back so people can search inside them. Large issues
        can take a while — you can leave this open and do something else,
        or click Stop at any point and pick up later.
      </p>
    </details>

    <div class="console" id="console">
      <div class="line placeholder">Nothing yet — logs will appear here once you click Start.</div>
      <span class="cursor">▌</span>
    </div>
  </div>
</main>

<script>
(function () {
  var form = document.getElementById('worker-form');
  var startBtn = document.getElementById('start-btn');
  var stopBtn = document.getElementById('stop-btn');
  var formError = document.getElementById('form-error');
  var statusDot = document.getElementById('status-dot');
  var statusText = document.getElementById('status-text');
  var consoleEl = document.getElementById('console');
  var toggleKeyBtn = document.getElementById('toggle-key');
  var apiKeyInput = document.getElementById('api_key');
  var placeholderLine = consoleEl.querySelector('.placeholder');

  toggleKeyBtn.addEventListener('click', function () {
    var showing = apiKeyInput.type === 'text';
    apiKeyInput.type = showing ? 'password' : 'text';
    toggleKeyBtn.textContent = showing ? 'Show' : 'Hide';
  });

  function isNearBottom() {
    return consoleEl.scrollHeight - consoleEl.scrollTop - consoleEl.clientHeight < 40;
  }

  var MAX_LOG_LINES = 500;

  function appendLine(text) {
    if (placeholderLine) { placeholderLine.remove(); placeholderLine = null; }
    var wasNearBottom = isNearBottom();
    var line = document.createElement('div');
    line.className = 'line';
    line.textContent = text;
    consoleEl.insertBefore(line, consoleEl.querySelector('.cursor'));
    var lines = consoleEl.querySelectorAll('.line');
    if (lines.length > MAX_LOG_LINES) {
      for (var i = 0; i < lines.length - MAX_LOG_LINES; i++) lines[i].remove();
    }
    if (wasNearBottom) consoleEl.scrollTop = consoleEl.scrollHeight;
  }

  function setRunning(running) {
    startBtn.disabled = running;
    stopBtn.disabled = !running;
    consoleEl.classList.toggle('is-running', running);
    if (running) {
      statusDot.className = 'dot running';
      statusText.textContent = 'Working \u2014 processing archive issues\u2026';
    }
  }

  function setIdleState(data) {
    setRunning(false);
    if (data.error) {
      statusDot.className = 'dot error';
      statusText.textContent = 'Something went wrong: ' + data.error;
    } else if (data.summary) {
      var s = data.summary;
      statusDot.className = 'dot done';
      statusText.textContent = (s.stopped ? 'Stopped. ' : 'Finished. ') +
        'Processed ' + s.processed + ', failed ' + s.failed + '.';
    } else {
      statusDot.className = 'dot';
      statusText.textContent = 'Idle \u2014 not doing anything right now.';
    }
  }

  function refreshStatus() {
    fetch('/status').then(function (r) { return r.json(); }).then(function (data) {
      if (data.running) { setRunning(true); } else { setIdleState(data); }
    }).catch(function () {});
  }

  form.addEventListener('submit', function (e) {
    e.preventDefault();
    formError.style.display = 'none';
    fetch('/start', { method: 'POST', body: new FormData(form) })
      .then(function (r) { return r.json().then(function (data) { return { ok: r.ok, data: data }; }); })
      .then(function (res) {
        if (!res.ok) {
          formError.textContent = res.data.error;
          formError.style.display = 'block';
          return;
        }
        setRunning(true);
      })
      .catch(function () {
        formError.textContent = 'Could not reach the worker. Is this page still running in your terminal?';
        formError.style.display = 'block';
      });
  });

  stopBtn.addEventListener('click', function () {
    stopBtn.disabled = true;
    statusText.textContent = 'Stopping \u2014 finishing the current step\u2026';
    fetch('/stop', { method: 'POST' }).catch(function () {});
  });

  var es = new EventSource('/events');
  es.onmessage = function (e) {
    if (e.data.indexOf('keep-alive') === -1) appendLine(e.data);
  };

  refreshStatus();
  setInterval(refreshStatus, 4000);
})();
</script>
</body>
</html>
"""


if __name__ == "__main__":
    url = f"http://127.0.0.1:{PORT}"
    print("=" * 60)
    print(" Icarus Ingestion Worker")
    print("=" * 60)
    print(f" Opening {url} in your browser...")
    print(" If it doesn't open on its own, copy that address into")
    print(" your browser's address bar.")
    print()
    print(" Close this window (or press Ctrl+C) when you're done —")
    print(" that stops everything, nothing keeps running afterward.")
    print("=" * 60)
    threading.Timer(1.0, lambda: webbrowser.open(url)).start()
    app.run(host="127.0.0.1", port=PORT, threaded=True)