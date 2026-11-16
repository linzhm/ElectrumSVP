"""Run device I/O while keeping Qt handlers and dialogs on the GUI thread."""
from PyQt5.QtCore import QTimer, Qt
from PyQt5.QtWidgets import (QAction, QLabel, QVBoxLayout, QHBoxLayout, QPushButton,
    QTreeWidget, QTreeWidgetItem, QLineEdit, QWidget, QCheckBox)

from electrumsv.app_state import app_state
from electrumsv.exceptions import UserCancelled
from electrumsv.i18n import _
from .util import WindowModalDialog, read_QIcon


def show_unavailable_hardware(window, keystore):
    action = QAction(read_QIcon('icons8-usb-disconnected-80.png'),
        keystore.label or keystore.hw_type, window)
    action.setToolTip(_('Hardware wallet dependencies unavailable'))
    action.triggered.connect(lambda: show_hardware_wallets(window))
    window.add_toolbar_action(action)


class DeviceOperationDialog(WindowModalDialog):
    def __init__(self, parent, operation):
        super().__init__(parent, _('Hardware wallet'))
        self.setWindowFlag(Qt.WindowCloseButtonHint, False)
        self._operation = operation
        self._finished = False
        self._result = None
        self._error = None
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel(_('Follow the instructions on your device.\n'
            'To cancel, reject the request on the device.')))
        QTimer.singleShot(0, self._start)

    def _start(self):
        try:
            app_state.app.run_in_thread(self._operation, on_done=self._done)
        except Exception as exc:
            self._error = exc
            self._finished = True
            self.accept()

    def _done(self, future):
        try:
            self._result = future.result()
        except Exception as exc:
            self._error = exc
        self._finished = True
        self.accept()

    def reject(self):
        # A running USB call cannot safely be cancelled by abandoning its dialogs.
        if self._finished:
            super().reject()

    def closeEvent(self, event):
        if not self._finished:
            event.ignore()
        else:
            super().closeEvent(event)

    def run(self):
        self.exec_()
        if self._error is not None:
            raise self._error
        return self._result


def run_device_operation(parent, operation):
    return DeviceOperationDialog(parent, operation).run()


