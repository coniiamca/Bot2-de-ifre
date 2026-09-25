"""Signed REST client for Binance USDⓈ-M trading (plan §12). Every call either returns the
parsed response or raises :class:`TradeError` whose ``action`` says whether the request may
have executed (``errors.classify``). No call is ever retried here: the OMS decides.

Conditional orders (STOP_MARKET etc.) go to the Algo Service (``/fapi/v1/algoOrder``) since
2025-12-09; ``/fapi/v1/order`` rejects them with -4120.
"""

from __future__ import annotations

from typing import Any

import aiohttp
import msgspec

from quanta.core.clock import Clock
from quanta.core.errors import QuantaError
from quanta.venues.binance_usdm.errors import Action, classify
from quanta.venues.binance_usdm.ratelimit import WeightBudget
from quanta.venues.binance_usdm.signing import Signer, signed_query

MARGIN_ALREADY_SET = -4046  # "No need to change margin type."
POSITION_MODE_ALREADY_SET = -4059  # "No need to change position side."


class TradeError(QuantaError):
    def __init__(self, action: Action, status: int, code: int | None, msg: str, path: str) -> None:
        self.action = action
        self.status = status
        self.code = code
        self.msg = msg
        self.path = path
        super().__init__(f"{path}: {action} (HTTP {status} code={code} {msg})")


# request weights (IP) of the endpoints used; unknown paths count 1
WEIGHTS = {
    "/fapi/v3/account": 5,
    "/fapi/v3/positionRisk": 5,
    "/fapi/v1/openOrders": 1,
    "/fapi/v1/allOpenOrders": 1,
    "/fapi/v1/countdownCancelAll": 10,
    "/fapi/v1/exchangeInfo": 1,
}


