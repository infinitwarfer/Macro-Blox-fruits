from __future__ import annotations

import re
import threading
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Callable

try:
    from pynput import keyboard, mouse
    from pynput.keyboard import Key, Controller as KeyboardController
    from pynput.mouse import Button, Controller as MouseController
except ImportError:  # pragma: no cover - only hit when pynput is missing
    keyboard = None
    mouse = None
    Key = None
    KeyboardController = None
    Button = None
    MouseController = None


class AHKParseError(Exception):
    """Raised when the pasted AHK-style code can't be understood."""


# ---------------------------------------------------------------------------
# Key name mapping (AHK-style names -> pynput special key names)
# ---------------------------------------------------------------------------

SPECIAL_KEYS = {
    "enter": "enter", "return": "enter",
    "tab": "tab",
    "space": "space",
    "esc": "esc", "escape": "esc",
    "up": "up", "down": "down", "left": "left", "right": "right",
    "backspace": "backspace", "bs": "backspace",
    "delete": "delete", "del": "delete",
    "insert": "insert", "ins": "insert",
    "home": "home", "end": "end",
    "pgup": "page_up", "pgdn": "page_down",
    "shift": "shift", "lshift": "shift", "rshift": "shift",
    "ctrl": "ctrl", "control": "ctrl", "lctrl": "ctrl", "rctrl": "ctrl",
    "alt": "alt", "lalt": "alt", "ralt": "alt",
    "lwin": "cmd", "rwin": "cmd", "win": "cmd",
}
for _i in range(1, 13):
    SPECIAL_KEYS[f"f{_i}"] = f"f{_i}"

MOUSE_TRIGGERS = {
    "lbutton": "left", "rbutton": "right", "mbutton": "middle",
    "xbutton1": "x1", "xbutton2": "x2",
}

MODIFIER_PREFIX = {"^": "ctrl", "!": "alt", "+": "shift", "#": "cmd"}


# ---------------------------------------------------------------------------
# Trigger parsing (the "F1::" / "XButton1::" / "^j::" part)
# ---------------------------------------------------------------------------

def parse_trigger(raw: str) -> dict:
    raw = raw.strip()
    if not raw:
        raise AHKParseError("Hotkey vazio antes de '::'.")

    mods: list[str] = []
    while raw and raw[0] in MODIFIER_PREFIX:
        mods.append(MODIFIER_PREFIX[raw[0]])
        raw = raw[1:]

    key = raw.strip().lower()
    if not key:
        raise AHKParseError("Hotkey inválido: falta a tecla/botão.")

    if key in MOUSE_TRIGGERS:
        return {"type": "mouse", "button": MOUSE_TRIGGERS[key], "mods": mods}

    return {"type": "keyboard", "key": key, "mods": mods}


# ---------------------------------------------------------------------------
# Action model
# ---------------------------------------------------------------------------

@dataclass
class Action:
    kind: str
    args: dict = field(default_factory=dict)


def _tokenize_send(text: str) -> list[tuple[str, str]]:
    tokens: list[tuple[str, str]] = []
    i = 0
    while i < len(text):
        ch = text[i]
        if ch == "{":
            j = text.find("}", i)
            if j == -1:
                raise AHKParseError("Chave '{' sem fechamento em um comando Send.")
            tokens.append(("special", text[i + 1:j].strip()))
            i = j + 1
        elif ch.isspace():
            i += 1
        else:
            tokens.append(("char", ch))
            i += 1
    return tokens


def _parse_send_tokens(tokens: list[tuple[str, str]]) -> list[Action]:
    actions: list[Action] = []
    for kind, val in tokens:
        if kind == "char":
            actions.append(Action("type_char", {"char": val}))
            continue

        parts = val.lower().split()
        if not parts:
            continue
        name, mode = parts[0], (parts[1] if len(parts) > 1 else None)
        mapped = SPECIAL_KEYS.get(name, name if len(name) == 1 else name)

        if mode == "down":
            actions.append(Action("key_down", {"key": mapped}))
        elif mode == "up":
            actions.append(Action("key_up", {"key": mapped}))
        else:
            actions.append(Action("key_tap", {"key": mapped}))
    return actions


LINE_RE = re.compile(r"^([A-Za-z]+)\s*,?\s*(.*)$")


