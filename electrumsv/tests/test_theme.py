"""Theme switching must be complete and keep System as a preference, not a snapshot."""
import pytest

pytest.importorskip('PyQt5')
from PyQt5.QtGui import QPalette
from PyQt5.QtWidgets import QApplication, QDialog, QLineEdit, QListWidget, QVBoxLayout

from electrumsv.gui.qt import theme as themes


@pytest.fixture
def gui():
    app = QApplication.instance() or QApplication([])
    palette, stylesheet = app.palette(), app.styleSheet()
    yield app
    app.setStyleSheet('')
    app.setPalette(palette)
    app.setStyleSheet(stylesheet)


@pytest.mark.parametrize('start', [themes.Theme.DARK, themes.Theme.PINK, themes.Theme.ORANGE])
def test_light_replaces_previous_palette_for_existing_and_new_controls(gui, start):
    controller = themes.ThemeController(gui, start)
    dialog = QDialog()
    layout = QVBoxLayout(dialog)
    edit = QLineEdit()
    layout.addWidget(edit)
    dialog.show()
    gui.processEvents()
    controller.select(themes.Theme.LIGHT)
    gui.processEvents()
    for widget in (dialog, edit, QLineEdit()):
        widget.ensurePolished()
        assert widget.palette().color(QPalette.Window).lightness() > 200
        assert widget.palette().color(QPalette.Text).lightness() < 80
    assert gui.palette().color(QPalette.Link).name() == '#005bbb'
    dialog.close()


def test_system_changes_only_followed_in_system_mode(gui, monkeypatch):
    current = [themes.Theme.DARK]
    monkeypatch.setattr(themes, 'system_theme', lambda: current[0])
    controller = themes.ThemeController(gui, themes.Theme.SYSTEM)
    changes = []
    controller.changed.connect(changes.append)
    assert controller.effective == themes.Theme.DARK
    current[0] = themes.Theme.LIGHT
    controller.refresh_system_theme()
    assert controller.effective == themes.Theme.LIGHT
    assert controller.preference == themes.Theme.SYSTEM
    assert changes == [themes.Theme.LIGHT]
    controller.select(themes.Theme.DARK)
    controller.refresh_system_theme()
    assert controller.effective == themes.Theme.DARK
    assert not controller._timer.isActive()
    controller.deleteLater()


def test_system_selection_clears_native_override_before_resolving(gui, monkeypatch):
    from electrumsv.gui.qt import macos_appearance
    calls = []
    monkeypatch.setattr(themes, '_uses_appkit', lambda: True)
    monkeypatch.setattr(macos_appearance, 'set_appearance', calls.append)
    def system_theme():
        assert calls[-1] is None
        return themes.Theme.DARK
    monkeypatch.setattr(themes, 'system_theme', system_theme)
    controller = themes.ThemeController(gui, themes.Theme.LIGHT)
    assert calls == [False]
    controller.select(themes.Theme.SYSTEM)
    assert calls == [False, None]
    controller._timer.stop()
    controller.deleteLater()


def test_local_account_styles_follow_theme(gui):
    widget = QListWidget()
    widget.setProperty('theme_role', 'account_types')
    themes.apply_theme(gui, themes.Theme.DARK)
    assert widget.styleSheet() == themes.DARK_ACCOUNT_TYPES_STYLE
    themes.apply_theme(gui, themes.Theme.LIGHT)
    assert widget.styleSheet() == themes.LIGHT_ACCOUNT_TYPES_STYLE


def test_theme_setting_preserves_legacy_and_explicit_choices(monkeypatch):
    assert themes.theme_from_config({'dark_mode': False}) == themes.Theme.LIGHT
    assert themes.theme_from_config({'dark_mode': True}) == themes.Theme.DARK
    assert themes.theme_from_config({'theme': 'light', 'dark_mode': True}) == themes.Theme.LIGHT
    monkeypatch.setattr(themes, 'system_theme', lambda: themes.Theme.DARK)
    assert themes.theme_from_config({'theme': 'system'}) == themes.Theme.DARK
    assert themes.theme_preference_from_config({'theme': 'system'}) == themes.Theme.SYSTEM


def test_switching_to_light_clears_legacy_dark_text_colors(gui):
    from electrumsv.gui.qt.util import ColorScheme
    from PyQt5.QtWidgets import QWidget
    themes.apply_theme(gui, themes.Theme.DARK)
    ColorScheme.update_from_widget(QWidget())
    assert ColorScheme.dark_scheme
    themes.apply_theme(gui, themes.Theme.LIGHT)
    ColorScheme.update_from_widget(QWidget())
    assert not ColorScheme.dark_scheme


def contrast(first, second):
    def luminance(color):
        channels = [value / 255 for value in color.getRgb()[:3]]
        channels = [v / 12.92 if v <= 0.04045 else ((v + 0.055) / 1.055) ** 2.4
            for v in channels]
        return sum(v * weight for v, weight in zip(channels, (0.2126, 0.7152, 0.0722)))
    low, high = sorted((luminance(first), luminance(second)))
    return (high + 0.05) / (low + 0.05)


@pytest.mark.parametrize('theme', [themes.Theme.LIGHT, themes.Theme.DARK])
@pytest.mark.parametrize('foreground,background', [
    (QPalette.WindowText, QPalette.Window), (QPalette.Text, QPalette.Base),
    (QPalette.Text, QPalette.AlternateBase), (QPalette.ButtonText, QPalette.Button),
    (QPalette.ToolTipText, QPalette.ToolTipBase), (QPalette.HighlightedText, QPalette.Highlight),
    (QPalette.Link, QPalette.Base), (QPalette.LinkVisited, QPalette.Base),
    (QPalette.PlaceholderText, QPalette.Base), (QPalette.BrightText, QPalette.Base),
])
def test_light_and_dark_text_palette_has_readable_contrast(theme, foreground, background):
    palette = themes.theme_palette(theme)
    assert contrast(palette.color(foreground), palette.color(background)) >= 4.5
