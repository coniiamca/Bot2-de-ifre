import msgspec
import pytest

from quanta.venues.binance_usdm import endpoints as ep
from quanta.venues.binance_usdm import messages as msg


@pytest.mark.parametrize(
    ("stream", "route"),
    [
        ("btcusdt@depth@100ms", ep.Route.PUBLIC),
        ("btcusdt@depth", ep.Route.PUBLIC),
        ("btcusdt@depth20@100ms", ep.Route.PUBLIC),
        ("btcusdt@rpiDepth@500ms", ep.Route.PUBLIC),
        ("btcusdt@bookTicker", ep.Route.PUBLIC),
        ("!bookTicker", ep.Route.PUBLIC),
        ("btcusdt@aggTrade", ep.Route.MARKET),
        ("btcusdt@markPrice@1s", ep.Route.MARKET),
        ("btcusdt@kline_1m", ep.Route.MARKET),
        ("!forceOrder@arr", ep.Route.MARKET),
    ],
)
def test_route_mapping(stream: str, route: ep.Route) -> None:
    assert ep.route_for_stream(stream) is route


def test_combined_url_and_route_validation() -> None:
    url = ep.combined_stream_url("wss://fstream.binance.com", ep.Route.PUBLIC, ["a@depth@100ms"])
    assert url == "wss://fstream.binance.com/public/stream?streams=a@depth@100ms"
    with pytest.raises(ValueError, match="do not belong"):
        ep.combined_stream_url("wss://x", ep.Route.PUBLIC, ["btcusdt@aggTrade"])
    with pytest.raises(ValueError):
        ep.combined_stream_url("wss://x", ep.Route.MARKET, [])


def test_stream_builders() -> None:
    assert ep.depth_stream("BTCUSDT", 100) == "btcusdt@depth@100ms"
    assert ep.depth_stream("BTCUSDT", 250) == "btcusdt@depth"
    with pytest.raises(ValueError):
        ep.depth_stream("BTCUSDT", 50)
    assert ep.mark_price_stream("ETHUSDT") == "ethusdt@markPrice@1s"


# Payload shapes from the official market-stream documentation.
DEPTH = (
    b'{"stream":"btcusdt@depth@100ms","data":{"e":"depthUpdate","E":1571889248277,'
    b'"T":1571889248276,"s":"BTCUSDT","U":390497796,"u":390497878,"pu":390497794,'
    b'"b":[["7403.89","0.002"]],"a":[["7405.96","3.340"],["7406.63","0.000"]]}}'
)
AGG = (
    b'{"stream":"btcusdt@aggTrade","data":{"e":"aggTrade","E":123456789,"s":"BTCUSDT",'
    b'"a":5933014,"p":"0.001","q":"100","nq":"100","f":100,"l":105,"T":123456785,"m":true}}'
)
FORCE = (
    b'{"stream":"!forceOrder@arr","data":{"e":"forceOrder","E":1568014460893,"st":1,'
    b'"o":{"s":"BTCUSDT","S":"SELL","o":"LIMIT","f":"IOC","q":"0.014","p":"9910",'
    b'"ap":"9910","X":"FILLED","l":"0.014","z":"0.014","T":1568014460893}}}'
)


def test_decode_depth_and_kind() -> None:
    env = msg.decode_envelope(DEPTH)
    assert msg.stream_kind(env.stream) == "depth"
    d = msg.decode_depth(env.data)
    assert (d.U, d.u, d.pu) == (390497796, 390497878, 390497794)
    assert d.a[1] == ("7406.63", "0.000")


def test_decode_agg_trade() -> None:
    env = msg.decode_envelope(AGG)
    t = msg.decode_agg_trade(env.data)
    assert t.a == 5933014 and t.m is True and t.nq == "100"


def test_decode_force_order_object_or_array() -> None:
    env = msg.decode_envelope(FORCE)
    orders = msg.decode_force_orders(env.data)
    assert len(orders) == 1 and orders[0].o.S == "SELL" and orders[0].st == 1
    arr = msg.decode_force_orders(b"[" + bytes(env.data) + b"]")
    assert len(arr) == 1


def test_missing_required_field_is_decode_error() -> None:
    with pytest.raises(msgspec.ValidationError):
        msg.decode_depth(b'{"E":1,"T":1,"s":"X","U":1,"u":2,"b":[],"a":[]}')
