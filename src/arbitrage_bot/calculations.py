from __future__ import annotations

from decimal import Decimal

from arbitrage_bot.amounts import validate_amount_rub
from arbitrage_bot.domain import AmountResult, ExchangeQuote, RateSnapshot, SpreadResult

_ONE_HUNDRED = Decimal(100)


def _calculate_direction(buy: ExchangeQuote, sell: ExchangeQuote) -> SpreadResult:
    gross_spread_rub = sell.bid - buy.ask
    effective_buy = buy.effective_ask
    effective_sell = sell.effective_bid
    net_spread_rub = effective_sell - effective_buy

    return SpreadResult(
        buy_exchange=buy.exchange,
        sell_exchange=sell.exchange,
        raw_buy=buy.ask,
        raw_sell=sell.bid,
        effective_buy=effective_buy,
        effective_sell=effective_sell,
        buy_fee_rate=buy.buy_fee_rate,
        sell_fee_rate=sell.sell_fee_rate,
        gross_spread_rub=gross_spread_rub,
        gross_spread_percent=gross_spread_rub / buy.ask * _ONE_HUNDRED,
        net_spread_rub=net_spread_rub,
        net_spread_percent=net_spread_rub / effective_buy * _ONE_HUNDRED,
    )


def calculate_best_spread(snapshot: RateSnapshot) -> SpreadResult:
    """Return the direction with the greatest net return on the RUB spent."""
    altyn_to_rapira = _calculate_direction(snapshot.altyn, snapshot.rapira)
    rapira_to_altyn = _calculate_direction(snapshot.rapira, snapshot.altyn)

    # The first direction wins an exact tie by product requirement.
    if altyn_to_rapira.net_spread_percent >= rapira_to_altyn.net_spread_percent:
        return altyn_to_rapira
    return rapira_to_altyn


def calculate_amount(spread: SpreadResult, amount_rub: Decimal) -> AmountResult:
    """Calculate the result of using the entire RUB amount in the chosen direction."""
    validated_amount = validate_amount_rub(amount_rub)
    usdt_to_sell = validated_amount / spread.effective_buy
    final_rub = usdt_to_sell * spread.effective_sell
    profit_rub = final_rub - validated_amount

    return AmountResult(
        amount_rub=validated_amount,
        usdt_to_sell=usdt_to_sell,
        final_rub=final_rub,
        profit_rub=profit_rub,
        profit_percent=profit_rub / validated_amount * _ONE_HUNDRED,
    )
