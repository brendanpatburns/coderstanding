from dataclasses import dataclass
from typing import Optional, Protocol

from . import util


class Priced(Protocol):
    def price(self) -> int: ...


class Item:
    def __init__(self, name: str, cents: int):
        self.name = name
        self.cents = cents

    def label(self) -> str:
        return util.format_price(self.cents)


class Gift(Item):
    pass


@dataclass
class Receipt:
    owner: Optional["Item"] = None
