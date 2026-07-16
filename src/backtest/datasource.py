from decimal import Decimal
from typing import TypedDict

from src.market.selector import SelectorRow


class BacktestRow(SelectorRow):
    close: Decimal
