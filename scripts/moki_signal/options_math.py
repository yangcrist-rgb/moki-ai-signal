from __future__ import annotations

import math
from dataclasses import dataclass
from statistics import NormalDist, median


_NORMAL = NormalDist()
CONTRACT_MULTIPLIER = 100
MIN_IV, MAX_IV = 0.03, 3.0


@dataclass(frozen=True)
class Contract:
    symbol: str
    kind: str
    strike: float
    expiration: int
    dte: int
    bid: float
    ask: float
    last: float
    volume: int
    open_interest: int
    iv: float

    @property
    def mid(self) -> float:
        if self.bid > 0 and self.ask > 0:
            return (self.bid + self.ask) / 2
        return self.last

    @property
    def spread_pct(self) -> float | None:
        if self.bid <= 0 or self.ask <= 0:
            return None
        return (self.ask - self.bid) / self.mid


def _d1(spot: float, strike: float, years: float, rate: float, iv: float) -> float:
    return (math.log(spot / strike) + (rate + 0.5 * iv * iv) * years) / (iv * math.sqrt(years))


def bs_delta(kind: str, spot: float, strike: float, years: float, rate: float, iv: float) -> float:
    years = max(years, 1 / 365)
    cdf = _NORMAL.cdf(_d1(spot, strike, years, rate, iv))
    return cdf if kind == "call" else cdf - 1.0


def bs_gamma(spot: float, strike: float, years: float, rate: float, iv: float) -> float:
    years = max(years, 1 / 365)
    return _NORMAL.pdf(_d1(spot, strike, years, rate, iv)) / (spot * iv * math.sqrt(years))


def bs_price(kind: str, spot: float, strike: float, years: float, rate: float, iv: float) -> float:
    years = max(years, 1 / 365)
    d1 = _d1(spot, strike, years, rate, iv)
    d2 = d1 - iv * math.sqrt(years)
    discount = strike * math.exp(-rate * years)
    if kind == "call":
        return spot * _NORMAL.cdf(d1) - discount * _NORMAL.cdf(d2)
    return discount * _NORMAL.cdf(-d2) - spot * _NORMAL.cdf(-d1)


def bs_theta_per_day(kind: str, spot: float, strike: float, years: float, rate: float, iv: float) -> float:
    years = max(years, 1 / 365)
    d1 = _d1(spot, strike, years, rate, iv)
    d2 = d1 - iv * math.sqrt(years)
    decay = -spot * _NORMAL.pdf(d1) * iv / (2 * math.sqrt(years))
    carry = rate * strike * math.exp(-rate * years)
    annual = decay - carry * _NORMAL.cdf(d2) if kind == "call" else decay + carry * _NORMAL.cdf(-d2)
    return annual / 365


def implied_vol(kind: str, price: float, spot: float, strike: float, years: float, rate: float) -> float | None:
    """Bisection inverse of ``bs_price``; None when the price is outside the no-arbitrage band."""
    years = max(years, 1 / 365)
    low, high = MIN_IV, MAX_IV
    if not (bs_price(kind, spot, strike, years, rate, low) < price < bs_price(kind, spot, strike, years, rate, high)):
        return None
    for _ in range(80):
        mid = (low + high) / 2
        if bs_price(kind, spot, strike, years, rate, mid) < price:
            low = mid
        else:
            high = mid
    return (low + high) / 2


def spot_for_put_delta(strike: float, years: float, rate: float, iv: float, target_abs_delta: float) -> float:
    """Underlying price at which a put's Black-Scholes delta reaches -target_abs_delta."""
    years = max(years, 1 / 365)
    d1 = _NORMAL.inv_cdf(1.0 - target_abs_delta)
    return strike * math.exp(d1 * iv * math.sqrt(years) - (rate + 0.5 * iv * iv) * years)


def parse_contracts(raw: list[dict], kind: str, now_epoch: int) -> list[Contract]:
    contracts = []
    for item in raw:
        iv = float(item.get("impliedVolatility") or 0.0)
        expiration = int(item.get("expiration") or 0)
        contracts.append(
            Contract(
                symbol=str(item.get("contractSymbol", "")),
                kind=kind,
                strike=float(item.get("strike") or 0.0),
                expiration=expiration,
                dte=max(0, math.ceil((expiration + 16 * 3600 - now_epoch) / 86400)),
                bid=float(item.get("bid") or 0.0),
                ask=float(item.get("ask") or 0.0),
                last=float(item.get("lastPrice") or 0.0),
                volume=int(item.get("volume") or 0),
                open_interest=int(item.get("openInterest") or 0),
                iv=iv,
            )
        )
    return [contract for contract in contracts if contract.strike > 0]


