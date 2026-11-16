"""Qt account flow and thread ownership, with simulated device I/O."""
from concurrent.futures import Future, ThreadPoolExecutor
from types import SimpleNamespace
from unittest.mock import Mock
import threading

import pytest
pytest.importorskip('PyQt5')
from PyQt5.QtCore import QObject, pyqtSignal
from PyQt5.QtWidgets import QApplication, QWidget

from electrumsv.app_state import AppStateProxy, app_state
from electrumsv.simple_config import SimpleConfig
from electrumsv.exceptions import UserCancelled
from electrumsv.gui.qt import account_wizard as aw
from electrumsv.gui.qt import hardware_accounts as ha
from electrumsv.gui.qt.hardware import run_device_operation


@pytest.fixture
def gui(tmp_path):
    app = QApplication.instance() or QApplication([])
    previous = app_state._proxy
    AppStateProxy(SimpleConfig({'electrum_sv_path': str(tmp_path)}), 'qt')
    yield app
    app_state.set_proxy(previous)


def test_hardware_account_pages_are_reachable(gui):
    window = QWidget()
    window._wallet = Mock()
    wizard = aw.AccountWizard(window)
    assert wizard.page(aw.AccountPage.FIND_HARDWARE_WALLET) is not None
    assert wizard.page(aw.AccountPage.SETUP_HARDWARE_WALLET) is not None
    assert aw.ResultType.HARDWARE.value == 4


def test_device_work_runs_off_gui_thread_and_returns_on_gui_thread(gui):
    class Bridge(QObject):
        done = pyqtSignal(object)
    bridge = Bridge()
    gui_thread = threading.get_ident()
    with ThreadPoolExecutor(max_workers=1) as executor:
        def run(func, on_done):
            bridge.done.connect(on_done)
            future = executor.submit(func)
            future.add_done_callback(bridge.done.emit)
            return future
        app_state.app = SimpleNamespace(run_in_thread=run)
        parent = QWidget()
        worker_thread = run_device_operation(parent, threading.get_ident)
    assert worker_thread != gui_thread


def test_cancelled_setup_cannot_create_an_account(gui, monkeypatch):
    window = QWidget()
    window._wallet = Mock()
    wizard = aw.AccountWizard(window)
    wizard.set_selected_device(('ledger', SimpleNamespace(device=SimpleNamespace(id_='test'))))
    support = Mock()
    support.connect.side_effect = UserCancelled()
    app_state.device_manager.hardware = support
    monkeypatch.setattr(ha, 'run_device_operation', lambda parent, task: task())
    page = wizard.page(aw.AccountPage.SETUP_HARDWARE_WALLET)
    page._initiate_setup()
    assert not page.isComplete()
    assert page._plugin is None
    assert 'cancelled' in page._plugin_debug_message
    window._wallet.create_account_from_keystore.assert_not_called()


def test_scan_completion_ignores_departed_page(gui):
    window = QWidget()
    window._wallet = Mock()
    wizard = aw.AccountWizard(window)
    page = wizard.page(aw.AccountPage.FIND_HARDWARE_WALLET)
    page._alive = False
    future = Future()
    future.set_exception(OSError('device unplugged'))
    page._on_scan_complete(future, page._scan_generation)
    assert not page.isComplete()


def test_old_scan_cannot_replace_results_after_reentering_page(gui):
    window = QWidget()
    window._wallet = Mock()
    wizard = aw.AccountWizard(window)
    page = wizard.page(aw.AccountPage.FIND_HARDWARE_WALLET)
    page._alive = True
    page._scan_generation = 2
    page._devices = ['current scan']
    future = Future()
    future.set_result(SimpleNamespace(devices=['old scan'], errors={}))
    page._on_scan_complete(future, 1)
    assert page._devices == ['current scan']


