from __future__ import annotations

import json
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from . import control, db
from .config import Config

PAGE = """<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>immich-omni</title>
<style>
  :root { color-scheme: light dark; }
  body { font-family: system-ui, -apple-system, "Segoe UI", sans-serif; margin: 0;
         background: #f6f7f9; color: #1d1f23; }
  main { max-width: 880px; margin: 0 auto; padding: 32px 20px 60px; }
  h1 { font-size: 20px; margin: 0 0 4px; }
  .sub { color: #6b7280; font-size: 13px; margin-bottom: 24px; }
  .cards { display: grid; grid-template-columns: repeat(auto-fit, minmax(120px, 1fr));
           gap: 12px; margin-bottom: 24px; }
  .card { background: #fff; border: 1px solid #e5e7eb; border-radius: 10px; padding: 14px; }
  .card .k { font-size: 12px; color: #6b7280; }
  .card .v { font-size: 24px; font-weight: 650; margin-top: 4px; font-variant-numeric: tabular-nums; }
  .bar { height: 12px; background: #e5e7eb; border-radius: 999px; overflow: hidden; margin: 6px 0 2px; }
  .bar > span { display: block; height: 100%; background: #3b82f6; width: 0; transition: width .4s; }
  .bar.vec > span { background: #10b981; }
  .row { display: flex; justify-content: space-between; font-size: 12px; color: #6b7280; }
  .panel { background: #fff; border: 1px solid #e5e7eb; border-radius: 10px; padding: 18px; margin-bottom: 16px; }
  .status { display: flex; align-items: center; gap: 10px; margin-bottom: 12px; }
  .dot { width: 9px; height: 9px; border-radius: 50%; background: #9ca3af; }
  .dot.ok { background: #10b981; }
  .dot.bad { background: #ef4444; }
  .dot.pause { background: #f59e0b; }
  button { font: inherit; padding: 8px 16px; border-radius: 8px; border: 1px solid #d1d5db;
           background: #fff; cursor: pointer; }
  button.primary { background: #3b82f6; border-color: #3b82f6; color: #fff; }
  button.danger { background: #f59e0b; border-color: #f59e0b; color: #fff; }
  button:disabled { opacity: .5; cursor: default; }
  #err { color: #ef4444; font-size: 13px; min-height: 18px; }
  @media (prefers-color-scheme: dark) {
    body { background: #111318; color: #e5e7eb; }
    .card, .panel { background: #1a1d24; border-color: #2b303a; }
    .bar { background: #2b303a; }
    button { background: #1a1d24; border-color: #3a4150; color: #e5e7eb; }
  }
</style>
</head>
<body>
<main>
  <h1>immich-omni</h1>
  <div class="sub">中文描述 + Qwen3-Embedding-8B · 1024 维 · pgvector</div>

  <div class="panel">
    <div class="status">
      <span class="dot" id="dot"></span>
      <strong id="state">加载中…</strong>
      <span class="sub" id="heartbeat"></span>
    </div>
    <div class="row"><span>描述进度</span><span id="descPct">-</span></div>
    <div class="bar"><span id="descBar"></span></div>
    <div class="row"><span>向量进度</span><span id="embPct">-</span></div>
    <div class="bar vec"><span id="embBar"></span></div>
    <div style="margin-top:16px; display:flex; gap:10px;">
      <button id="pause" class="danger">暂停</button>
      <button id="resume" class="primary">继续</button>
    </div>
    <div id="err"></div>
  </div>

  <div class="cards">
    <div class="card"><div class="k">总数</div><div class="v" id="total">-</div></div>
    <div class="card"><div class="k">已描述</div><div class="v" id="described">-</div></div>
    <div class="card"><div class="k">已向量化</div><div class="v" id="embedded">-</div></div>
    <div class="card"><div class="k">待处理</div><div class="v" id="pending">-</div></div>
    <div class="card"><div class="k">失败</div><div class="v" id="failed">-</div></div>
  </div>

  <div class="panel">
    <div class="row"><span>最近 30 分钟平均速度</span><span id="rate">-</span></div>
    <div class="row" style="margin-top:6px"><span>预计剩余时间</span><span id="eta">-</span></div>
  </div>
</main>
<script>
const $ = (id) => document.getElementById(id);
let paused = null;

function fmtDuration(sec) {
  if (sec == null) return '-';
  sec = Math.max(0, Math.round(sec));
  const d = Math.floor(sec / 86400);
  const h = Math.floor((sec % 86400) / 3600);
  const m = Math.floor((sec % 3600) / 60);
  if (d > 0) return `${d} 天 ${h} 小时`;
  if (h > 0) return `${h} 小时 ${m} 分`;
  return `${m} 分`;
}

async function refresh() {
  try {
    const r = await fetch('/api/status', {cache: 'no-store'});
    if (!r.ok) throw new Error('HTTP ' + r.status);
    const s = await r.json();
    const described = s.described + s.embedded;
    const descPct = s.total ? described / s.total * 100 : 0;
    const embPct = s.total ? s.embedded / s.total * 100 : 0;
    $('total').textContent = s.total.toLocaleString();
    $('described').textContent = described.toLocaleString();
    $('embedded').textContent = s.embedded.toLocaleString();
    $('pending').textContent = s.pending.toLocaleString();
    $('failed').textContent = s.failed.toLocaleString();
    $('descPct').textContent = descPct.toFixed(2) + '%';
    $('embPct').textContent = embPct.toFixed(2) + '%';
    $('descBar').style.width = descPct + '%';
    $('embBar').style.width = embPct + '%';
    $('rate').textContent = s.rate_per_min.toFixed(2) + ' 张/分钟';
    $('eta').textContent = s.paused ? '已暂停' : fmtDuration(s.eta_seconds);
    const dot = $('dot');
    dot.className = 'dot';
    if (!s.worker_alive) { dot.classList.add('bad'); $('state').textContent = 'worker 未运行'; }
    else if (s.paused) { dot.classList.add('pause'); $('state').textContent = '已暂停'; }
    else { dot.classList.add('ok'); $('state').textContent = '运行中'; }
    $('heartbeat').textContent = s.worker_seen_at ? '心跳 ' + new Date(s.worker_seen_at).toLocaleTimeString() : '';
    paused = s.paused;
    $('pause').disabled = s.paused;
    $('resume').disabled = !s.paused;
    $('err').textContent = '';
  } catch (e) {
    $('err').textContent = '刷新失败: ' + e.message;
  }
}

async function toggle(action) {
  try {
    const r = await fetch('/api/' + action, {method: 'POST'});
    if (!r.ok) throw new Error('HTTP ' + r.status);
    await refresh();
  } catch (e) { $('err').textContent = e.message; }
}

$('pause').onclick = () => toggle('pause');
$('resume').onclick = () => toggle('resume');
refresh();
setInterval(refresh, 2000);
</script>
</body>
</html>
"""


