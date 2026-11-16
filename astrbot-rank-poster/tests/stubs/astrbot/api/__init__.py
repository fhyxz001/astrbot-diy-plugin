"""Minimal AstrBot API stubs for local tests (no real AstrBot required)."""

import logging


logger = logging.getLogger("astrbot")


class AstrBotConfig(dict):
    """AstrBotConfig is a dict subclass in real AstrBot."""

    def save_config(self):
        return None
