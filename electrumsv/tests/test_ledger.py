"""Ledger discovery and setup regressions, without connecting to a device."""

from types import SimpleNamespace
from unittest.mock import Mock

from bitcoinx import BIP32PrivateKey, bip32_decompose_chain_string
import pytest

hid = pytest.importorskip("hid")
pytest.importorskip("btchip.btchip")

from electrumsv.device import DeviceMgr
from electrumsv.devices.ledger import ledger
from electrumsv.networks import Net


@pytest.mark.parametrize("product_id", [0x0005, 0x5000, 0x5011, 0x5015, 0x1011, 0x4011])
def test_enumerate_nano_devices(monkeypatch, product_id):
    """S Plus USB identities and existing S/X devices reach the wallet wizard."""
    entries = [
        dict(vendor_id=0x2c97, product_id=product_id, path=b"ledger", serial_number="test",
            interface_number=0, usage_page=0xffa0),
        dict(vendor_id=0x1234, product_id=product_id, path=b"other", serial_number="other",
            interface_number=0, usage_page=0xffa0),
    ]
    monkeypatch.setattr(hid, "enumerate", lambda *args: entries)
    monkeypatch.setattr(ledger, "app_state", SimpleNamespace(device_manager=DeviceMgr()))

    plugin = ledger.LedgerPlugin("ledger")
    devices = plugin.enumerate_devices()

    assert len(devices) == 1
    assert devices[0].path == b"ledger"
    assert devices[0].product_key == (0x2c97, product_id)


class FakeBitcoinCashApp:
    """Model the public-key path restriction introduced by BCH app 2.4.10.

    This is a host-side test double, not a Ledger firmware emulator. The seed
    below is the public BIP32 test vector, never a user wallet.
    """

    def __init__(self, restricted):
        self.restricted = restricted
        self.root = BIP32PrivateKey.from_seed(bytes(range(16)), Net.COIN)
        self.paths = []

    def getFirmwareVersion(self):
        return {"version": "2.4.13" if self.restricted else "2.0.3"}

    def getOperationMode(self):
        return 1

    def getVerifyPinRemainingAttempts(self):
        # Modern Nano devices unlock on the device, not through the HW.1 PIN API.
        raise ledger.BTChipException("Unsupported instruction", sw=0x6d00)

    def getWalletPublicKey(self, path):
        self.paths.append(path)
        indices = bip32_decompose_chain_string("m/" + path)
        if self.restricted:
            allowed = (len(indices) >= 2 and indices[1] in (0x80000000, 0x80000091))
            allowed = allowed or (indices and indices[0] in (0x8000002d, 0x80454c45))
            if not allowed:
                raise ledger.BTChipException("Invalid status 6f00", sw=0x6f00)
        node = self.root
        for index in indices:
            node = node.child_safe(index)
        return {
            "publicKey": node.public_key.to_bytes(compressed=False),
            "chainCode": node.derivation().chain_code,
        }


@pytest.mark.parametrize("restricted", [False, True], ids=["legacy-app", "hardened-app"])
@pytest.mark.parametrize("wallet_path", ["m/44'/0'/0'", "m/44'/145'/0'"])
def test_setup_preserves_wallet_keys(monkeypatch, coin, restricted, wallet_path):
    app = FakeBitcoinCashApp(restricted)
    monkeypatch.setattr(ledger, "btchip", lambda transport: app)
    client = ledger.Ledger_Client(None)
    manager = Mock()
    manager.client_by_id.return_value = client
    monkeypatch.setattr(ledger, "app_state", SimpleNamespace(device_manager=manager))
    plugin = ledger.LedgerPlugin("ledger")
    monkeypatch.setattr(plugin, "create_handler", lambda wizard: Mock())
    device_info = SimpleNamespace(device=SimpleNamespace(id_="test-ledger"))

    # The old setup probe tries m/44', which the hardened app refuses.
    plugin.setup_device(device_info, None)
    assert client.preflightDone

    # Setup is only a probe: the user's selected/stored account still determines
    # the complete xpub, including its parent fingerprint and child number.
    actual = plugin.get_master_public_key("test-ledger", wallet_path, None)
    expected = app.root
    for index in bip32_decompose_chain_string(wallet_path):
        expected = expected.child_safe(index)
    assert actual.to_extended_key_string() == expected.public_key.to_extended_key_string()


def test_setup_propagates_device_failure(monkeypatch):
    """A failed device probe must not advance the wallet setup silently."""
    client = Mock()
    client.get_master_public_key.side_effect = ledger.BTChipException("Disconnected")
    manager = Mock()
    manager.client_by_id.return_value = client
    monkeypatch.setattr(ledger, "app_state", SimpleNamespace(device_manager=manager))
    plugin = ledger.LedgerPlugin("ledger")
    monkeypatch.setattr(plugin, "create_handler", lambda wizard: Mock())

    with pytest.raises(ledger.BTChipException, match="Disconnected"):
        plugin.setup_device(SimpleNamespace(device=SimpleNamespace(id_="test-ledger")), None)
