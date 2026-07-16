from decimal import Decimal
import unittest

from src.domain.models import ProfitBucket
from src.portfolio.yield_accrual import accrue_rwusd_interest


class YieldAccrualTests(unittest.TestCase):
    def test_accrue_rwusd_interest_adds_hourly_interest_to_bucket(self) -> None:
        bucket = ProfitBucket(rwusd_principal=Decimal("1000"))

        updated = accrue_rwusd_interest(
            bucket=bucket,
            apr=Decimal("0.12"),
            elapsed_hours=Decimal("24"),
        )

        self.assertEqual(updated.rwusd_principal, Decimal("1000"))
        self.assertEqual(
            updated.rwusd_interest_accrued.quantize(Decimal("0.0001")),
            Decimal("0.3288"),
        )


if __name__ == "__main__":
    unittest.main()
