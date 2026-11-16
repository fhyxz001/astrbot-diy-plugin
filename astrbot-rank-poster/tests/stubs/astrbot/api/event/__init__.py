"""Minimal stubs of astrbot.api.event."""

from __future__ import annotations


class AstrMessageEvent:
    pass


class MessageChain:
    def __init__(self, chain=None, **kwargs):
        self.chain = list(chain or [])

    def message(self, text: str):
        self.chain.append(("text", text))
        return self

    def file_image(self, path: str):
        self.chain.append(("image", path))
        return self


class _Filter:
    class EventMessageType:
        GROUP_MESSAGE = 1
        PRIVATE_MESSAGE = 2
        OTHER_MESSAGE = 4
        ALL = 7

    @staticmethod
    def command(*args, **kwargs):
        def decorator(func):
            return func

        return decorator

    @staticmethod
    def event_message_type(*args, **kwargs):
        def decorator(func):
            return func

        return decorator


filter = _Filter()
