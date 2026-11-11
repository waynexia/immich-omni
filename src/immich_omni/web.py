from __future__ import annotations

import json
import os
import re
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

import httpx

from . import control, db, search as search_mod
from .config import Config

UUID_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", re.I
)

PAGE = """<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>immich-omni</title>
<style>
  :root { color-scheme: light dark; }
  * { box-sizing: border-box; }
  body { font-family: system-ui, -apple-system, "Segoe UI", sans-serif; margin: 0;
         background: #f6f7f9; color: #1d1f23; }
  main { max-width: 1080px; margin: 0 auto; padding: 32px 20px 60px; }
  h1 { font-size: 20px; margin: 0 0 4px; }
  .sub { color: #6b7280; font-size: 13px; }
  .tabs { display: flex; gap: 6px; margin: 20px 0 18px; border-bottom: 1px solid #e5e7eb; }
  .tab { border: 0; background: transparent; color: #6b7280; font: inherit; padding: 10px 14px;
         cursor: pointer; border-bottom: 2px solid transparent; margin-bottom: -1px; }
  .tab.active { color: #3b82f6; border-bottom-color: #3b82f6; font-weight: 600; }
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
  #search-form { display: flex; gap: 10px; margin-bottom: 10px; }
  #search-q { flex: 1; font: inherit; padding: 10px 14px; border-radius: 8px;
              border: 1px solid #d1d5db; background: #fff; color: inherit; }
  .grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(190px, 1fr));
          gap: 14px; margin-top: 16px; }
  .result { background: #fff; border: 1px solid #e5e7eb; border-radius: 10px;
            overflow: hidden; cursor: pointer; transition: transform .12s, box-shadow .12s; }
  .result:hover { transform: translateY(-2px); box-shadow: 0 8px 20px rgba(0,0,0,.08); }
  .result img { width: 100%; aspect-ratio: 1; object-fit: cover; display: block; background: #e5e7eb; }
  .result-body { padding: 9px 10px 11px; }
  .result-name { font-size: 13px; font-weight: 600; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .result-ranks { font-size: 11px; color: #6b7280; margin: 3px 0 5px; }
  .result-desc { font-size: 12px; color: #4b5563; margin: 0; display: -webkit-box;
                 -webkit-line-clamp: 3; -webkit-box-orient: vertical; overflow: hidden; }
  #modal { position: fixed; inset: 0; background: rgba(0,0,0,.82); display: flex;
           align-items: center; justify-content: center; padding: 24px; z-index: 50; }
  #modal[hidden] { display: none; }
  #modal img { max-width: 92vw; max-height: 86vh; object-fit: contain; border-radius: 6px; }
  #modal-cap { color: #e5e7eb; font-size: 13px; margin-top: 10px; text-align: center; }
  #modal-hint { color: #9ca3af; text-align: center; margin-top: 4px; }
  #modal-close { position: fixed; top: 18px; right: 22px; font-size: 26px; line-height: 1;
                 background: transparent; border: 0; color: #fff; cursor: pointer; }
  @media (prefers-color-scheme: dark) {
    body { background: #111318; color: #e5e7eb; }
    .card, .panel, .result, #search-q { background: #1a1d24; border-color: #2b303a; }
    .bar { background: #2b303a; }
    .tabs { border-color: #2b303a; }
    button { background: #1a1d24; border-color: #3a4150; color: #e5e7eb; }
    .result-desc { color: #9ca3af; }
  }
</style>
</head>
<body>
<main>
  <h1>immich-omni</h1>
  <div class="sub">中文描述 + Qwen3-Embedding-8B · 1024 维 · pgvector</div>

  <nav class="tabs">
    <button class="tab active" data-tab="progress">进度</button>
    <button class="tab" data-tab="search">搜索</button>
  </nav>

  <section id="tab-progress">
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
        <button id="retry">重试失败</button>
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
  </section>

  <section id="tab-search" hidden>
    <form id="search-form">
      <input id="search-q" type="search" placeholder="描述你想找的画面，例如：雨天的城市街道" autocomplete="off">
      <button class="primary" type="submit">搜索</button>
    </form>
    <div id="search-meta" class="sub">描述向量 + Immich CLIP，RRF 融合</div>
    <div id="results" class="grid"></div>
  </section>
</main>

<div id="modal" hidden>
  <button id="modal-close" aria-label="关闭">×</button>
  <div>
    <img id="modal-img" alt="">
    <div id="modal-cap"></div>
    <div class="sub" id="modal-hint">原图需要 asset.download 权限；HEIC 或权限不足时自动显示 preview</div>
  </div>
</div>

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
$('retry').onclick = async () => {
  try {
    const r = await fetch('/api/retry', {method: 'POST'});
    if (!r.ok) throw new Error('HTTP ' + r.status);
    const d = await r.json();
    $('err').textContent = `已重置 ${d.retried} 条失败任务`;
    await refresh();
  } catch (e) { $('err').textContent = e.message; }
};

document.querySelectorAll('.tab').forEach((tab) => {
  tab.onclick = () => {
    document.querySelectorAll('.tab').forEach((t) => t.classList.toggle('active', t === tab));
    $('tab-progress').hidden = tab.dataset.tab !== 'progress';
    $('tab-search').hidden = tab.dataset.tab !== 'search';
    if (tab.dataset.tab === 'search') $('search-q').focus();
  };
});

function rankText(item) {
  const qwen = item.qwen_rank ? `描述 #${item.qwen_rank}` :
    '无描述向量';
  const clip = item.clip_rank ? `CLIP #${item.clip_rank}` : 'CLIP —';
  return `RRF ${item.score.toFixed(4)} · ${qwen} · ${clip}`;
}

function makeCard(item) {
  const card = document.createElement('article');
  card.className = 'result';
  const img = document.createElement('img');
  img.loading = 'lazy';
  img.src = `/api/thumb/${item.asset_id}?size=thumbnail`;
  img.alt = item.file_name || '';
  const body = document.createElement('div');
  body.className = 'result-body';
  const name = document.createElement('div');
  name.className = 'result-name';
  name.textContent = item.file_name || item.asset_id.slice(0, 8);
  const ranks = document.createElement('div');
  ranks.className = 'result-ranks';
  ranks.textContent = rankText(item);
  const desc = document.createElement('p');
  desc.className = 'result-desc';
  desc.textContent = item.description || '（暂无描述向量）';
  body.append(name, ranks, desc);
  card.append(img, body);
  card.onclick = () => openModal(item);
  return card;
}

function renderResults(data) {
  const grid = $('results');
  grid.replaceChildren(...data.results.map(makeCard));
  const t = data.timings;
  const clipNote = data.clip_error ? ` · CLIP 失败: ${data.clip_error}` : '';
  $('search-meta').textContent =
    `${data.results.length} 条结果 · 描述向量 ${t.qwen_ms.toFixed(0)}ms · ` +
    `Immich CLIP ${t.clip_ms.toFixed(0)}ms · 融合 ${t.total_ms.toFixed(0)}ms · ` +
    `权重 ${data.weights.qwen}:${data.weights.clip}` + clipNote;
}

$('search-form').onsubmit = async (event) => {
  event.preventDefault();
  const query = $('search-q').value.trim();
  if (!query) return;
  $('search-meta').textContent = '搜索中…';
  $('results').replaceChildren();
  try {
    const started = performance.now();
    const r = await fetch(`/api/search?q=${encodeURIComponent(query)}&limit=60`);
    if (!r.ok) throw new Error('HTTP ' + r.status);
    const data = await r.json();
    renderResults(data);
    console.debug('search wall ms', performance.now() - started);
  } catch (e) {
    $('search-meta').textContent = '搜索失败: ' + e.message;
  }
};

function openModal(item) {
  const modal = $('modal');
  const img = $('modal-img');
  modal.hidden = false;
  $('modal-cap').textContent = item.file_name || item.asset_id;
  img.onerror = () => { img.src = `/api/thumb/${item.asset_id}?size=preview`; };
  img.src = `/api/original/${item.asset_id}`;
}

$('modal-close').onclick = () => { $('modal').hidden = true; };
$('modal').onclick = (event) => { if (event.target === $('modal')) $('modal').hidden = true; };
document.addEventListener('keydown', (event) => { if (event.key === 'Escape') $('modal').hidden = true; });

refresh();
setInterval(refresh, 2000);
</script>
</body>
</html>
"""