class HardwareWalletDialog(WindowModalDialog):
    """Connection diagnostics and explicit account-key comparison; never signs or installs."""
    def __init__(self, parent, stores=()):
        super().__init__(parent, _('Hardware wallets'))
        self.stores = stores
        self.setMinimumSize(700, 400)
        layout = QVBoxLayout(self)
        intro = QLabel(_('To add an account, choose Add account → Connect hardware wallet. '
            'For an existing account, use its Keys tab to display an address or sign a message.'))
        intro.setWordWrap(True)
        layout.addWidget(intro)
        paths = [store.get_derivation() for store in stores if hasattr(store, 'hw_type')]
        account_note = QLabel(_('Current account paths: {}').format(', '.join(paths)) if paths
            else _('No hardware account is selected. Connection status is available below.'))
        account_note.setTextFormat(Qt.PlainText)
        account_note.setWordWrap(True)
        layout.addWidget(account_note)
        self.devices = QTreeWidget()
        self.devices.setHeaderLabels([_('Device'), _('Status'), _('Application / version')])
        self.devices.setRootIsDecorated(False)
        self.devices.currentItemChanged.connect(self._selected)
        layout.addWidget(self.devices)
        self.details = QLabel(_('Refresh to read connection status. This does not sign anything.'))
        self.details.setTextFormat(Qt.PlainText)
        self.details.setWordWrap(True)
        layout.addWidget(self.details)
        buttons = QHBoxLayout()
        self.refresh_button = QPushButton(_('Refresh'))
        self.refresh_button.clicked.connect(self.refresh)
        buttons.addWidget(self.refresh_button)
        self.verify_button = QPushButton(_('Connect and check current account'))
        self.verify_button.setEnabled(False)
        self.verify_button.clicked.connect(self.verify_selected)
        buttons.addWidget(self.verify_button)
        buttons.addStretch(1)
        close = QPushButton(_('Close'))
        close.clicked.connect(self.accept)
        buttons.addWidget(close)
        layout.addLayout(buttons)

    def _selected(self, current, previous):
        if current is not None:
            self.details.setText(current.data(0, Qt.UserRole))
            status = current.data(0, Qt.UserRole + 1)
            self.verify_button.setEnabled(status.device is not None and any(
                getattr(store, 'hw_type', None) == status.kind for store in self.stores))
        else:
            self.verify_button.setEnabled(False)

    def verify_selected(self):
        item = self.devices.currentItem()
        if item is None:
            return
        status = item.data(0, Qt.UserRole + 1)
        support = app_state.device_manager.hardware
        self.details.setText(_('Unlock the selected device on its own screen. '
            'Confirm any app-opening request. Then the account public key will be checked.'))
        try:
            handler = support.plugin(status.kind).create_handler(self.parent())
            run_device_operation(self, lambda: support.verify_device(status, self.stores, handler))
            self.details.setText(_('Matches a hardware key in the current account. '
                'The complete account public key was checked. It will be checked again before use.'))
        except UserCancelled:
            self.details.setText(_('Account check cancelled. Select the device and retry.'))
        except Exception as exc:
            self.details.setText(str(exc))

    def refresh(self):
        states = {'not_detected': _('Not detected'), 'unavailable': _('Unavailable'),
            'connected': _('Connected; pairing unchecked'), 'locked': _('Locked'),
            'bch_ready': _('Bitcoin Cash open'), 'app_not_open': _('Open Bitcoin Cash to sign')}
        self.refresh_button.setEnabled(False)
        try:
            rows = run_device_operation(self, app_state.device_manager.hardware.status)
            self.devices.clear()
            for row in rows:
                item = QTreeWidgetItem([row.label, states[row.state],
                    ' '.join(value for value in (row.app, row.version) if value)])
                item.setData(0, Qt.UserRole, row.detail)
                item.setData(0, Qt.UserRole + 1, row)
                self.devices.addTopLevelItem(item)
            self.devices.resizeColumnToContents(0)
            self.devices.resizeColumnToContents(1)
            if len(rows) == 1:
                self.devices.setCurrentItem(self.devices.topLevelItem(0))
            else:
                self.devices.setCurrentItem(None)
                self.devices.clearSelection()
                # Qt otherwise chooses the first row when this view receives initial focus.
                self.refresh_button.setFocus(Qt.OtherFocusReason)
        except Exception as exc:
            self.devices.clear()
            self.details.setText(str(exc))
        finally:
            self.refresh_button.setEnabled(True)


def show_hardware_wallets(parent):
    account = getattr(parent, '_account', None)
    dialog = HardwareWalletDialog(parent, account.get_keystores() if account is not None else ())
    QTimer.singleShot(0, dialog.refresh)
    dialog.exec_()


class MessageHashReview(QWidget):
    """Digest shown by Ledger's BCH app; this is not the Bitcoin message-signing digest."""
    def __init__(self, message_edit):
        super().__init__()
        import hashlib
        layout = QVBoxLayout(self)
        note = QLabel(_('Ledger may display only a message hash. Compare it with this SHA-256 '
            'of the exact UTF-8 message, including spaces and newlines. '
            'The device has not displayed the message text.'))
        note.setWordWrap(True)
        layout.addWidget(note)
        self.digest = QLineEdit()
        self.digest.setReadOnly(True)
        layout.addWidget(self.digest)
        def update():
            self.digest.setText(hashlib.sha256(message_edit.toPlainText().encode('utf8')).hexdigest().upper())
        message_edit.textChanged.connect(update)
        update()


