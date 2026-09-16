from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from typing import Callable

try:
    from pynput.keyboard import Controller as KeyboardController
except ImportError:
    KeyboardController = None

try:
    from PIL import ImageGrab
except ImportError:
    ImageGrab = None


@dataclass
class PixelSettings:
    x: int = 759
    y: int = 624
    target_color: int = 0xFFFFFF
    mode: str = "X"
    action: str = "click"
    cooldown_ms: int = 15000


class PixelMacro:
    def __init__(self, log: Callable[[str], None] | None = None):
        self.log = log or (lambda _: None)
        self.settings = PixelSettings()
        self.active = False
        self.scanning = False
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._keyboard = KeyboardController() if KeyboardController else None

    @property
    def available(self) -> bool:
        return ImageGrab is not None and self._keyboard is not None

    def update(self, **values):
        for key, value in values.items():
            if not hasattr(self.settings, key):
                continue
            setattr(self.settings, key, value)

    def start(self):
        if not self.available:
            raise RuntimeError("Pixel macro precisa de Pillow e pynput.")

        if self._thread and self._thread.is_alive():
            self.active = True
            self.scanning = True
            return

        self._stop.clear()
        self.active = True
        self.scanning = True
        self._thread = threading.Thread(
            target=self._loop,
            name="InfinityxWare-Pixel",
            daemon=True,
        )
        self._thread.start()
        self.log("Pixel macro iniciado.")

    def stop(self):
        self.active = False
        self.scanning = False
        self._stop.set()
        self.log("Pixel macro parado.")

    def _loop(self):
        next_fire = 0.0

        while not self._stop.is_set():
            if not self.scanning:
                self._stop.wait(0.05)
                continue

            now = time.monotonic()
            if now < next_fire:
                self._stop.wait(0.02)
                continue

            try:
                current = self._read_pixel()
                if current != self.settings.target_color:
                    key = self.settings.mode.lower()

                    if self.settings.action.lower() == "hold":
                        self._keyboard.press(key)
                        self._stop.wait(3.0)
                        self._keyboard.release(key)
                    else:
                        self._keyboard.press(key)
                        self._keyboard.release(key)

                    next_fire = time.monotonic() + self.settings.cooldown_ms / 1000.0
                    self.log(f"Pixel acionou [{key.upper()}]")

            except Exception as exc:
                self.log(f"Pixel: {type(exc).__name__}")

            self._stop.wait(0.02)

    def _read_pixel(self) -> int:
        img = ImageGrab.grab(
            bbox=(
                int(self.settings.x),
                int(self.settings.y),
                int(self.settings.x) + 1,
                int(self.settings.y) + 1,
            )
        )
        r, g, b = img.getpixel((0, 0))[:3]
        return (r << 16) | (g << 8) | b