def test_input_selection_never_offers_already_signed_or_foreign_inputs(gui):
    from PyQt5.QtCore import Qt
    from electrumsv.tests.test_external_signing import transaction, CAPABILITIES, sign
    from electrumsv.devices.signing import prepare_signing_request, apply_signing_result
    from electrumsv.transaction import TransactionContext
    from electrumsv.gui.qt.hardware import SigningInputsDialog
    tx, keys = transaction()
    store = SimpleNamespace(is_signature_candidate=lambda key: key.to_public_key() == keys[1].public_key)
    parent = QWidget()
    dialog = SigningInputsDialog(parent, tx, [store])
    assert dialog.selected_indexes() == {1}
    assert dialog.inputs.topLevelItemCount() == 1
    dialog.inputs.topLevelItem(0).setCheckState(0, Qt.Unchecked)
    assert not dialog.selected_indexes()
    assert not dialog.sign_button.isEnabled()
    request = prepare_signing_request(tx, TransactionContext(), store.is_signature_candidate, CAPABILITIES)
    apply_signing_result(tx, request, sign(request, keys[1]))
    finished = SigningInputsDialog(parent, tx, [store])
    assert finished.inputs.topLevelItemCount() == 0


def test_message_hash_preserves_spaces_newlines_and_unicode(gui):
    from PyQt5.QtWidgets import QTextEdit
    from electrumsv.gui.qt.hardware import MessageHashReview
    import hashlib
    edit = QTextEdit()
    review = MessageHashReview(edit)
    for text in [' a\nb ', '矿工签名\n', '']:
        edit.setPlainText(text)
        assert review.digest.text() == hashlib.sha256(text.encode('utf8')).hexdigest().upper()


def test_status_multiple_devices_require_selection_and_check_selected_account(gui, monkeypatch):
    from electrumsv.devices.hardware import DeviceStatus
    from electrumsv.gui.qt import hardware
    parent = QWidget()
    store = SimpleNamespace(hw_type='ledger', get_derivation=lambda: "m/44'/0'/1'")
    support = Mock()
    support.status.return_value = [DeviceStatus('ledger', 'Nano S', 'locked', 'Unlock Nano S',
        device=SimpleNamespace(id_='s')), DeviceStatus('ledger', 'Nano X', 'bch_ready', 'BCH ready',
        app='Bitcoin Cash', version='2.4.14', device=SimpleNamespace(id_='x'))]
    app_state.device_manager.hardware = support
    monkeypatch.setattr(hardware, 'run_device_operation', lambda parent, task: task())
    dialog = hardware.HardwareWalletDialog(parent, [store])
    dialog.refresh()
    dialog.show()
    gui.processEvents()
    assert dialog.devices.currentItem() is None
    assert not dialog.verify_button.isEnabled()
    dialog.devices.setCurrentItem(dialog.devices.topLevelItem(1))
    assert dialog.verify_button.isEnabled()
    dialog.verify_selected()
    assert support.verify_device.call_args.args[0].device.id_ == 'x'
    assert support.verify_device.call_args.args[1] == [store]
    assert 'Matches' in dialog.details.text()
    support.verify_device.side_effect = UserCancelled()
    dialog.verify_selected()
    assert 'cancelled' in dialog.details.text()


def test_cancelled_password_restores_transaction_signing_controls(gui):
    from electrumsv.gui.qt.transaction_dialog import TxDialog
    from electrumsv.tests.test_external_signing import transaction
    tx, _ = transaction()
    window = Mock()
    window.sign_tx.return_value = None  # @protected exits before starting a worker.
    dialog = SimpleNamespace(tx=tx, _payment_request=None, _main_window=window,
        sign_button=Mock(), partial_sign_button=Mock(), update=Mock())
    TxDialog.sign(dialog, input_indexes={1})
    window.sign_tx.assert_called_once()
    assert window.sign_tx.call_args.kwargs['input_indexes'] == {1}
    dialog.update.assert_called_once()
    window.pop_top_level_window.assert_called_once_with(dialog)
