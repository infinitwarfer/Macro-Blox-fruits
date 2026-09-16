from __future__ import annotations

import os
import re
import subprocess
from typing import Callable


class RobloxServer:
    def __init__(self, log: Callable[[str], None] | None = None):
        self.log = log or (lambda _: None)

    @staticmethod
    def build_uri(link: str) -> str:
        link = link.strip()

        share = re.search(
            r"roblox\.com/share\?code=([^&]+)(?:&type=([^&]+))?",
            link,
            re.I,
        )
        if share:
            code = share.group(1)
            kind = share.group(2) or "Server"
            return f"roblox://navigation/share_links?code={code}&type={kind}"

        game = re.search(r"roblox\.com/games/(\d+)", link, re.I)
        if not game:
            place = re.search(r"[?&]placeId=(\d+)", link, re.I)
            if place:
                place_id = place.group(1)
            else:
                raise ValueError("Link Roblox inválido.")
        else:
            place_id = game.group(1)

        code = re.search(r"privateServerLinkCode=([^&]+)", link, re.I)
        if code:
            return (
                f"roblox://experiences/start?"
                f"placeId={place_id}&linkCode={code.group(1)}"
            )

        return f"roblox://experiences/start?placeId={place_id}"

    def join(self, link: str) -> str:
        uri = self.build_uri(link)

        if os.name == "nt":
            os.startfile(uri)
        else:
            subprocess.Popen(["xdg-open", uri])

        self.log("Abrindo Roblox...")
        return uri
