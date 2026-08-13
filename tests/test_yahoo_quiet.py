import logging
import sys

from optionda.market.yahoo import YahooClient

_CURL16 = (
    "Failed to perform, curl: (16) . See "
    "https://curl.se/libcurl/c/libcurl-errors.html first for more details."
)


class _NoisyTicker:
    def __init__(self, symbol: str) -> None:
        self.ticker = symbol

    @property
    def info(self) -> dict:
        logging.getLogger("yfinance").error(_CURL16)
        print(_CURL16, file=sys.stderr)
        raise RuntimeError(_CURL16)


def test_yahoo_spot_fetch_swallows_curl_http2_noise(capsys, monkeypatch) -> None:
    monkeypatch.setattr("optionda.market.yahoo.yf.Ticker", _NoisyTicker)

    spots = YahooClient().get_spots(["AAPL"])

    err = capsys.readouterr().err
    assert spots == {}
    assert "curl: (16)" not in err
    assert "libcurl-errors" not in err
    assert "Failed to perform" not in err
