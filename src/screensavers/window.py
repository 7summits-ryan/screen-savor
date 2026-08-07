import gi
gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')
from gi.repository import Gtk, Adw, Gio, GLib
import os

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
            # Suffixes stack left to right, so the gear lands beside the Run
            # button of the one saver that has anything to adjust.
            if (getattr(saver_cls, "TUNABLES", None)
                    or getattr(saver_cls, "FILE_TUNABLES", None)):
                gear = Gtk.Button(icon_name="emblem-system-symbolic")
                gear.set_valign(Gtk.Align.CENTER)
                gear.add_css_class("flat")
                gear.set_tooltip_text(f"{name} settings")
                gear.connect("clicked", self.on_tune_clicked, name, saver_cls)
                row.add_suffix(gear)
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

        # Show Tray Icon. Like the daemon row above, the subtitle has to say
        # what actually happened - a tray icon depends on the desktop providing
        # somewhere to put it, and on GNOME that means an extension. Without
        # this the switch would look like it simply does nothing.
        self.tray_row = Adw.ActionRow(title="Show Tray Icon")
        tray_switch = Gtk.Switch(valign=Gtk.Align.CENTER)
        self.settings.bind("show-tray-icon", tray_switch, "active",
                           Gio.SettingsBindFlags.DEFAULT)
        self.tray_row.add_suffix(tray_switch)
        settings_group.add(self.tray_row)
        self.settings.connect("changed::show-tray-icon",
                              lambda *_: self.refresh_tray_state())
        self.refresh_tray_state()

        # getattr: the screenshot tool in build-aux drives this window from a
        # bare Adw.Application that has no daemon.
        daemon = getattr(self.get_application(), "daemon", None)
        if daemon is not None:
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

    def refresh_tray_state(self):
        """Match the close behaviour and the row's subtitle to the desktop.

        Called by the application whenever the tray comes or goes, because a
        watcher can appear and disappear under us - disabling the GNOME
        extension takes the tray away from a running app.

        Hiding on close is allowed only while an icon is really on screen. With
        no tray, a hidden window would be unreachable and the app unquittable,
        so closing has to keep meaning quit.
        """
        # getattr, as above: the screenshot tool has no application of ours.
        available = getattr(self.get_application(), "tray_available", False)
        self.set_hide_on_close(available)

        if not self.settings.get_boolean("show-tray-icon"):
            self.tray_row.set_subtitle("Closing the window quits Screen Savor")
        elif available:
            self.tray_row.set_subtitle(
                "Closing the window minimizes to the tray, and the screensaver "
                "hides it when it starts")
        else:
            self.tray_row.set_subtitle("No system tray found on this desktop")

    def on_default_saver_changed(self, row, param):
        selected_idx = row.get_selected()
        if selected_idx >= 0 and selected_idx < len(self.savers):
            self.settings.set_string("default-saver", self.savers[selected_idx][0])

    def on_tune_clicked(self, button, name, saver_cls):
        """Put up the tuning dialog a saver describes through TUNABLES.

        The rows write into the saver's live tuning dictionary as they change,
        so a saver already up on screen picks the new value up on its next
        throw - which is the point of adjusting these while watching one.
        """
        dialog = Adw.PreferencesDialog(title=f"{name} Settings")
        page = Adw.PreferencesPage()

        rows = []
        file_rows = []
        groups = {}

        # Files first, so their section is the one that ends up at the top of
        # the page - what a saver is playing matters more than how it is drawn.
        for spec in getattr(saver_cls, "FILE_TUNABLES", ()):
            group = self._tune_group(page, groups, spec[0])
            row = self._build_file_row(saver_cls, spec)
            group.add(row)
            file_rows.append((spec, row))

        for spec in getattr(saver_cls, "TUNABLES", ()):
            section, key, title, subtitle, lower, upper, step, digits = spec
            group = self._tune_group(page, groups, section)

            # Use SwitchRow for boolean tunables (0.0-1.0 range with step 1.0)
            is_boolean = (lower == 0.0 and upper == 1.0 and step == 1.0)

            if is_boolean:
                row = Adw.SwitchRow(title=title, subtitle=subtitle)
                row.set_active(saver_cls.TUNING[key] > 0.5)
                row.connect("notify::active", self.on_tune_switch_changed, saver_cls, key)
            else:
                row = Adw.SpinRow(title=title, subtitle=subtitle, digits=digits)
                row.set_adjustment(Gtk.Adjustment(value=saver_cls.TUNING[key],
                                                  lower=lower, upper=upper,
                                                  step_increment=step,
                                                  page_increment=step * 10))
                row.connect("notify::value", self.on_tune_changed, saver_cls, key)

            group.add(row)
            rows.append((key, row))

        reset = Gtk.Button(label="Reset to Defaults")
        reset.set_halign(Gtk.Align.CENTER)
        reset.add_css_class("pill")
        reset.connect("clicked", self.on_tune_reset, saver_cls, rows, file_rows)
        actions = Adw.PreferencesGroup()
        actions.add(reset)
        page.add(actions)

        dialog.add(page)
        dialog.present(self)

    def _tune_group(self, page, groups, section):
        group = groups.get(section)
        if group is None:
            # Only the first section carries the note; repeating it under
            # every heading would be noise.
            group = Adw.PreferencesGroup(
                title=section,
                description=None if groups else
                "Takes effect immediately, including on a running screensaver")
            groups[section] = group
            page.add(group)
        return group

    # -- file settings ----------------------------------------------------

    def _build_file_row(self, saver_cls, spec):
        """A row for one of the settings a saver names in FILE_TUNABLES.

        The subtitle carries the current pick rather than an explanation of the
        setting, which is the way libadwaita rows show a chosen value - and the
        only way it fits, with two buttons already taking the width. What the
        setting is for goes in the tooltip, and the title says most of it.
        """
        title, subtitle, empty_label = spec[2], spec[3], spec[4]
        row = Adw.ActionRow(title=title, subtitle=self._file_label(saver_cls, spec))
        row.set_tooltip_text(subtitle)

        choose = Gtk.Button(label="Choose…", valign=Gtk.Align.CENTER)
        choose.connect("clicked", self.on_pick_file, saver_cls, spec, row)
        row.add_suffix(choose)

        clear = Gtk.Button(icon_name="edit-clear-symbolic",
                           valign=Gtk.Align.CENTER)
        clear.add_css_class("flat")
        clear.set_tooltip_text(f"Go back to the {empty_label.lower()}")
        clear.connect("clicked",
                      lambda _b: self._set_file(saver_cls, spec, row, ""))
        row.add_suffix(clear)

        return row

    def _file_label(self, saver_cls, spec):
        """What to show for the current pick.

        The basename rather than the path: inside the sandbox the stored path is
        a document portal handle, and only its last component means anything to
        the person who chose it. A file that has since gone says so, because the
        saver would otherwise fall back without explaining itself.
        """
        path = saver_cls.FILES.get(spec[1]) or ""
        if not path:
            return spec[4]
        name = os.path.basename(path)
        return name if os.path.exists(path) else f"{name} (missing)"

    def on_pick_file(self, button, saver_cls, spec, row):
        title, mimes, suffixes = spec[2], spec[5], spec[6]

        # Both MIME types and suffixes: the portal turns a filter into globs and
        # content types, and not every desktop looks at both.
        file_filter = Gtk.FileFilter(name=title)
        for mime in mimes:
            file_filter.add_mime_type(mime)
        for suffix in suffixes:
            file_filter.add_suffix(suffix)
        filters = Gio.ListStore.new(Gtk.FileFilter)
        filters.append(file_filter)

        dialog = Gtk.FileDialog(title=f"Choose a {title}", modal=True)
        dialog.set_filters(filters)
        dialog.set_default_filter(file_filter)

        current = saver_cls.FILES.get(spec[1]) or ""
        if current and os.path.exists(current):
            dialog.set_initial_file(Gio.File.new_for_path(current))

        # Parented on the window rather than the preferences dialog, which is an
        # Adw.Dialog and so not a Gtk.Window at all.
        #
        # This is a Gtk.FileDialog on purpose: in the sandbox it goes through the
        # file chooser portal, which registers the pick with the document portal
        # and grants this app persistent access to that one file. That is what
        # lets the manifest ask for no filesystem permission at all.
        dialog.open(self, None, self.on_file_chosen, (saver_cls, spec, row))

    def on_file_chosen(self, dialog, result, data):
        saver_cls, spec, row = data
        try:
            chosen = dialog.open_finish(result)
        except GLib.Error:
            return          # dismissed, which arrives as an error rather than None
        if chosen is None:
            return

        path = chosen.get_path()
        if path is None:
            return          # not a local file, so there is nothing to play

        self._set_file(saver_cls, spec, row, path)

    def _set_file(self, saver_cls, spec, row, path):
        # The same two writes a tunable gets: the live dictionary a running
        # saver reads, and the key it is restored from next time.
        saver_cls.FILES[spec[1]] = path
        self.settings.set_string(spec[1], path)
        row.set_subtitle(self._file_label(saver_cls, spec))

    def on_tune_changed(self, row, param, saver_cls, key):
        saver_cls.TUNING[key] = row.get_value()
        self.settings.set_value(saver_cls.TUNING_KEY,
                                GLib.Variant("a{sd}", saver_cls.TUNING))

    def on_tune_switch_changed(self, row, param, saver_cls, key):
        saver_cls.TUNING[key] = 1.0 if row.get_active() else 0.0
        self.settings.set_value(saver_cls.TUNING_KEY,
                                GLib.Variant("a{sd}", saver_cls.TUNING))

    def on_tune_reset(self, button, saver_cls, rows, file_rows):
        # Setting each row emits notify::value or notify::active, which writes
        # the value back through on_tune_changed or on_tune_switch_changed.
        for key, row in rows:
            default_value = saver_cls.DEFAULT_TUNING[key]
            if isinstance(row, Adw.SwitchRow):
                row.set_active(default_value > 0.5)
            else:
                row.set_value(default_value)

        # No signal to ride on here, so these are written back directly.
        for spec, row in file_rows:
            self._set_file(saver_cls, spec, row,
                           saver_cls.DEFAULT_FILES[spec[1]])

    def on_run_clicked(self, button, saver_cls):
        session = SaverSession(self.get_application(), saver_cls,
                               on_finished=self.sessions.discard)
        self.sessions.add(session)
        session.start()
