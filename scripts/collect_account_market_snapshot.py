import argparse

from src.app.bootstrap import load_settings_from_env
from src.app.simulation_snapshot import write_account_market_snapshot
from src.exchange.binance_account import BinanceAccountService
from src.exchange.binance_market import BinanceMarketDataService
from src.exchange.binance_rest import BinanceRestClient


def cli(argv: list[str] | None = None) -> dict[str, object]:
    parser = argparse.ArgumentParser(prog="collect_account_market_snapshot")
    parser.add_argument("--env-file", default=".env")
    parser.add_argument("--output-path", required=True)
    parser.add_argument("--selected-symbol", action="append", default=[])
    args = parser.parse_args(argv)

    settings = load_settings_from_env(args.env_file)
    rest_client = BinanceRestClient(
        api_key=settings.exchange.api_key,
        api_secret=settings.exchange.api_secret,
        base_url=settings.exchange.base_url,
    )
    account_service = BinanceAccountService(rest_client)
    market_data_service = BinanceMarketDataService(rest_client)
    return write_account_market_snapshot(
        output_path=args.output_path,
        account_service=account_service,
        market_data_service=market_data_service,
        candidate_symbols=settings.universe.candidate_symbols,
        interval=settings.backtest.primary_bar_interval,
        selected_symbols=args.selected_symbol,
    )


if __name__ == "__main__":
    cli()
