from dataclasses import dataclass, field


@dataclass(slots=True, frozen=True)
class PreflightCheckResult:
    name: str
    ok: bool
    reason: str | None = None


@dataclass(slots=True)
class PreflightReport:
    checks: list[PreflightCheckResult] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return all(item.ok for item in self.checks)


class PreflightChecker:
    def __init__(
        self,
        account_service,
        stream_client,
        candidate_symbols: list[str],
    ) -> None:
        self._account_service = account_service
        self._stream_client = stream_client
        self._candidate_symbols = candidate_symbols

    def run(self) -> PreflightReport:
        checks = [
            self._check_pm_account(),
            self._check_um_position_mode(),
            self._check_symbol_rules(),
            self._check_user_stream(),
            self._check_rwusd_capability(),
        ]
        return PreflightReport(checks=checks)

    def _check_pm_account(self) -> PreflightCheckResult:
        try:
            self._account_service.get_pm_account_snapshot()
        except Exception as exc:
            return PreflightCheckResult(name="pm_account", ok=False, reason=str(exc))
        return PreflightCheckResult(name="pm_account", ok=True)

    def _check_um_position_mode(self) -> PreflightCheckResult:
        try:
            payload = self._account_service.get_um_position_mode()
        except Exception as exc:
            return PreflightCheckResult(name="um_position_mode", ok=False, reason=str(exc))

        if not payload.get("dualSidePosition", False):
            return PreflightCheckResult(
                name="um_position_mode",
                ok=False,
                reason="hedge_mode_required",
            )
        return PreflightCheckResult(name="um_position_mode", ok=True)

    def _check_symbol_rules(self) -> PreflightCheckResult:
        try:
            for symbol in self._candidate_symbols:
                self._account_service.get_symbol_order_sizing_rule(symbol)
        except Exception as exc:
            return PreflightCheckResult(name="symbol_rules", ok=False, reason=str(exc))
        return PreflightCheckResult(name="symbol_rules", ok=True)

    def _check_user_stream(self) -> PreflightCheckResult:
        try:
            payload = self._stream_client.start_user_stream()
            listen_key = payload.get("listenKey")
            if not listen_key:
                return PreflightCheckResult(
                    name="user_stream",
                    ok=False,
                    reason="listen_key_missing",
                )
            self._stream_client.keepalive_user_stream()
        except Exception as exc:
            return PreflightCheckResult(name="user_stream", ok=False, reason=str(exc))
        finally:
            try:
                self._stream_client.close_user_stream()
            except Exception:
                pass

        return PreflightCheckResult(name="user_stream", ok=True)

    def _check_rwusd_capability(self) -> PreflightCheckResult:
        required_methods = (
            "transfer_pm_to_spot",
            "subscribe_rwusd",
            "redeem_rwusd",
            "transfer_spot_to_pm",
        )
        missing = [
            name
            for name in required_methods
            if not callable(getattr(self._account_service, name, None))
        ]
        if missing:
            return PreflightCheckResult(
                name="rwusd_capability",
                ok=False,
                reason=f"missing methods: {', '.join(missing)}",
            )
        return PreflightCheckResult(name="rwusd_capability", ok=True)