def parse_body(lines: list[str]) -> list[Action]:
    actions: list[Action] = []
    i = 0
    while i < len(lines):
        line = lines[i].split(";", 1)[0].strip()
        i += 1
        if not line:
            continue

        low = line.lower()
        if low == "return":
            break

        if low.startswith("loop"):
            m = re.match(r"loop\s*,?\s*(\d+)", low)
            count = int(m.group(1)) if m else 1

            if i < len(lines) and lines[i].strip() == "{":
                i += 1
                block: list[str] = []
                depth = 1
                while i < len(lines) and depth > 0:
                    l = lines[i]
                    if l.strip() == "{":
                        depth += 1
                    elif l.strip() == "}":
                        depth -= 1
                        if depth == 0:
                            i += 1
                            break
                    block.append(l)
                    i += 1
                inner = parse_body(block)
                actions.append(Action("loop", {"count": count, "actions": inner}))
            continue

        m = LINE_RE.match(line)
        if not m:
            raise AHKParseError(f"Linha não reconhecida: \"{line}\"")

        cmd, rest = m.group(1).lower(), m.group(2).strip()

        if cmd == "send":
            actions.extend(_parse_send_tokens(_tokenize_send(rest)))
        elif cmd == "sleep":
            digits = re.sub(r"[^\d]", "", rest)
            actions.append(Action("sleep", {"ms": int(digits) if digits else 0}))
        elif cmd == "click":
            parts = [p.strip() for p in rest.split(",") if p.strip()]
            args: dict[str, Any] = {}
            if len(parts) >= 2:
                args["x"], args["y"] = int(parts[0]), int(parts[1])
            args["button"] = parts[2].lower() if len(parts) >= 3 else "left"
            actions.append(Action("click", args))
        elif cmd == "mousemove":
            parts = [p.strip() for p in rest.split(",") if p.strip()]
            if len(parts) >= 2:
                actions.append(Action("mousemove", {"x": int(parts[0]), "y": int(parts[1])}))
        else:
            raise AHKParseError(f"Comando não suportado: \"{cmd}\"")

    return actions


def parse_ahk(code: str) -> tuple[dict, list[Action]]:
    """Parse a small AHK-style subset: hotkey line + Send/Sleep/Click/Loop body."""
    raw_lines = code.replace("\r\n", "\n").split("\n")

    idx = 0
    while idx < len(raw_lines) and not raw_lines[idx].split(";", 1)[0].strip():
        idx += 1
    if idx >= len(raw_lines):
        raise AHKParseError("Código vazio.")

    first = raw_lines[idx].split(";", 1)[0]
    m = re.match(r"^\s*([^\s:][^:]*?)::(.*)$", first)
    if not m:
        raise AHKParseError(
            "A primeira linha precisa definir um hotkey, ex: F1:: ou XButton1::"
        )

    trigger = parse_trigger(m.group(1))
    inline_rest = m.group(2).strip()
    remaining = raw_lines[idx + 1:]
    body_lines = ([inline_rest] if inline_rest else []) + remaining

    actions = parse_body(body_lines)
    if not actions:
        raise AHKParseError("Nenhuma ação encontrada (use Send, Sleep, Click, Loop...).")

    return trigger, actions


# ---------------------------------------------------------------------------
# Runtime: stores macros and executes them when their trigger fires
# ---------------------------------------------------------------------------

@dataclass
class CustomMacro:
    id: str
    name: str
    code: str
    trigger: dict
    actions: list
    enabled: bool = True