def usable_iv(contract: Contract) -> bool:
    return MIN_IV <= contract.iv <= MAX_IV


def gex_by_strike(contracts: list[Contract], spot: float, rate: float, *, band: float = 0.25) -> dict[float, float]:
    """Dealer gamma exposure per 1% move, assuming dealers are long calls and short puts.

    This is the common public convention, not observed dealer inventory.
    """
    exposure: dict[float, float] = {}
    for contract in contracts:
        if not usable_iv(contract) or contract.open_interest <= 0 or abs(contract.strike / spot - 1) > band:
            continue
        gamma = bs_gamma(spot, contract.strike, contract.dte / 365, rate, contract.iv)
        value = gamma * contract.open_interest * CONTRACT_MULTIPLIER * spot * spot * 0.01
        exposure[contract.strike] = exposure.get(contract.strike, 0.0) + (value if contract.kind == "call" else -value)
    return exposure


def gex_summary(contracts: list[Contract], spot: float, rate: float) -> dict[str, object]:
    exposure = gex_by_strike(contracts, spot, rate)
    if not exposure:
        return {"status": "unavailable"}
    calls = {strike: value for strike, value in exposure.items() if value > 0 and strike >= spot}
    puts = {strike: value for strike, value in exposure.items() if value < 0 and strike <= spot}
    ordered = sorted(exposure.items())
    flip = None
    running = 0.0
    for strike, value in ordered:
        previous = running
        running += value
        if previous < 0 <= running or previous > 0 >= running:
            flip = strike
    return {
        "status": "estimated",
        "net_gex_usd_per_1pct": round(sum(exposure.values())),
        "call_wall": max(calls, key=calls.get) if calls else None,
        "put_wall": min(puts, key=puts.get) if puts else None,
        "gamma_flip_strike": flip,
        "regime": "positive-gamma" if sum(exposure.values()) > 0 else "negative-gamma",
    }


def volume_anomaly(current: float, prior: list[float], *, minimum_days: int = 3, multiple: float = 2.0) -> dict[str, object]:
    if len(prior) < minimum_days:
        return {"status": "building-baseline", "baseline_days": len(prior), "required_days": minimum_days}
    baseline = median(prior)
    ratio = current / baseline if baseline > 0 else None
    flagged = ratio is not None and ratio >= multiple
    return {
        "status": "spike" if flagged else "normal",
        "baseline_days": len(prior),
        "baseline_median": round(baseline),
        "ratio": round(ratio, 2) if ratio is not None else None,
    }


def expirations_in_window(expirations: list[int], now_epoch: int, *, min_dte: int, max_dte: int) -> list[int]:
    return [value for value in expirations if min_dte <= math.ceil((value + 16 * 3600 - now_epoch) / 86400) <= max_dte]


def pick_expiration(
    expirations: list[int],
    now_epoch: int,
    *,
    min_dte: int,
    max_dte: int,
    target_dte: int,
    open_interest: dict[int, int] | None = None,
) -> int | None:
    """Prefer the most liquid expiry in the window; break ties by distance to target DTE."""
    candidates = []
    for expiration in expirations_in_window(expirations, now_epoch, min_dte=min_dte, max_dte=max_dte):
        dte = math.ceil((expiration + 16 * 3600 - now_epoch) / 86400)
        liquidity = (open_interest or {}).get(expiration, 0)
        candidates.append((-liquidity, abs(dte - target_dte), expiration))
    return min(candidates)[2] if candidates else None


def pick_by_delta(
    contracts: list[Contract],
    spot: float,
    rate: float,
    target_abs_delta: float,
    *,
    min_open_interest: int,
    max_spread_pct: float,
    max_delta_gap: float = 0.1,
    delta_range: tuple[float, float] | None = None,
) -> tuple[Contract, float] | None:
    best: tuple[float, Contract, float] | None = None
    for contract in contracts:
        if not usable_iv(contract) or contract.open_interest < min_open_interest or contract.mid <= 0:
            continue
        spread = contract.spread_pct
        if spread is None or spread > max_spread_pct:
            continue
        delta = bs_delta(contract.kind, spot, contract.strike, contract.dte / 365, rate, contract.iv)
        distance = abs(abs(delta) - target_abs_delta)
        if distance > max_delta_gap:
            continue
        if delta_range is not None and not delta_range[0] <= abs(delta) <= delta_range[1]:
            continue
        if best is None or distance < best[0]:
            best = (distance, contract, delta)
    return (best[1], best[2]) if best else None
