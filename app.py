from __future__ import annotations

import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

from .config import load_config, save_config
from .custom_macros import AHKParseError, CustomMacroManager
from .macros import MacroManager
from .pixel import PixelMacro
from .server import RobloxServer


ROOT = Path(__file__).resolve().parent.parent
FRONTEND = ROOT / "index.html"
ASSETS = ROOT / "assets"
PORT = 8765


class Backend:
    def __init__(self):
        self.started_at = time.time()
        self.logs: list[str] = []
        self.config = load_config()

        self.macros = MacroManager(self.log)
        self.pixel = PixelMacro(self.log)
        self.roblox = RobloxServer(self.log)
        self.custom_macros = CustomMacroManager(self.log)

        self._load_state()
        self.macros.start_listening()

    def log(self, message: str):
        stamp = time.strftime("%H:%M:%S")
        line = f"[{stamp}] {message}"
        self.logs.append(line)
        del self.logs[:-100]
        print(line)

    def _load_state(self):
        pixel = self.config.get("pixel", {})
        self.pixel.update(**pixel)

        macros = self.config.get("macros", {})
        for name in ("r_x", "f_x", "spam_r"):
            enabled = bool(macros.get(name, {}).get("enabled", False))
            self.macros.set_enabled(name, enabled)

        self.custom_macros.load(self.config.get("custom_macros", {}))

    def save(self):
        self.config["macros"] = {
            name: {"enabled": state}
            for name, state in self.macros.enabled.items()
        }
        self.config["pixel"] = self.pixel.settings.__dict__.copy()
        self.config["custom_macros"] = self.custom_macros.dump()
        save_config(self.config)

    def status(self):
        return {
            "ok": True,
            "version": "0.1.0",
            "uptime": int(time.time() - self.started_at),
            "modules": {
                "macros": self.macros.available,
                "pixel": self.pixel.available,
                "server": True,
                "boost": True,
                "custom_macros": self.custom_macros.available,
            },
            "macro_state": dict(self.macros.enabled),
            "custom_macro_count": len(self.custom_macros.macros),
            "pixel": {
                **self.pixel.settings.__dict__,
                "active": self.pixel.active,
                "scanning": self.pixel.scanning,
            },
            "logs": self.logs[-30:],
        }

    def shutdown(self):
        self.macros.stop_all()
        self.pixel.stop()
        self.custom_macros.stop_all()


BACKEND = Backend()


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *_):
        pass

    def _send(self, code: int, payload):
        raw = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(raw)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(raw)

    def _body(self):
        length = int(self.headers.get("Content-Length", 0))
        raw = self.rfile.read(length) if length else b"{}"
        return json.loads(raw.decode("utf-8"))

    def do_GET(self):
        path = urlparse(self.path).path

        if path == "/":
            html = FRONTEND.read_text(encoding="utf-8")
            bridge = (
                '<script src="/bridge.js"></script>'
            )
            html = html.replace("</body>", bridge + "</body>")
            raw = html.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)
            return

        if path.startswith("/assets/"):
            asset_path = ASSETS / path[len("/assets/"):]
            if asset_path.is_file() and asset_path.resolve().parent == ASSETS.resolve():
                raw = asset_path.read_bytes()
                content_type = "image/x-icon" if asset_path.suffix == ".ico" else "image/png"
                self.send_response(200)
                self.send_header("Content-Type", content_type)
                self.send_header("Content-Length", str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)
                return
            self._send(404, {"ok": False, "error": "Not found"})
            return

        if path == "/bridge.js":
            raw = BRIDGE_JS.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/javascript; charset=utf-8")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)
            return

        if path == "/api/status":
            self._send(200, BACKEND.status())
            return

        if path == "/api/modules":
            self._send(200, BACKEND.status()["modules"])
            return

        if path == "/api/logs":
            self._send(200, {"logs": BACKEND.logs[-50:]})
            return

        if path == "/api/custom-macros":
            self._send(200, {"ok": True, "macros": BACKEND.custom_macros.as_list()})
            return

        self._send(404, {"ok": False, "error": "Not found"})

    def do_POST(self):
        path = urlparse(self.path).path

        try:
            data = self._body()

            if path == "/api/macros/set":
                name = str(data["name"])
                enabled = bool(data["enabled"])
                BACKEND.macros.set_enabled(name, enabled)
                BACKEND.save()
                self._send(200, BACKEND.status())
                return

            if path == "/api/macros/stop":
                BACKEND.macros.stop_all()
                BACKEND.save()
                self._send(200, BACKEND.status())
                return

            if path == "/api/pixel/config":
                BACKEND.pixel.update(**data)
                BACKEND.save()
                self._send(200, BACKEND.status())
                return

            if path == "/api/pixel/start":
                BACKEND.pixel.start()
                self._send(200, BACKEND.status())
                return

            if path == "/api/pixel/stop":
                BACKEND.pixel.stop()
                self._send(200, BACKEND.status())
                return

            if path == "/api/server/join":
                uri = BACKEND.roblox.join(str(data["link"]))
                self._send(200, {"ok": True, "uri": uri})
                return

            if path == "/api/custom-macros/create":
                macro = BACKEND.custom_macros.create(str(data["name"]), str(data["code"]))
                BACKEND.save()
                self._send(200, {"ok": True, "id": macro.id})
                return

            if path == "/api/custom-macros/set":
                BACKEND.custom_macros.set_enabled(str(data["id"]), bool(data["enabled"]))
                BACKEND.save()
                self._send(200, {"ok": True})
                return

            if path == "/api/custom-macros/delete":
                BACKEND.custom_macros.delete(str(data["id"]))
                BACKEND.save()
                self._send(200, {"ok": True})
                return

            self._send(404, {"ok": False, "error": "Not found"})

        except AHKParseError as exc:
            self._send(400, {"ok": False, "error": str(exc)})
        except Exception as exc:
            self._send(400, {"ok": False, "error": str(exc)})


