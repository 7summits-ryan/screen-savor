import gi
gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')
from gi.repository import Gtk, Adw, Gio

from screensavers.savers import SAVERS
from screensavers.session import SaverSession

class ScreensaversWindow(Adw.ApplicationWindow):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.set_title("Screen Savor")
        self.set_default_size(400, 300)

        self.box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.set_content(self.box)

        header = Adw.HeaderBar()
        self.box.append(header)

        self.settings = Gio.Settings.new("software._7summits.ScreenSavor")

        # Sessions started from here, kept until they finish
        self.sessions = set()

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

        self.savers = SAVERS

        for name, saver_cls in self.savers:
            row = Adw.ActionRow(title=name)
            btn = Gtk.Button(label="Run")
            btn.set_valign(Gtk.Align.CENTER)
            btn.connect("clicked", self.on_run_clicked, saver_cls)
            row.add_suffix(btn)
            list_box.append(row)

        self.box.append(pref_group)

        # Settings
        settings_group = Adw.PreferencesGroup(title="Settings")
        settings_group.set_margin_bottom(24)
        settings_group.set_margin_start(24)
        settings_group.set_margin_end(24)

        # Multi-monitor. Applies to every run, idle or manual, which is why it
        # sits above the daemon's own settings rather than among them.
        monitors_row = Adw.SwitchRow(title="Cover All Monitors",
                                     subtitle="Run the screensaver on every connected display")
        self.settings.bind("multi-monitor", monitors_row, "active", Gio.SettingsBindFlags.DEFAULT)
        settings_group.add(monitors_row)

        # Enable Daemon. Idle detection is not available on every desktop, so
        # the row reports which backend was found - or that there wasn't one,
        # which would otherwise look like the switch simply doing nothing.
        self.enable_row = Adw.ActionRow(title="Enable Idle Screensaver")
        self.enable_switch = Gtk.Switch(valign=Gtk.Align.CENTER)
        self.settings.bind("daemon-enabled", self.enable_switch, "active", Gio.SettingsBindFlags.DEFAULT)
        self.enable_row.add_suffix(self.enable_switch)
        settings_group.add(self.enable_row)

        # getattr: the screenshot tool in build-aux drives this window from a
        # bare Adw.Application that has no daemon.
        daemon = getattr(self.get_application(), "daemon", None)
        if daemon is not None:
            daemon.on_status_changed = self.on_daemon_status_changed
            self.on_daemon_status_changed(daemon)

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

    def on_daemon_status_changed(self, daemon):
        self.enable_row.set_subtitle(daemon.status)
        self.enable_switch.set_sensitive(daemon.available or not daemon.detected)

    def on_default_saver_changed(self, row, param):
        selected_idx = row.get_selected()
        if selected_idx >= 0 and selected_idx < len(self.savers):
            self.settings.set_string("default-saver", self.savers[selected_idx][0])

    def on_run_clicked(self, button, saver_cls):
        session = SaverSession(self.get_application(), saver_cls,
                               on_finished=self.sessions.discard)
        self.sessions.add(session)
        session.start()
