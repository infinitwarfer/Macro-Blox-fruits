from __future__ import annotations

import threading
import time
from typing import Callable

try:
    from pynput import keyboard, mouse
except ImportError:
    keyboard = None
    mouse = None


class MacroManager:
    """
    Python implementation of the three macros supplied for InfinityxWare.

    r_x:
        XButton1 -> R, short delay, X

    f_x:
        XButton1 -> F, short delay, X

    spam_r:
        XButton2 toggles repeated R.
    """

    def __init__(self, log: Callable[[str], None] | None = None):
        self.log = log or (lambda _: None)

    
        self.enabled = {"r_x": False, "f_x": False, "spam_r": False}


        self._spam_running = False

        self._mouse_listener = None
        self._spam_thread: threading.Thread | None = None
        self._spam_stop = threading.Event()
        self._lock = threading.Lock()

        self._keyboard = None
        if keyboard is not None:
            self._keyboard = keyboard.Controller()

    @property
    def available(self) -> bool:
        return keyboard is not None and mouse is not None

    @property
    def spam_running(self) -> bool:
        return self._spam_running

    def start_listening(self) -> bool:
        if not self.available:
            self.log("pynput não está instalado.")
            return False

        if self._mouse_listener is not None:
            return True

        self._mouse_listener = mouse.Listener(on_click=self._on_click)
        self._mouse_listener.daemon = True
        self._mouse_listener.start()
        self.log("Listener de macros iniciado.")
        return True

    def _on_click(self, x, y, button, pressed):
        if not pressed:
            return

        if button == mouse.Button.x1 and self.enabled["r_x"]:
            self._send_sequence("r", "x")
        elif button == mouse.Button.x1 and self.enabled["f_x"]:
            self._send_sequence("f", "x")
        elif button == mouse.Button.x2 and self.enabled["spam_r"]:
            self.toggle_spam_r()

    def _send_sequence(self, first: str, second: str):
        if self._keyboard is None:
            return
        self._keyboard.press(first)
        self._keyboard.release(first)
        time.sleep(0.001)
        self._keyboard.press(second)
        self._keyboard.release(second)

    def set_enabled(self, name: str, enabled: bool) -> None:
        if name not in self.enabled:
            raise ValueError(f"Macro desconhecida: {name}")

     
        if name in ("r_x", "f_x") and enabled:
            other = "f_x" if name == "r_x" else "r_x"
            self.enabled[other] = False

        self.enabled[name] = bool(enabled)

        if name == "spam_r" and not enabled:
            self._spam_running = False
            self._stop_spam_thread()

        self.log(f"{name}: {'ON' if enabled else 'OFF'}")

    def toggle_spam_r(self) -> bool:
        """Chamado pelo clique do XButton2. Liga/desliga o loop de fato."""
        self._spam_running = not self._spam_running

        if self._spam_running:
            self._start_spam_thread()
        else:
            self._stop_spam_thread()

        self.log(f"Spam R: {'ON' if self._spam_running else 'OFF'}")
        return self._spam_running

    def _start_spam_thread(self):
        if self._spam_thread and self._spam_thread.is_alive():
            return

        self._spam_stop.clear()
        self._spam_thread = threading.Thread(
            target=self._spam_loop,
            name="InfinityxWare-SpamR",
            daemon=True,
        )
        self._spam_thread.start()

    def _stop_spam_thread(self):
        self._spam_stop.set()

    def _spam_loop(self):
        while not self._spam_stop.is_set():
            if self._keyboard is not None:
                self._keyboard.press("r")
                self._keyboard.release("r")
            self._spam_stop.wait(0.010)

    def stop_all(self):
        self.enabled = {key: False for key in self.enabled}
        self._spam_running = False
        self._stop_spam_thread()

        if self._mouse_listener is not None:
            try:
                self._mouse_listener.stop()
            except Exception:
                pass
            self._mouse_listener = None

        self.log("Todos os macros foram parados.")