class Handler(BaseHTTPRequestHandler):
    cfg: Config

    def log_message(self, *_: object) -> None:
        return

    def _send(self, status: int, body: bytes, content_type: str, extra: dict | None = None) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        if not (extra and "Cache-Control" in extra):
            self.send_header("Cache-Control", "no-store")
        for key, value in (extra or {}).items():
            self.send_header(key, value)
        self.end_headers()
        self.wfile.write(body)

    def _json(self, status: int, data: dict) -> None:
        self._send(status, json.dumps(data).encode(), "application/json")

    def _proxy(self, asset_id: str, size: str | None, original: bool) -> None:
        if not UUID_RE.match(asset_id):
            self._json(404, {"error": "invalid asset id"})
            return
        base = f"{self.cfg.immich_url}/api/assets/{asset_id}"
        headers = {"x-api-key": self.cfg.immich_api_key}
        try:
            with httpx.Client(timeout=httpx.Timeout(180.0, connect=30.0)) as client:
                fallback: str | None = None
                if original:
                    response = client.get(f"{base}/original", headers=headers)
                    if response.status_code == 403:
                        # The key may lack asset.download; show the preview instead.
                        response = client.get(
                            f"{base}/thumbnail",
                            params={"size": "preview"},
                            headers=headers,
                        )
                        fallback = "preview-permission"
                else:
                    response = client.get(
                        f"{base}/thumbnail",
                        params={"size": size or "thumbnail"},
                        headers=headers,
                    )
                if response.status_code != 200:
                    self._json(response.status_code, {"error": "immich request failed"})
                    return
                body = response.content
                content_type = response.headers.get(
                    "content-type", "application/octet-stream"
                )
                if (
                    original
                    and content_type.split(";")[0].lower()
                    in {"image/heic", "image/heif", "image/heic-sequence"}
                ):
                    preview = client.get(
                        f"{base}/thumbnail",
                        params={"size": "preview"},
                        headers=headers,
                    )
                    if preview.status_code == 200:
                        body = preview.content
                        content_type = preview.headers.get("content-type", "image/jpeg")
                        fallback = "preview-heic"
                extra: dict[str, str] = {"Cache-Control": "private, max-age=3600"}
                if fallback:
                    extra["X-Immich-Fallback"] = fallback
                self._send(200, body, content_type, extra)
        except httpx.HTTPError as error:
            self._json(502, {"error": str(error)})

    def do_GET(self) -> None:  # noqa: N802 - http.server API
        try:
            parsed = urlparse(self.path)
            if parsed.path == "/":
                self._send(200, PAGE.encode(), "text/html; charset=utf-8")
            elif parsed.path == "/api/status":
                with db.connect(self.cfg) as conn:
                    self._json(200, control.snapshot(conn))
            elif parsed.path == "/api/search":
                query = parse_qs(parsed.query).get("q", [""])[0].strip()
                if not query:
                    self._json(400, {"error": "missing q"})
                    return
                try:
                    limit = int(parse_qs(parsed.query).get("limit", ["50"])[0])
                except ValueError:
                    limit = 50
                limit = max(1, min(limit, 100))
                self._json(200, search_mod.search(self.cfg, query, limit))
            elif parsed.path.startswith("/api/thumb/"):
                asset_id = parsed.path[len("/api/thumb/") :]
                size = parse_qs(parsed.query).get("size", ["thumbnail"])[0]
                if size not in {"thumbnail", "preview"}:
                    size = "thumbnail"
                self._proxy(asset_id, size, original=False)
            elif parsed.path.startswith("/api/original/"):
                asset_id = parsed.path[len("/api/original/") :]
                self._proxy(asset_id, None, original=True)
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
            elif self.path == "/api/retry":
                with db.connect(self.cfg) as conn:
                    retried = control.retry_failed(conn)
                self._json(200, {"retried": retried})
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
