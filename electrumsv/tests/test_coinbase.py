"""Mining rewards: output indices, maturity and previous-transaction encoding."""
import threading
from types import SimpleNamespace
from unittest.mock import Mock

from bitcoinx import PrivateKey, Script
import pytest

from electrumsv.bitcoin import is_coinbase_mature
from electrumsv.constants import ScriptType, TransactionOutputFlag, TxFlags
from electrumsv.transaction import Transaction, XTxInput, XTxOutput
from electrumsv.wallet import AbstractAccount, Wallet
from electrumsv.wallet_database import (DatabaseContext, SynchronousWriter, TransactionCache,
    TransactionTable, TxData, TxProof)
from electrumsv.wallet_database.migration import create_database, update_database


@pytest.mark.parametrize('height,tip,mature', [
    (500, 598, False), (500, 599, True), (500, 600, True),
    (0, 1000, False), (-1, 1000, False), (None, 1000, False), (1001, 1000, False)])
def test_reward_balance_and_coin_selection_agree(height, tip, mature):
    reward = SimpleNamespace(tx_hash=b'c' * 32, value=1000, is_coinbase=True)
    account = SimpleNamespace(_utxos_lock=threading.RLock(), _utxos={0: reward},
        _wallet=SimpleNamespace(get_local_height=lambda: tip,
            _transaction_cache=SimpleNamespace(get_flags=lambda _: TxFlags.StateSettled)),
        get_transaction_metadata=lambda _: TxData(height=height))
    assert is_coinbase_mature(height, tip) == mature
    assert bool(AbstractAccount.get_utxos(account, mature=True)) == mature
    expected = (1000, 0, 0) if mature else (0, 0, 1000)
    if height is None or height <= 0:
        expected = (0, 0, 0)
    assert AbstractAccount.get_balance(account) == expected


def coinbase_transaction():
    key = PrivateKey.from_int(1).public_key
    # Output 0 is data; the wallet's reward is output 2, not the block position (0).
    return Transaction(2, [XTxInput(bytes(32), 0xffffffff,
        Script(bytes.fromhex('0350c300')), 0xffffffff)], [
        XTxOutput(0, Script(bytes.fromhex('6a'))),
        XTxOutput(100, PrivateKey.from_int(2).public_key.P2PKH_script()),
        XTxOutput(900, key.P2PKH_script())], 0)


def test_coinbase_reward_index_and_flag_survive_wallet_indexing():
    tx = coinbase_transaction()
    assert tx.is_coinbase()
    account = Mock()
    account._sync_state.get_transaction_key_ids.return_value = [7]
    account._sync_state.get_key_history.return_value = []
    account.get_keyinstance.return_value = SimpleNamespace(keyinstance_id=7)
    account._get_cached_script.return_value = (tx.outputs[2].script_pubkey,
        bytes(tx.outputs[2].script_pubkey), None)
    account.get_utxo.return_value = None
    account.get_stxo.return_value = None
    account._keyinstances = {7: account.get_keyinstance.return_value}
    assert AbstractAccount._process_key_usage(account, tx.hash(), tx, [(2, tx.outputs[2])])
    args = account.create_transaction_output.call_args.args
    assert args[1] == 2
    assert args[2] == 900
    assert args[3] & TransactionOutputFlag.IS_COINBASE


def test_trezor_coinbase_previous_input_preserves_null_outpoint():
    pytest.importorskip('trezorlib')
    from electrumsv.devices.trezor.trezor import TrezorPlugin
    tx = coinbase_transaction()
    plugin = TrezorPlugin('trezor')
    encoded = plugin.tx_inputs(tx, is_prev_tx=True)[0]
    assert encoded.prev_hash == bytes(32)
    assert encoded.prev_index == 0xffffffff
    assert encoded.script_sig == bytes(tx.inputs[0].script_sig)
    assert encoded.sequence == 0xffffffff


@pytest.fixture
def reward_cache(tmp_path):
    context = DatabaseContext(str(tmp_path / 'rewards.sqlite'))
    connection = context.acquire_connection()
    create_database(connection)
    update_database(connection)
    store = TransactionTable(context)
    cache = TransactionCache(store)
    tx = coinbase_transaction()
    with SynchronousWriter() as writer:
        cache.add([(tx.hash(), TxData(height=500, position=0), tx,
            TxFlags.StateSettled, None)], completion_callback=writer.get_callback())
        assert writer.succeeded()
    with SynchronousWriter() as writer:
        cache.update_proof(tx.hash(), TxProof(0, [b'p' * 32]), writer.get_callback())
        assert writer.succeeded()
    yield cache, store, tx
    store.close()
    context.release_connection(connection)
    context.close()


def eligible(cache, tx_hash, tip=599, **kwargs):
    wallet = SimpleNamespace(_transaction_cache=cache, get_local_height=lambda: tip)
    return Wallet.is_transaction_chain_valid(wallet, tx_hash, **kwargs)


def flush(store):
    with SynchronousWriter() as writer:
        store._db_context.queue_write(lambda db: None, writer.get_callback())
        assert writer.succeeded()