BRIDGE_JS = r"""
(() => {
  const api = async (url, options = {}) => {
    const res = await fetch(url, {
      headers: {'Content-Type': 'application/json'},
      ...options
    });
    return await res.json();
  };

  const post = (url, data = {}) =>
    api(url, {method: 'POST', body: JSON.stringify(data)});

  const names = {
    r_x: 'R → X',
    f_x: 'F → X',
    spam_r: 'Spam R'
  };

  
  const ixwStyle = document.createElement('style');
  ixwStyle.textContent = `
    .ixw-modal-overlay{position:fixed;inset:0;background:rgba(5,2,2,.85);
      backdrop-filter:blur(2px);display:flex;align-items:center;justify-content:center;z-index:999;}
    .ixw-modal{background:var(--bg-panel);border:1px solid var(--red-dim);
      box-shadow:0 0 30px rgba(255,26,43,.25);width:min(560px,92vw);padding:24px;
      font-family:'Share Tech Mono',monospace;color:var(--text-hi);}
    .ixw-modal h3{font-family:'Orbitron',sans-serif;color:var(--red-core);
      text-shadow:var(--glow);margin:0 0 14px;font-size:1rem;letter-spacing:1px;}
    .ixw-modal input, .ixw-modal textarea{width:100%;background:#070303;
      border:1px solid var(--line);color:var(--text-hi);font-family:'Share Tech Mono',monospace;
      padding:10px;font-size:.85rem;}
    .ixw-modal textarea{min-height:220px;resize:vertical;white-space:pre;}
    .ixw-modal .row{display:flex;justify-content:flex-end;gap:10px;margin-top:16px;}
    .ixw-modal button{all:unset;cursor:pointer;padding:9px 16px;font-size:.78rem;
      letter-spacing:1px;border:1px solid var(--line);color:var(--text-mid);}
    .ixw-modal button:hover{border-color:var(--red-core);color:var(--red-core);}
    .ixw-modal button.primary{border-color:var(--red-deep);color:var(--red-core);}
    .ixw-modal .err{color:var(--red-core);font-size:.75rem;margin-top:8px;min-height:1em;}
    .ixw-modal .hint{color:var(--text-low);font-size:.7rem;margin-top:8px;line-height:1.5;}
    .macro-card{position:relative;}
    .macro-card .del{position:absolute;top:6px;right:8px;color:var(--text-low);font-size:.7rem;}
    .macro-card .del:hover{color:var(--red-core);}
  `;
  document.head.appendChild(ixwStyle);

  function openModal(innerHTML) {
    closeModal();
    const overlay = document.createElement('div');
    overlay.className = 'ixw-modal-overlay';
    overlay.id = 'ixwOverlay';
    overlay.innerHTML = `<div class="ixw-modal">${innerHTML}</div>`;
    overlay.addEventListener('click', (e) => { if (e.target === overlay) closeModal(); });
    document.body.appendChild(overlay);
    return overlay;
  }

  function closeModal() {
    const existing = document.getElementById('ixwOverlay');
    if (existing) existing.remove();
  }

  function startCreateMacroFlow() {
    const overlay = openModal(`
      <h3>&gt; NOVO MACRO — passo 1/2</h3>
      <input type="text" id="ixwName" placeholder="nome do macro" autocomplete="off">
      <div class="err" id="ixwErr1"></div>
      <div class="row">
        <button id="ixwCancel1">cancelar</button>
        <button class="primary" id="ixwNext">avançar</button>
      </div>
    `);
    const nameInput = overlay.querySelector('#ixwName');
    nameInput.focus();
    overlay.querySelector('#ixwCancel1').addEventListener('click', closeModal);
    overlay.querySelector('#ixwNext').addEventListener('click', () => {
      const name = nameInput.value.trim();
      if (!name) {
        overlay.querySelector('#ixwErr1').textContent = 'digite um nome para o macro.';
        return;
      }
      stepTwoMacroCode(name);
    });
    nameInput.addEventListener('keydown', (e) => {
      if (e.key === 'Enter') overlay.querySelector('#ixwNext').click();
    });
  }

  function stepTwoMacroCode(name) {
    const overlay = openModal(`
      <h3>&gt; NOVO MACRO — passo 2/2</h3>
      <textarea id="ixwCode" placeholder="XButton1::
Send, r
Sleep, 50
Send, x
return" spellcheck="false"></textarea>
      <div class="hint">
        cole o macro no formato AutoHotkey (AHK): primeira linha é o gatilho
        (ex: F1::, XButton1::, ^j::), depois comandos <b>Send</b>, <b>Sleep</b>,
        <b>Click</b> ou <b>Loop { }</b>, finalizando com <b>return</b>.
      </div>
      <div class="err" id="ixwErr2"></div>
      <div class="row">
        <button id="ixwBack">voltar</button>
        <button class="primary" id="ixwSave">salvar</button>
      </div>
    `);
    overlay.querySelector('#ixwCode').focus();
    overlay.querySelector('#ixwBack').addEventListener('click', () => startCreateMacroFlow());
    overlay.querySelector('#ixwSave').addEventListener('click', async () => {
      const code = overlay.querySelector('#ixwCode').value;
      const errEl = overlay.querySelector('#ixwErr2');
      errEl.textContent = 'validando...';
      try {
        const res = await post('/api/custom-macros/create', { name, code });
        if (!res.ok) {
          errEl.textContent = res.error || 'código inválido.';
          return;
        }
        closeModal();
        refreshOptions();
      } catch (e) {
        errEl.textContent = 'falha ao conectar com o backend.';
      }
    });
  }

  async function refreshOptions() {
    const grid = document.querySelector('#view-options .options-grid');
    if (!grid) return;

    let data;
    try {
      data = await api('/api/custom-macros');
    } catch (e) {
      return;
    }

    const cards = (data.macros || []).map(m => `
      <div class="option-slot macro-card" data-id="${m.id}" style="cursor:pointer">
        <span class="del" data-del="${m.id}" title="remover">✕</span>
        <div class="plus">${m.enabled ? '●' : '○'}</div>
        <strong>${m.name}</strong>
        <small>${m.enabled ? 'ATIVO' : 'desativado'}</small>
      </div>
    `).join('');

    grid.innerHTML = cards + `
      <div class="option-slot" id="ixwAddSlot" style="cursor:pointer">
        <div class="plus">+</div>novo macro
      </div>
    `;

    const addSlot = grid.querySelector('#ixwAddSlot');
    if (addSlot) addSlot.addEventListener('click', startCreateMacroFlow);

    grid.querySelectorAll('.macro-card').forEach(card => {
      card.addEventListener('click', async (e) => {
        if (e.target.dataset.del) return;
        const id = card.dataset.id;
        const macro = (data.macros || []).find(m => m.id === id);
        if (!macro) return;
        await post('/api/custom-macros/set', { id, enabled: !macro.enabled });
        refreshOptions();
      });
    });

    grid.querySelectorAll('[data-del]').forEach(btn => {
      btn.addEventListener('click', async (e) => {
        e.stopPropagation();
        await post('/api/custom-macros/delete', { id: btn.dataset.del });
        refreshOptions();
      });
    });
  }

  function ensureMacroUI() {
    if (document.getElementById('view-macros')) return;

    const nav = document.querySelector('nav');
    const btn = document.createElement('button');
    btn.className = 'nav-btn';
    btn.dataset.view = 'macros';
    btn.innerHTML = '<span class="dot"></span> Macros';
    nav.appendChild(btn);

    const view = document.createElement('section');
    view.className = 'view';
    view.id = 'view-macros';
    view.innerHTML = `
      <header class="hero">
        <div>
          <h1>MAC<span>ROS</span></h1>
          <p class="sub">automação de entrada // Python backend</p>
        </div>
      </header>
      <div class="options-grid" id="macroGrid"></div>
      <p class="options-note">
        XButton1 executa a macro selecionada R → X ou F → X.
        XButton2 alterna o Spam R.
      </p>
    `;
    document.querySelector('main').insertBefore(
      view, document.querySelector('footer.credit')
    );

    btn.addEventListener('click', () => activateView('macros'));
  }

  function activateView(name) {
    document.querySelectorAll('.nav-btn').forEach(b =>
      b.classList.toggle('active', b.dataset.view === name)
    );
    document.querySelectorAll('.view').forEach(v =>
      v.classList.toggle('active', v.id === 'view-' + name)
    );
  }

  async function refresh() {
    ensureMacroUI();
    const state = await api('/api/status');

    const grid = document.getElementById('macroGrid');
    if (grid) {
      grid.innerHTML = Object.entries(names).map(([key, label]) => {
        const on = !!state.macro_state[key];
        return `
          <button class="option-slot macro-card" data-macro="${key}"
                  style="cursor:pointer">
            <div class="plus">${on ? '●' : '+'}</div>
            <strong>${label}</strong>
            <small>${on ? 'ATIVO' : 'desativado'}</small>
          </button>
        `;
      }).join('');

      grid.querySelectorAll('[data-macro]').forEach(card => {
        card.addEventListener('click', async () => {
          const key = card.dataset.macro;
          await post('/api/macros/set', {
            name: key,
            enabled: !state.macro_state[key]
          });
          refresh();
        });
      });
    }

    const moduleValues = document.querySelectorAll('.stats .stat .value');
    if (moduleValues[1]) {
      const modules = Object.values(state.modules).filter(Boolean).length;
      moduleValues[1].textContent =
        `${modules} / ${Object.keys(state.modules).length}`;
    }

    const consoleEl = document.getElementById('console');
    if (consoleEl && state.logs) {
      consoleEl.innerHTML = state.logs.map(
        line => `<div class="line" style="opacity:1">${line}</div>`
      ).join('') + '<span class="cursor"></span>';
    }
  }

  window.InfinityxWare = {
    api,
    post,
    refresh,
    activateView
  };

  document.addEventListener('DOMContentLoaded', () => {
    refresh();
    refreshOptions();
    setInterval(refresh, 1000);

    const optionsBtn = document.querySelector('.nav-btn[data-view="options"]');
    if (optionsBtn) optionsBtn.addEventListener('click', refreshOptions);
  });
})();
"""


def main():
    server = ThreadingHTTPServer(("127.0.0.1", PORT), Handler)
    print(f"InfinityxWare backend: http://127.0.0.1:{PORT}")


    server_thread = threading.Thread(target=server.serve_forever, daemon=True)
    server_thread.start()

    try:
        import webview
    except ImportError:
        print(
            "pywebview não está instalado. Rode: "
            "python -m pip install -r requirements.txt"
        )
        _shutdown(server)
        return

    window_kwargs = dict(
        title="InfinityxWare",
        url=f"http://127.0.0.1:{PORT}",
        width=1280,
        height=820,
        min_size=(960, 620),
        background_color="#050202",
    )

    icon_path = ASSETS / "icon.ico"
    if icon_path.exists():
        window_kwargs["icon"] = str(icon_path)

    try:
        webview.create_window(**window_kwargs)
    except TypeError:
        # Installed pywebview version doesn't support the icon kwarg.
        window_kwargs.pop("icon", None)
        webview.create_window(**window_kwargs)

    webview.start()

    _shutdown(server)


def _shutdown(server: ThreadingHTTPServer):
    BACKEND.shutdown()
    server.shutdown()
    server.server_close()


if __name__ == "__main__":
    main()
