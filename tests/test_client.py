import httpx
import pytest

from core.client import PlaudAPIError, PlaudClient
from core.config import PlaudConfig


def test_client_wraps_network_errors_without_raw_httpx_traceback() -> None:
    cfg = PlaudConfig(
        authorization="Bearer test",
        x_device_id="device",
        x_pld_tag="tag",
        x_pld_user="user",
    )
    client = PlaudClient(cfg)

    def boom(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("network down", request=request)

    client._client = httpx.Client(
        base_url=cfg.base_url,
        transport=httpx.MockTransport(boom),
    )

    with pytest.raises(PlaudAPIError) as excinfo:
        client.list_folders()

    msg = str(excinfo.value)
    assert msg.startswith("Plaud network error for GET api-apne1.plaud.ai/filetag/")
    assert "network down" in msg
    assert "Check your internet connection or Plaud session" in msg
