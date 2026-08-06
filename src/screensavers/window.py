import gi
gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')
from gi.repository import Gtk, Gdk, GLib, Adw, Gio

from screensavers.savers import DVDLogoSaver, MatrixSaver, ColorPulseSaver

class ScreensaversWindow(Adw.ApplicationWindow):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.set_title("Screen Savor")
        self.set_default_size(400, 300)

        self.box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.set_content(self.box)

        header = Adw.HeaderBar()
        self.box.append(header)
        
        self.settings = Gio.Settings.new("software.sevensummits.screensavor")

        # Screensavers List
        list_box = Gtk.ListBox()
        list_box.set_selection_mode(Gtk.SelectionMode.NONE)
        list_box.add_css_class("boxed-list")
        
        pref_group = Adw.PreferencesGroup(title="Run Screensaver")
        pref_group.add(list_box)
        pref_group.set_margin_top(24)
        pref_group.set_margin_bottom(24)
        pref_group.set_margin_start(24)
        pref_group.set_margin_end(24)
        
        self.savers = [
            ("DVD Logo", DVDLogoSaver),
            ("Matrix Rain", MatrixSaver),
            ("Color Pulse", ColorPulseSaver)
        ]

        for name, saver_cls in self.savers:
            row = Adw.ActionRow(title=name)
            btn = Gtk.Button(label="Run")
            btn.set_valign(Gtk.Align.CENTER)
            btn.connect("clicked", self.on_run_clicked, saver_cls)
            row.add_suffix(btn)
            list_box.append(row)
        
        self.box.append(pref_group)
        
        # Settings
        settings_group = Adw.PreferencesGroup(title="Idle Daemon Settings")
        settings_group.set_margin_bottom(24)
        settings_group.set_margin_start(24)
        settings_group.set_margin_end(24)

        # Enable Daemon
        enable_row = Adw.ActionRow(title="Enable Idle Screensaver")
        enable_switch = Gtk.Switch(valign=Gtk.Align.CENTER)
        self.settings.bind("daemon-enabled", enable_switch, "active", Gio.SettingsBindFlags.DEFAULT)
        enable_row.add_suffix(enable_switch)
        settings_group.add(enable_row)

        # Idle Timeout
        timeout_row = Adw.SpinRow(title="Idle Timeout (Minutes)")
        timeout_row.set_adjustment(Gtk.Adjustment(value=5, lower=1, upper=120, step_increment=1))
        self.settings.bind("idle-timeout", timeout_row, "value", Gio.SettingsBindFlags.DEFAULT)
        settings_group.add(timeout_row)
        
        # Default Saver
        saver_row = Adw.ComboRow(title="Default Screensaver")
        model = Gtk.StringList()
        for name, _ in self.savers:
            model.append(name)
        saver_row.set_model(model)
        
        # Bind combo row to settings (string to index and back)
        current_saver = self.settings.get_string("default-saver")
        for i, (name, _) in enumerate(self.savers):
            if name == current_saver:
                saver_row.set_selected(i)
                break
                
        saver_row.connect("notify::selected", self.on_default_saver_changed)
        settings_group.add(saver_row)

        self.box.append(settings_group)

    def on_default_saver_changed(self, row, param):
        selected_idx = row.get_selected()
        if selected_idx >= 0 and selected_idx < len(self.savers):
            self.settings.set_string("default-saver", self.savers[selected_idx][0])

    def on_run_clicked(self, button, saver_cls):
        saver_win = SaverWindow(saver_cls, self.get_application())
        saver_win.present()

class SaverWindow(Gtk.Window):
    def __init__(self, saver_cls, app, **kwargs):
        super().__init__(application=app, **kwargs)
        self.set_decorated(False)
        self.fullscreen()
        
        # Hide mouse cursor
        self.set_cursor(Gdk.Cursor.new_from_name("none"))
        self._closing = False

        self.saver = saver_cls()
        self.set_child(self.saver)

        # Event controllers for exit
        key_ctrl = Gtk.EventControllerKey()
        key_ctrl.connect("key-pressed", self.on_activity)
        self.add_controller(key_ctrl)

        motion_ctrl = Gtk.EventControllerMotion()
        motion_ctrl.connect("motion", self.on_motion)
        self.add_controller(motion_ctrl)
        
        click_ctrl = Gtk.GestureClick()
        click_ctrl.connect("pressed", self.on_activity)
        self.add_controller(click_ctrl)

        self.last_x = None
        self.last_y = None

    def on_activity(self, *args):
        if self._closing:
            return True
        self._closing = True
        GLib.idle_add(self.close)
        return True

    def on_motion(self, ctrl, x, y):
        if self.last_x is None or self.last_y is None:
            self.last_x = x
            self.last_y = y
            return
        
        # Only close if moved significantly
        if abs(self.last_x - x) > 10 or abs(self.last_y - y) > 10:
            if not self._closing:
                self._closing = True
                GLib.idle_add(self.close)