class BinanceTradeRest:
    def __init__(
        self,
        session: aiohttp.ClientSession,
        base_url: str,
        signer: Signer,
        budget: WeightBudget,
        clock: Clock,
        recv_window_ms: int = 5000,
        timeout_s: float = 10.0,
    ) -> None:
        self._session = session
        self._base = base_url.rstrip("/")
        self._signer = signer
        self._budget = budget
        self._clock = clock
        self._recv_window = recv_window_ms
        self._timeout = aiohttp.ClientTimeout(total=timeout_s)
        self.offset_ms = 0  # server − local clock
        self.order_count_1m: int | None = None

    # -- plumbing ------------------------------------------------------------------------
    def now_ms(self) -> int:
        return self._clock.now_ns() // 1_000_000 + self.offset_ms

    async def sync_time(self) -> float:
        """Measure the server clock; returns the local clock offset in seconds (local − server)."""
        sent = self._clock.now_ns()
        data = await self.request("GET", "/fapi/v1/time", signed=False)
        recv = self._clock.now_ns()
        mid_ms = (sent + recv) // 2 // 1_000_000
        self.offset_ms = int(data["serverTime"]) - mid_ms
        return -self.offset_ms / 1000.0

    async def request(
        self,
        method: str,
        path: str,
        params: dict[str, Any] | None = None,
        *,
        signed: bool = True,
        key_only: bool = False,
        weight: int | None = None,
    ) -> Any:
        w = WEIGHTS.get(path, 1) if weight is None else weight
        if w:
            await self._budget.acquire(w)
        headers = {}
        if signed or key_only:
            headers["X-MBX-APIKEY"] = self._signer.api_key
        if signed:
            query = signed_query(params or {}, self._signer, self.now_ms(), self._recv_window)
        else:
            query = "&".join(f"{k}={v}" for k, v in (params or {}).items() if v is not None)
        url = f"{self._base}{path}" + (f"?{query}" if query else "")
        resp_headers: dict[str, str] = {}
        try:
            async with self._session.request(
                method, url, headers=headers, timeout=self._timeout
            ) as resp:
                body = await resp.read()
                status = resp.status
                resp_headers = {
                    k: v for k, v in resp.headers.items() if k.lower().startswith("x-mbx")
                }
                retry_after = resp.headers.get("Retry-After")
        except (aiohttp.ClientError, TimeoutError) as exc:
            if w:
                self._budget.release(w, None)
            # no response: the request may or may not have reached the matching engine
            raise TradeError(Action.UNKNOWN, 0, None, type(exc).__name__, path) from exc
        if w:
            self._budget.release(w, resp_headers)
        for k, v in resp_headers.items():
            if k.lower() == "x-mbx-order-count-1m":
                self.order_count_1m = int(v)
        if status == 200:
            return msgspec.json.decode(body) if body else {}
        code, msg = None, body[:200].decode("utf-8", "replace")
        try:
            parsed = msgspec.json.decode(body)
            if isinstance(parsed, dict):
                code, msg = parsed.get("code"), str(parsed.get("msg", ""))
        except msgspec.DecodeError:
            pass
        if status in (418, 429):
            self._budget.block_for(float(retry_after or 60))
        raise TradeError(classify(status, code, msg), status, code, msg, path)

    # -- market info (unsigned) ----------------------------------------------------------
    async def exchange_info(self) -> Any:
        return await self.request("GET", "/fapi/v1/exchangeInfo", signed=False)

    async def book_ticker(self, symbol: str) -> Any:
        return await self.request(
            "GET", "/fapi/v1/ticker/bookTicker", {"symbol": symbol}, signed=False, weight=2
        )

    async def premium_index(self, symbol: str) -> Any:
        return await self.request("GET", "/fapi/v1/premiumIndex", {"symbol": symbol}, signed=False)

    # -- orders ----------------------------------------------------------------------------
    async def new_order(
        self,
        symbol: str,
        side: str,
        order_type: str,
        client_id: str,
        quantity: str | None = None,
        price: str | None = None,
        time_in_force: str | None = None,
        reduce_only: bool | None = None,
    ) -> Any:
        return await self.request(
            "POST",
            "/fapi/v1/order",
            {
                "symbol": symbol,
                "side": side,
                "type": order_type,
                "quantity": quantity,
                "price": price,
                "timeInForce": time_in_force,
                "reduceOnly": reduce_only,
                "newClientOrderId": client_id,
                "newOrderRespType": "RESULT",
            },
        )

    async def query_order(self, symbol: str, client_id: str) -> Any:
        return await self.request(
            "GET", "/fapi/v1/order", {"symbol": symbol, "origClientOrderId": client_id}
        )

    async def cancel_order(self, symbol: str, client_id: str) -> Any:
        return await self.request(
            "DELETE", "/fapi/v1/order", {"symbol": symbol, "origClientOrderId": client_id}
        )

    async def cancel_all_orders(self, symbol: str) -> Any:
        return await self.request("DELETE", "/fapi/v1/allOpenOrders", {"symbol": symbol})

    async def open_orders(self, symbol: str | None = None) -> Any:
        return await self.request(
            "GET", "/fapi/v1/openOrders", {"symbol": symbol}, weight=1 if symbol else 40
        )

    async def countdown_cancel_all(self, symbol: str, countdown_ms: int) -> Any:
        """Dead-man switch: cancel the symbol's open orders unless renewed in time (0 = off)."""
        return await self.request(
            "POST",
            "/fapi/v1/countdownCancelAll",
            {"symbol": symbol, "countdownTime": countdown_ms},
        )

    # -- algo (conditional) orders -------------------------------------------------------
    async def new_stop_close(
        self, symbol: str, side: str, trigger_price: str, client_algo_id: str
    ) -> Any:
        """Protective stop: STOP_MARKET closePosition on the mark price."""
        return await self.request(
            "POST",
            "/fapi/v1/algoOrder",
            {
                "algoType": "CONDITIONAL",
                "symbol": symbol,
                "side": side,
                "type": "STOP_MARKET",
                "triggerPrice": trigger_price,
                "closePosition": True,
                "workingType": "MARK_PRICE",
                "priceProtect": True,
                "clientAlgoId": client_algo_id,
            },
        )

    async def new_stop_order(
        self, symbol: str, side: str, trigger_price: str, quantity: str, client_algo_id: str
    ) -> Any:
        """Plain STOP_MARKET with a quantity (used by the dead-man drill)."""
        return await self.request(
            "POST",
            "/fapi/v1/algoOrder",
            {
                "algoType": "CONDITIONAL",
                "symbol": symbol,
                "side": side,
                "type": "STOP_MARKET",
                "triggerPrice": trigger_price,
                "quantity": quantity,
                "workingType": "MARK_PRICE",
                "clientAlgoId": client_algo_id,
            },
        )

    async def cancel_algo_order(self, client_algo_id: str) -> Any:
        return await self.request("DELETE", "/fapi/v1/algoOrder", {"clientAlgoId": client_algo_id})

    async def cancel_all_algo_orders(self, symbol: str) -> Any:
        return await self.request("DELETE", "/fapi/v1/algoOpenOrders", {"symbol": symbol})

    async def open_algo_orders(self, symbol: str | None = None) -> Any:
        return await self.request("GET", "/fapi/v1/openAlgoOrders", {"symbol": symbol})

    # -- account ---------------------------------------------------------------------------
    async def account(self) -> Any:
        return await self.request("GET", "/fapi/v3/account")

    async def position_risk(self, symbol: str | None = None) -> Any:
        return await self.request("GET", "/fapi/v3/positionRisk", {"symbol": symbol})

    async def set_leverage(self, symbol: str, leverage: int) -> Any:
        return await self.request(
            "POST", "/fapi/v1/leverage", {"symbol": symbol, "leverage": leverage}
        )

    async def set_isolated(self, symbol: str) -> None:
        try:
            await self.request(
                "POST", "/fapi/v1/marginType", {"symbol": symbol, "marginType": "ISOLATED"}
            )
        except TradeError as e:
            if e.code != MARGIN_ALREADY_SET:
                raise

    async def set_one_way(self) -> None:
        try:
            await self.request("POST", "/fapi/v1/positionSide/dual", {"dualSidePosition": False})
        except TradeError as e:
            if e.code != POSITION_MODE_ALREADY_SET:
                raise

    # -- user data stream ------------------------------------------------------------------
    async def new_listen_key(self) -> str:
        data = await self.request("POST", "/fapi/v1/listenKey", signed=False, key_only=True)
        return str(data["listenKey"])

    async def keepalive_listen_key(self) -> None:
        await self.request("PUT", "/fapi/v1/listenKey", signed=False, key_only=True)

    async def close_listen_key(self) -> None:
        await self.request("DELETE", "/fapi/v1/listenKey", signed=False, key_only=True)
