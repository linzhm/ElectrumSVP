# Ledger compatibility in ElectrumSVP

ElectrumSVP uses the Ledger **Bitcoin Cash** app through the legacy `btchip`
protocol to sign BSV transactions. With Bitcoin and Bitcoin Cash installed,
open **Bitcoin Cash** on the device before using the wallet. The device signs;
ElectrumSVP assembles the transaction and broadcasts it to the BSV network.

## Nano S Plus compatibility update

This update addresses two host-side problems:

- Discovery now includes Nano S Plus product IDs `0005`, `5000`, `5011` and
  `5015` under Ledger vendor ID `2c97`. Existing device IDs are preserved.
- Setup probes `m/44'/145'/0'`. The old probe, `m/44'/0'`, also requested its
  parent `m/44'` to calculate the xpub fingerprint. Bitcoin Cash app 2.4.10
  introduced path restrictions that reject that parent with status `6f00`.
  The new probe and its parent are both within the allowed paths.

The probe's result is discarded. It does **not** change the wallet's selected
path, stored xpub, receiving addresses or change addresses. Existing wallets
must continue to use their original derivation, commonly `m/44'/0'/0'` or
`m/44'/145'/0'`. The current BCH app's allowed paths do not include coin type
`236'`; choosing that path is not a compatibility fix.

This is a compatibility candidate, **not a claim of verified Nano S Plus BSV
signing**. Device OS and app versions are separate; record both when testing.

## Using a device

1. Connect and unlock the Ledger using its own buttons.
2. Open the **Bitcoin Cash** app on the device.
3. Close Ledger Wallet / Ledger Live and other software accessing the device.
4. Use ElectrumSVP's hardware-wallet setup, preserving the original derivation
   when recovering an existing account.
5. Verify a receiving address on the device before receiving funds. A failed
   display request is not verification: the address must appear and match on
   the device's screen.

Do not enter a recovery phrase into the computer to test this integration.
Use a dedicated test device/account and small amounts. Do not update or attempt
to downgrade a funded device merely to reproduce a compatibility problem.

## Signing scope and verification

The signing code and hardware dependency pins are unchanged. The existing
implementation deliberately sends outpoints and amounts without full parent
transactions, because BSV parent transactions can exceed hardware parsing
limits. This can produce an **unverified inputs** warning. Electron Cash's
trusted-input implementation needs a separate evaluation of BSV transaction
limits and fee verification before it can be adopted.

Before declaring a Nano S Plus OS/app combination supported, complete:

| Check | Current evidence |
| --- | --- |
| USB discovery, restricted-path setup, old account xpubs | Automated tests with test doubles |
| Physical device OS and BCH app versions | Not yet recorded |
| Receiving and change addresses on the device | Physical-device test pending |
| BSV P2PKH signing: one/multiple inputs, with/without change | Physical-device test pending |
| Independent verification of signatures, outputs, fees and BSV broadcast | Physical-device test pending |
| Rejection, disconnect, reconnect and reopening | Physical-device test pending |
| Large parent transactions, data outputs and multisig | Separate evaluation pending |

The device tests should check `SIGHASH_ALL | FORKID` (`0x41`) signatures and
confirm the recipient and amount on the device. Software tests do not establish
what its screen displays or which transactions its app will accept.

## Regression tests

With the project runtime dependencies, `pytest` and the optional Ledger
hardware libraries installed:

```sh
python -m pytest -q electrumsv/tests/test_ledger.py \
  electrumsv/tests/test_keystore.py electrumsv/tests/test_transaction.py
```

The Ledger tests simulate USB enumeration and BCH public-key path restrictions;
they never open a physical device. Full xpubs are compared with a public BIP32
test vector on both wallet networks, for both historical account paths and
older unrestricted app behavior. These network cases test xpub encoding, not
device support for testnet signing. Tests skip if the hardware libraries are
absent; a skipped run is not a Ledger validation result.

On Apple Silicon, building pinned `hidapi==0.10.0` may require older build tools
(`Cython==0.29.37`, `setuptools==65.7.0`) with build isolation disabled. If its
import fails with `_NSAppKitVersionNumber`, initialize AppKit before running
headless tests:

```sh
python - <<'PYTEST'
import ctypes
ctypes.CDLL('/System/Library/Frameworks/AppKit.framework/AppKit', mode=ctypes.RTLD_GLOBAL)
import pytest
raise SystemExit(pytest.main([
    '-q', 'electrumsv/tests/test_ledger.py',
    'electrumsv/tests/test_keystore.py', 'electrumsv/tests/test_transaction.py',
]))
PYTEST
```

These are local test-environment workarounds, not changes to the published
requirements. The existing pins document historical macOS exit crashes;
dependency modernization needs its own platform testing.

## Upstream references

- [Electron Cash setup fix, PR #3265](https://github.com/Electron-Cash/Electron-Cash/pull/3265)
- [Electron Cash Ledger device list](https://github.com/Electron-Cash/Electron-Cash/blob/694c4d89bdb21447f00bec0375522356743e4e66/electroncash_plugins/ledger/ledger.py)
- [Ledger BCH 2.4.13 changelog](https://github.com/LedgerHQ/app-bitcoin-legacy/blob/fa287121d830a9ea4518babd09993764d32439fb/CHANGELOG.md)
- [BCH app path permissions and ForkID](https://github.com/LedgerHQ/app-bitcoin-legacy/blob/fa287121d830a9ea4518babd09993764d32439fb/Makefile)
- [BCH device-side 0x41 signing](https://github.com/LedgerHQ/lib-app-bitcoin/blob/e369bc30a714b94a110b9f78f3e0569dcba83285/handler/hash_sign.c#L120-L133)