@pytest.mark.parametrize('cause', ['reorg', 'history_removed', 'height_changed'])
def test_reward_proof_revoked_persistently_and_reconfirmation_needs_new_proof(reward_cache, cause):
    cache, store, tx = reward_cache
    tx_hash = tx.hash()
    assert eligible(cache, tx_hash)
    assert not eligible(cache, tx_hash, tip=598)
    assert cache.get_proof(tx_hash) is not None
    with SynchronousWriter() as writer:
        callback = writer.get_callback()
        if cause == 'reorg':
            count, hashes = cache.apply_reorg(499, callback)
            assert (count, hashes) == (1, [tx_hash])
        elif cause == 'history_removed':
            assert cache.invalidate_coinbase(tx_hash, callback)
        else:
            cache.update([(tx_hash, TxData(height=501), None, TxFlags.HasHeight)], callback)
        assert writer.succeeded()
    assert not eligible(cache, tx_hash, tip=1000)
    assert cache.get_proof(tx_hash) is None
    reloaded = TransactionCache(store)
    assert reloaded.get_proof(tx_hash) is None
    assert reloaded.get_transaction(tx_hash).is_coinbase()
    assert not reloaded.settle_verified(tx_hash, 500, 0, TxProof(0, []))
    # A new reported height alone must not restore the old verified state.
    with SynchronousWriter() as writer:
        reloaded.update([(tx_hash, TxData(height=501), None, TxFlags.HasHeight)],
            writer.get_callback())
        # A no-op update has no writer callback.
        if cause != 'height_changed':
            assert writer.succeeded()
    assert not eligible(reloaded, tx_hash, tip=1000)
    assert reloaded.settle_verified(tx_hash, 501, 0, TxProof(0, [b'n' * 32]))
    flush(store)
    final = TransactionCache(store)
    assert eligible(final, tx_hash, tip=600)
    assert not eligible(final, tx_hash, tip=599)
    assert final.get_proof(tx_hash).branch == [b'n' * 32]


def test_orphan_descendant_outputs_are_not_spendable(reward_cache):
    cache, store, reward = reward_cache
    child = Transaction(2, [XTxInput(reward.hash(), 2, Script(), 0xffffffff)],
        [XTxOutput(800, reward.outputs[2].script_pubkey)], 0)
    with SynchronousWriter() as writer:
        cache.add([(child.hash(), TxData(height=0), child, TxFlags.StateCleared, None)],
            writer.get_callback())
        assert writer.succeeded()
    assert eligible(cache, child.hash())
    with SynchronousWriter() as writer:
        assert cache.invalidate_coinbase(reward.hash(), writer.get_callback())
        assert writer.succeeded()
    assert not eligible(cache, child.hash())
    assert not eligible(cache, child.hash(), for_spending=False)
    assert cache.get_transaction(child.hash()) is not None
    assert not cache.invalidate_coinbase(child.hash())


def test_invalid_coin_selection_is_rejected_before_fee_estimation():
    account = SimpleNamespace(_wallet=SimpleNamespace(is_transaction_chain_valid=lambda _: False))
    with pytest.raises(ValueError, match='mining reward'):
        AbstractAccount.make_unsigned_transaction(account, [SimpleNamespace(tx_hash=b'x' * 32)],
            [], Mock())


@pytest.mark.parametrize('same_block', [False, True])
def test_merkle_reply_from_superseded_block_is_not_admitted(same_block):
    import asyncio
    from electrumsv.network import Network
    tx_hash = b't' * 32
    old_header = SimpleNamespace(merkle_root=tx_hash, hash=b'o' * 32, timestamp=1)
    current_header = old_header if same_block else SimpleNamespace(hash=b'n' * 32)
    async def request_proof(tx_id, height):
        return {'merkle': [], 'pos': 0}
    async def headers_at_heights(heights):
        return {500: old_header}
    session = SimpleNamespace(logger=Mock(), request_proof=request_proof,
        headers_at_heights=headers_at_heights)
    async def main_session():
        return session
    network = SimpleNamespace(_main_session=main_session,
        chain=lambda: SimpleNamespace(header_at_height=lambda height: current_header))
    wallet = Mock()
    assert not asyncio.run(Network._request_proofs(network, wallet, {tx_hash: 500}))
    assert wallet.add_transaction_proof.called == same_block


def test_history_shows_invalidated_reward_as_unverified_not_normal_mempool(reward_cache):
    from electrumsv.gui.qt.history_list import get_tx_status, TxStatus
    cache, store, reward = reward_cache
    with SynchronousWriter() as writer:
        cache.invalidate_coinbase(reward.hash(), writer.get_callback())
        assert writer.succeeded()
    account = SimpleNamespace(have_transaction_data=lambda tx_hash: True,
        get_transaction_metadata=cache.get_metadata, get_transaction=cache.get_transaction)
    assert get_tx_status(account, reward.hash(), 0, 0, False) == TxStatus.REWARD_UNVERIFIED


@pytest.mark.parametrize('another_key_reports_reward', [False, True])
def test_reward_removed_from_all_subscriptions_is_invalidated(reward_cache, monkeypatch,
        another_key_reports_reward):
    import asyncio
    from bitcoinx import hash_to_hex_str
    from electrumsv.wallet import SyncState
    from electrumsv.app_state import app_state
    cache, store, reward = reward_cache
    sync = SyncState()
    tx_id = hash_to_hex_str(reward.hash())
    sync.set_key_history(7, [(tx_id, 500)])
    if another_key_reports_reward:
        sync.set_key_history(8, [(tx_id, 500)])
    account = SimpleNamespace(_stopped=False, lock=threading.RLock(), _logger=Mock(),
        _sync_state=sync, _keyinstances={7: SimpleNamespace(script_type=ScriptType.P2PKH)},
        synchronize=Mock())
    account._wallet = SimpleNamespace(_transaction_cache=cache, get_accounts=lambda: [account],
        txs_changed_event=Mock(), trigger_callback=Mock())
    # Completion is deliberately deferred; this test only exercises history admission/invalidation.
    monkeypatch.setattr('electrumsv.wallet.app_state', SimpleNamespace(
        app=SimpleNamespace(run_in_thread=lambda function: None)))
    asyncio.run(AbstractAccount.set_key_history(account, 7, ScriptType.P2PKH, [], {}))
    flush(store)
    assert bool(cache.get_flags(reward.hash()) & TxFlags.StateSettled) == another_key_reports_reward
