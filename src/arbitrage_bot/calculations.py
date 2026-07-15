from __future__ import annotations

from decimal import Decimal

from arbitrage_bot.amounts import validate_amount_rub
from arbitrage_bot.domain import AmountResult, RateSnapshot, SpreadResult
from arbitrage_bot.errors import InsufficientAmountError

_ONE_HUNDRED = Decimal(100)


def calculate_spread(snapshot: RateSnapshot) -> SpreadResult:
    """Calculate the supported Altyn-to-Rapira direction."""
    altyn_rate = snapshot.altyn.rate
    rapira_bid = snapshot.rapira.bid
    rapira_effective_bid = snapshot.rapira.effective_bid
    gross_spread_rub = rapira_bid - altyn_rate
    net_spread_rub = rapira_effective_bid - altyn_rate

    return SpreadResult(
        altyn_rate=altyn_rate,
        rapira_bid=rapira_bid,
        rapira_effective_bid=rapira_effective_bid,
        rapira_sell_fee_rate=snapshot.rapira.sell_fee_rate,
        gross_spread_rub=gross_spread_rub,
        gross_spread_percent=gross_spread_rub / altyn_rate * _ONE_HUNDRED,
        net_spread_rub=net_spread_rub,
        net_spread_percent=net_spread_rub / altyn_rate * _ONE_HUNDRED,
    )


def calculate_amount(snapshot: RateSnapshot, amount_rub: Decimal) -> AmountResult:
    """Calculate an amount using only the stored market snapshot."""
    validated_amount = validate_amount_rub(amount_rub)
    usdt_bought = validated_amount / snapshot.altyn.rate
    network_fee_usdt = snapshot.altyn.network_fee_usdt
    if network_fee_usdt >= usdt_bought:
        raise InsufficientAmountError("network fee must be less than the purchased USDT amount")

    usdt_to_sell = usdt_bought - network_fee_usdt
    final_rub = usdt_to_sell * snapshot.rapira.effective_bid
    profit_rub = final_rub - validated_amount

    return AmountResult(
        amount_rub=validated_amount,
        usdt_bought=usdt_bought,
        network_fee_usdt=network_fee_usdt,
        usdt_to_sell=usdt_to_sell,
        final_rub=final_rub,
        profit_rub=profit_rub,
        profit_percent=profit_rub / validated_amount * _ONE_HUNDRED,
    )