class CustomMacroManager:
    def __init__(self, log: Callable[[str], None] | None = None):
        self.log = log or (lambda _msg: None)
        self.macros: dict[str, CustomMacro] = {}
        self._running: dict[str, bool] = {}

        self._keyboard_ctl = KeyboardController() if KeyboardController else None
        self._mouse_ctl = MouseController() if MouseController else None
        self._mouse_listener = None
        self._hotkeys = None

    @property
    def available(self) -> bool:
        return keyboard is not None and mouse is not None

    # -- persistence --------------------------------------------------

    def load(self, data: dict) -> None:
        for mid, entry in data.items():
            try:
                trigger, actions = parse_ahk(entry["code"])
                self.macros[mid] = CustomMacro(
                    id=mid,
                    name=entry.get("name", mid),
                    code=entry["code"],
                    trigger=trigger,
                    actions=actions,
                    enabled=bool(entry.get("enabled", True)),
                )
            except (AHKParseError, KeyError) as exc:
                self.log(f"Falha ao carregar macro salvo '{entry.get('name', mid)}': {exc}")
        self._rebuild_listeners()

    def dump(self) -> dict:
        return {
            m.id: {"name": m.name, "code": m.code, "enabled": m.enabled}
            for m in self.macros.values()
        }

    def as_list(self) -> list[dict]:
        return [
            {"id": m.id, "name": m.name, "enabled": m.enabled, "code": m.code}
            for m in self.macros.values()
        ]

    # -- CRUD -----------------------------------------------------------

    def create(self, name: str, code: str) -> CustomMacro:
        name = name.strip() or "macro sem nome"
        trigger, actions = parse_ahk(code)  # raises AHKParseError on bad input
        mid = uuid.uuid4().hex[:8]
        macro = CustomMacro(id=mid, name=name, code=code, trigger=trigger, actions=actions)
        self.macros[mid] = macro
        self._rebuild_listeners()
        self.log(f"Macro criado: {name}")
        return macro

    def delete(self, mid: str) -> None:
        macro = self.macros.pop(mid, None)
        self._rebuild_listeners()
        if macro:
            self.log(f"Macro removido: {macro.name}")

    def set_enabled(self, mid: str, enabled: bool) -> None:
        macro = self.macros.get(mid)
        if not macro:
            raise ValueError("Macro não encontrado.")
        macro.enabled = bool(enabled)
        self.log(f"{macro.name}: {'ON' if macro.enabled else 'OFF'}")

    # -- listeners --------------------------------------------------------

    def _rebuild_listeners(self) -> None:
        if not self.available:
            return

        if self._mouse_listener is not None:
            try:
                self._mouse_listener.stop()
            except Exception:
                pass
            self._mouse_listener = None

        if self._hotkeys is not None:
            try:
                self._hotkeys.stop()
            except Exception:
                pass
            self._hotkeys = None

        keyboard_map = {}
        for macro in self.macros.values():
            if macro.trigger["type"] == "keyboard":
                combo = "+".join(
                    [f"<{mod}>" for mod in macro.trigger["mods"]] + [macro.trigger["key"]]
                )
                keyboard_map[combo] = self._make_runner(macro.id)

        if keyboard_map:
            try:
                self._hotkeys = keyboard.GlobalHotKeys(keyboard_map)
                self._hotkeys.daemon = True
                self._hotkeys.start()
            except Exception as exc:
                self.log(f"Não foi possível registrar hotkeys: {exc}")
                self._hotkeys = None

        if any(m.trigger["type"] == "mouse" for m in self.macros.values()):
            self._mouse_listener = mouse.Listener(on_click=self._on_click)
            self._mouse_listener.daemon = True
            self._mouse_listener.start()

    def _on_click(self, x, y, button, pressed):
        if not pressed:
            return

        btn_name = None
        if button == mouse.Button.left:
            btn_name = "left"
        elif button == mouse.Button.right:
            btn_name = "right"
        elif button == mouse.Button.middle:
            btn_name = "middle"
        elif hasattr(mouse.Button, "x1") and button == mouse.Button.x1:
            btn_name = "x1"
        elif hasattr(mouse.Button, "x2") and button == mouse.Button.x2:
            btn_name = "x2"

        if btn_name is None:
            return

        for macro in self.macros.values():
            if macro.enabled and macro.trigger["type"] == "mouse" and macro.trigger["button"] == btn_name:
                self._run_macro(macro.id)

    def _make_runner(self, mid: str):
        def _runner():
            self._run_macro(mid)
        return _runner

    # -- execution --------------------------------------------------------

    def _run_macro(self, mid: str) -> None:
        macro = self.macros.get(mid)
        if not macro or not macro.enabled or self._running.get(mid):
            return
        self._running[mid] = True

        def worker():
            try:
                self._exec_actions(macro.actions)
            except Exception as exc:
                self.log(f"Erro ao rodar '{macro.name}': {exc}")
            finally:
                self._running[mid] = False

        threading.Thread(target=worker, daemon=True, name=f"IXW-Macro-{mid}").start()

    def _exec_actions(self, actions: list[Action]) -> None:
        for action in actions:
            if action.kind == "type_char":
                self._keyboard_ctl.press(action.args["char"])
                self._keyboard_ctl.release(action.args["char"])
            elif action.kind == "key_tap":
                key = self._resolve_key(action.args["key"])
                self._keyboard_ctl.press(key)
                self._keyboard_ctl.release(key)
            elif action.kind == "key_down":
                self._keyboard_ctl.press(self._resolve_key(action.args["key"]))
            elif action.kind == "key_up":
                self._keyboard_ctl.release(self._resolve_key(action.args["key"]))
            elif action.kind == "sleep":
                time.sleep(max(0, action.args["ms"]) / 1000.0)
            elif action.kind == "click":
                args = action.args
                if "x" in args:
                    self._mouse_ctl.position = (args["x"], args["y"])
                btn = {
                    "left": Button.left, "right": Button.right, "middle": Button.middle,
                }.get(args.get("button", "left"), Button.left)
                self._mouse_ctl.click(btn)
            elif action.kind == "mousemove":
                self._mouse_ctl.position = (action.args["x"], action.args["y"])
            elif action.kind == "loop":
                for _ in range(action.args["count"]):
                    self._exec_actions(action.args["actions"])

    def _resolve_key(self, name: str):
        if len(name) == 1:
            return name
        special = getattr(Key, name, None) if Key else None
        return special if special is not None else name

    def stop_all(self) -> None:
        if self._mouse_listener is not None:
            try:
                self._mouse_listener.stop()
            except Exception:
                pass
            self._mouse_listener = None
        if self._hotkeys is not None:
            try:
                self._hotkeys.stop()
            except Exception:
                pass
            self._hotkeys = None
