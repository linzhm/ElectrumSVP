from types import SimpleNamespace

from bitcoinx import PrivateKey, Script
import pytest

from electrumsv.constants import ScriptType
from electrumsv.devices.hardware import HardwareError
from electrumsv.devices.ledger.input_verification import plan_input_verification, parent_work
from electrumsv.transaction import Transaction, TransactionContext, XPublicKey, XTxInput, XTxOutput


def fixture():
    key = PrivateKey.from_int(1).public_key
    context, inputs = TransactionContext(), []
    for number in range(2):
        parent = Transaction(2, [XTxInput(bytes(32), 0xffffffff,
            Script(b'public coinbase fixture' + bytes([number])), 0xffffffff)],
            [XTxOutput(0, Script(b'\x6a')), XTxOutput(1000, key.P2PKH_script())], 0)
        context.prev_txs[parent.hash()] = parent
        inputs.append(XTxInput(parent.hash(), 1, Script(), 0xfffffffe, value=1000,
            x_pubkeys=[XPublicKey(pubkey_bytes=key.to_bytes())], threshold=1,
            signatures=[b'\xff'], script_type=ScriptType.P2PKH))
    return Transaction(2, inputs, [XTxOutput(900, key.P2PKH_script()),
        XTxOutput(1000, key.P2PKH_script())], 0), context


@pytest.mark.parametrize('model', ['Nano S', 'Nano S Plus', 'Nano X'])
def test_small_two_input_two_output_coinbase_parents_are_verified(model):
    tx, context = fixture()
    plan = plan_input_verification(tx, context, model, '2.4.7')
    assert plan.trusted
    assert len(plan.parents) == 2
    for raw, parent in zip(plan.parents, context.prev_txs.values()):
        assert len(raw) == parent_work(parent)[0]
        assert Transaction.from_bytes(raw).is_coinbase()


@pytest.mark.parametrize('error', ['hash', 'index', 'amount', 'script'])
def test_inconsistent_parents_are_errors_not_unverified_fallback(error):
    tx, context = fixture()
    parent = context.prev_txs[tx.inputs[0].prev_hash]
    if error == 'hash':
        parent.locktime = 1
    elif error == 'index':
        tx.inputs[0].prev_idx = 7
    elif error == 'amount':
        tx.inputs[0].value = 999
    else:
        tx.inputs[0].x_pubkeys[0] = XPublicKey(pubkey_bytes=PrivateKey.from_int(2).public_key.to_bytes())
    with pytest.raises(HardwareError, match='does not match'):
        plan_input_verification(tx, context, 'Nano S', '2.4.7')


def test_two_inputs_can_still_exceed_legacy_parent_script_budget():
    tx, context = fixture()
    parent = context.prev_txs.pop(tx.inputs[0].prev_hash)
    parent.outputs[0].script_pubkey = Script(b'\x6a' + b'x' * 20000)
    tx.inputs[0].prev_hash = parent.hash()
    context.prev_txs[parent.hash()] = parent
    assert not plan_input_verification(tx, context, 'Nano S', '2.4.7').trusted
    assert plan_input_verification(tx, context, 'Nano S Plus', '2.4.14').trusted


def test_missing_parent_is_explicitly_unverified():
    tx, context = fixture()
    context.prev_txs.clear()
    plan = plan_input_verification(tx, context, 'Nano X', '2.4.14')
    assert not plan.trusted and 'unavailable' in plan.reason


def test_declining_reduced_verification_never_starts_transaction_signing():
    pytest.importorskip('btchip')
    from unittest.mock import Mock
    from electrumsv.devices.ledger.ledger import Ledger_KeyStore
    from electrumsv.exceptions import UserCancelled
    tx, context = fixture()
    context.prev_txs.clear()
    client = Mock()
    client.getFirmwareVersion.return_value = {'version': '2.4.7'}
    store = Mock()
    store.handler.yes_no_question.return_value = False
    store.get_client_electrum.return_value = SimpleNamespace(_device=None, dongleObject=client)
    with pytest.raises(UserCancelled):
        Ledger_KeyStore.sign_transaction(store, tx, '', context)
    client.getTrustedInput.assert_not_called()
    client.startUntrustedTransaction.assert_not_called()