class LedgerSigningOptionsDialog(WindowModalDialog):
    """Per-operation policy; never persist a reduced-verification preference."""
    def __init__(self, parent, tx):
        super().__init__(parent, _('Ledger signing options'))
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel(_('{} inputs · {} outputs').format(len(tx.inputs), len(tx.outputs))))
        self.verify_inputs = QCheckBox(_('Verify previous transactions on the device (recommended)'))
        self.verify_inputs.setChecked(True)
        layout.addWidget(self.verify_inputs)
        note = QLabel(_('When enabled, available complete previous transactions are sent to '
            'the device to verify input amounts. Large transactions may take several minutes. '
            'If data is missing or exceeds the device model’s work budget, you will be asked '
            'before continuing with reduced verification.'))
        note.setWordWrap(True)
        layout.addWidget(note)
        warning = QLabel(_('When disabled, complete previous transactions are not sent. '
            'The device may show “unverified inputs” and cannot independently verify the fee. '
            'The current transaction’s inputs and outputs are still sent, and every requested '
            'input still needs its own signature. This may therefore remain slow.'))
        warning.setWordWrap(True)
        layout.addWidget(warning)
        buttons = QHBoxLayout()
        cancel = QPushButton(_('Cancel'))
        cancel.clicked.connect(self.reject)
        proceed = QPushButton(_('Continue to device'))
        proceed.clicked.connect(self.accept)
        buttons.addWidget(cancel)
        buttons.addWidget(proceed)
        layout.addLayout(buttons)
        self.resize(560, 250)


class SigningInputsDialog(WindowModalDialog):
    """Choose signatures, while retaining the complete transaction for device review."""
    def __init__(self, parent, tx, stores):
        super().__init__(parent, _('Choose inputs to sign'))
        from electrumsv.transaction import NO_SIGNATURE
        layout = QVBoxLayout(self)
        note = QLabel(_('Only checked inputs will receive new signatures. Existing signatures '
            'will be kept. Ledger still reviews the whole transaction: each new signature uses '
            'SIGHASH_ALL | FORKID (0x41), committing to all inputs and outputs. '
            'Save the incomplete transaction as JSON to pass it to another signer.'))
        note.setWordWrap(True)
        layout.addWidget(note)
        self.inputs = QTreeWidget()
        self.inputs.setHeaderLabels([_('Input'), _('Amount (satoshis)'), _('Outpoint')])
        for index, txin in enumerate(tx.inputs):
            if txin.is_complete() or not any(
                    signature == NO_SIGNATURE and store.is_signature_candidate(key)
                    for key, signature in zip(txin.x_pubkeys, txin.signatures) for store in stores):
                continue
            item = QTreeWidgetItem([str(index + 1), str(txin.value),
                '{}:{}'.format(txin.prev_hash[::-1].hex(), txin.prev_idx)])
            item.setData(0, Qt.UserRole, index)
            item.setToolTip(2, item.text(2))
            item.setCheckState(0, Qt.Checked)
            self.inputs.addTopLevelItem(item)
        self.inputs.resizeColumnToContents(0)
        self.inputs.resizeColumnToContents(1)
        layout.addWidget(self.inputs)
        buttons = QHBoxLayout()
        cancel = QPushButton(_('Cancel'))
        cancel.clicked.connect(self.reject)
        self.sign_button = QPushButton(_('Continue to device'))
        self.sign_button.clicked.connect(self.accept)
        self.inputs.itemChanged.connect(lambda: self.sign_button.setEnabled(bool(self.selected_indexes())))
        self.sign_button.setEnabled(bool(self.selected_indexes()))
        buttons.addWidget(cancel)
        buttons.addWidget(self.sign_button)
        layout.addLayout(buttons)
        self.resize(720, 360)

    def selected_indexes(self):
        return {self.inputs.topLevelItem(i).data(0, Qt.UserRole)
            for i in range(self.inputs.topLevelItemCount())
            if self.inputs.topLevelItem(i).checkState(0) == Qt.Checked}