class Handler(BaseHTTPRequestHandler):
    cfg: Config

    def log_message(self, *_: object) -> None:  # keep the container logs clean
        return

    def _send(self, status: int, body: bytes, content_type: str) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _json(self, status: int, data: dict) -> None:
        self._send(status, json.dumps(data).encode(), "application/json")

    def do_GET(self) -> None:  # noqa: N802 - http.server API
        try:
            if self.path == "/":
                self._send(200, PAGE.encode(), "text/html; charset=utf-8")
            elif self.path.startswith("/api/status"):
                with db.connect(self.cfg) as conn:
                    self._json(200, control.snapshot(conn))
            else:
                self._json(404, {"error": "not found"})
        except Exception as error:  # noqa: BLE001 - surface to the UI
            self._json(500, {"error": str(error)})

    def do_POST(self) -> None:  # noqa: N802 - http.server API
        try:
            if self.path == "/api/pause":
                with db.connect(self.cfg) as conn:
                    control.set_paused(conn, True)
                self._json(200, {"paused": True})
            elif self.path == "/api/resume":
                with db.connect(self.cfg) as conn:
                    control.set_paused(conn, False)
                self._json(200, {"paused": False})
            else:
                self._json(404, {"error": "not found"})
        except Exception as error:  # noqa: BLE001
            self._json(500, {"error": str(error)})


def serve(cfg: Config, port: int | None = None) -> None:
    port = port or int(os.environ.get("WEB_PORT", "8090"))
    Handler.cfg = cfg
    server = ThreadingHTTPServer(("0.0.0.0", port), Handler)
    print(f"web UI listening on http://0.0.0.0:{port}", flush=True)
    server.serve_forever()
