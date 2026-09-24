import json

from quanta.recorder.records import meta_record, rest_record, ws_invalid_record, ws_record


def test_ws_record_splices_payload_verbatim() -> None:
    line = ws_record(123, "conn#1", 7, b'{"stream":"x","data":{"a":1}}')
    obj = json.loads(line)
    assert obj == {
        "t": 123,
        "k": "ws",
        "c": "conn#1",
        "n": 7,
        "p": {"stream": "x", "data": {"a": 1}},
    }


def test_newlines_removed_keep_single_line() -> None:
    line = ws_record(1, "c", 1, b'{"a":\n1,\r\n"b":2}')
    assert b"\n" not in line
    assert json.loads(line)["p"] == {"a": 1, "b": 2}


def test_ws_invalid_record_escapes_text() -> None:
    obj = json.loads(ws_invalid_record(1, "c", 2, 'not "json"\n'))
    assert obj["k"] == "ws_invalid" and obj["p"] == 'not "json"\n'


def test_rest_record_json_and_non_json_body() -> None:
    a = json.loads(
        rest_record(
            5,
            path="/p",
            params={"s": "X"},
            status=200,
            sent_ns=4,
            body=b'{"x":[1,2]}',
            purpose="poll",
            headers={"h": "1"},
        )
    )
    assert a["k"] == "rest" and a["p"]["body"] == {"x": [1, 2]} and a["p"]["status"] == 200
    b = json.loads(
        rest_record(
            5,
            path="/p",
            params={},
            status=451,
            sent_ns=4,
            body=b"<html>blocked</html>",
            purpose="poll",
        )
    )
    assert b["p"]["body"] == "<html>blocked</html>"
    c = json.loads(
        rest_record(5, path="/p", params={}, status=500, sent_ns=4, body=b"", purpose="poll")
    )
    assert c["p"]["body"] == ""


def test_meta_record() -> None:
    obj = json.loads(meta_record(9, "depth_gap", symbol="BTCUSDT", got_u=5))
    assert obj == {"t": 9, "k": "meta", "p": {"type": "depth_gap", "symbol": "BTCUSDT", "got_u": 5}}
