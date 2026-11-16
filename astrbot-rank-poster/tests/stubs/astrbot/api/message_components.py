"""Minimal stubs of astrbot.api.message_components."""

from __future__ import annotations


class Plain:
    def __init__(self, text: str = ""):
        self.text = text


class Image:
    def __init__(self, path: str = ""):
        self.path = path

    @classmethod
    def fromFileSystem(cls, path: str):
        return cls(path)

    @classmethod
    def fromURL(cls, url: str):
        return cls(url)


class Video:
    def __init__(self, path: str = ""):
        self.path = path

    @classmethod
    def fromFileSystem(cls, path: str):
        return cls(path)

    @classmethod
    def fromURL(cls, url: str):
        return cls(url)
