import os
from .models import Item, Priced, Receipt
from .util import format_price


class Ledger:
    def record(self, line):
        pass


class Cart(Priced):
    ledger: Ledger

    def __init__(self, owner: Receipt):
        self.items = Item("start", 0)
        self.owner = owner
        self.log = Ledger()

    def add(self, item: Item) -> "Cart":
        self.log.record(item)
        os.getcwd()
        return self

    def total(self):
        return format_price(1)
