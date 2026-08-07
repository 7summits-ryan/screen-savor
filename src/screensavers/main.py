import sys
import gi

gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')
from gi.repository import Gtk, Gio, Adw, GLib

from screensavers import background, idle, tray
from screensavers.savers import load_tuning, saver_for_name
from screensavers.session import SaverSession
from screensavers.window import ScreensaversWindow

SCHEMA_ID = "software._7summits.ScreenSavor"

class IdleDaemon:
    """Starts the screensaver once the session has been idle long enough.

    Which desktop we are on decides how idle time is discovered, so the backend
    is probed for at start-up rather than assumed - see screensavers.idle. Until
    that probe lands there is nothing to arm, and if it comes back empty the
    daemon stays inert and says so through `status`.
    """

    def __init__(self, app):
        self.app = app
        self.settings = Gio.Settings.new(SCHEMA_ID)
        self.backend = None
        self.detected = False
        self.session = None
        self.is_holding = False
        self.on_status_changed = None

        self.settings.connect("changed::daemon-enabled", self.update_daemon)
        self.settings.connect("changed::idle-timeout", self.update_daemon)

        idle.detect(self.on_backend_detected)

    @property
    def available(self):
        return self.backend is not None

    @property
    def status(self):
        """One line on the state of idle detection, for the preferences window."""
        if not self.detected:
            return "Checking for idle detection support…"
        if self.backend is None:
            return "Not supported on this desktop - use the Run buttons above"
        return f"Using {self.backend.LABEL}"

    def on_backend_detected(self, backend):
        self.backend = backend
        self.detected = True
        self.update_daemon()
        if self.on_status_changed is not None:
            self.on_status_changed(self)

    def update_daemon(self, *args):
        if not self.detected:
            return

        if self.backend is not None:
            self.backend.disarm()

        # Holding with no backend would keep the process resident forever
        # without ever being able to fire.
        enabled = self.settings.get_boolean("daemon-enabled") and self.available

        if enabled and not self.is_holding:
            self.app.hold()
            self.is_holding = True
            # From here the process can outlive its window, so make sure the
            # desktop offers some way back to it.
            self.app.ensure_reachable_in_background()
        elif not enabled and self.is_holding:
            self.app.release()
            self.is_holding = False

        if not enabled:
            return

        timeout_minutes = self.settings.get_int("idle-timeout")
        self.backend.arm(timeout_minutes * 60 * 1000, self.trigger_screensaver)

    def trigger_screensaver(self, from_idle=True):
        if self.session is not None:
            return

        # Going idle is the user walking away, so put the window away with it
        # rather than leaving it sitting there for whoever comes back. Only
        # when there is a tray to get it from again - hiding the last way into
        # the app would be worse than leaving it open.
        if from_idle and self.app.tray_available:
            self.app.hide_main_window()

        saver_cls = saver_for_name(self.settings.get_string("default-saver"))

        def on_finished(session):
            if self.session is session:
                self.session = None

        self.session = SaverSession(self.app, saver_cls, on_finished=on_finished)
        self.session.start()

class ScreensaversApplication(Adw.Application):
    """The application, and the owner of everything that outlives a window.

    With a tray icon up, closing the window hides it instead of destroying it,
    so the process keeps running whether or not the idle daemon is holding it.
    That is only safe while something on screen can bring it back, so the tray's
    availability - which comes and goes with the desktop's watcher - decides
    whether the window is allowed to hide at all.
    """

    def __init__(self):
        super().__init__(application_id=SCHEMA_ID,
                         flags=Gio.ApplicationFlags.FLAGS_NONE)
        self.daemon = None
        self.tray = None
        self.settings = Gio.Settings.new(SCHEMA_ID)

    def do_startup(self):
        Adw.Application.do_startup(self)
        # Before anything can run a saver, so an idle start uses the same
        # values the preferences window shows.
        load_tuning(self.settings)
        self.daemon = IdleDaemon(self)
        # The application relays this on to the window and the tray, so that
        # both show the same thing and neither has to know about the other.
        self.daemon.on_status_changed = self.on_daemon_status
        self.settings.connect("changed::show-tray-icon", self.update_tray)
        self.update_tray()

    def do_activate(self):
        # Saver windows belong to the application too, so active_window can be
        # one of them - launching the app again while the saver is up would
        # re-present a fullscreen saver instead of the controls.
        win = self.main_window
        if not win:
            win = ScreensaversWindow(application=self)
        win.present()

    @property
    def main_window(self):
        return next((w for w in self.get_windows()
                     if isinstance(w, ScreensaversWindow)), None)

    def hide_main_window(self):
        win = self.main_window
        if win is not None:
            win.set_visible(False)

    # -- tray -------------------------------------------------------------

    @property
    def tray_available(self):
        """Whether an icon is actually on screen, not merely asked for."""
        return self.tray is not None and self.tray.available

    def update_tray(self, *args):
        wanted = self.settings.get_boolean("show-tray-icon")

        if wanted and self.tray is None:
            self.tray = tray.TrayIcon(self,
                                      on_open=self.activate,
                                      on_run_saver=self.run_saver_now,
                                      on_quit=self.quit)
            self.tray.on_available_changed = self.on_tray_changed
            self.tray.start()
        elif not wanted and self.tray is not None:
            self.tray.stop()
            self.tray = None

        self.on_tray_changed()

    def run_saver_now(self):
        """The tray menu's entry. Not an idle start, so the window stays put."""
        self.daemon.trigger_screensaver(from_idle=False)

    def on_tray_changed(self, _tray=None):
        available = self.tray_available
        win = self.main_window

        if win is not None:
            win.refresh_tray_state()
            # A window hidden into a tray that has since gone away - the
            # extension being disabled, say - leaves the app running with
            # nothing to reach it by. get_realized() keeps this from firing
            # against a window that has simply not been shown yet.
            if not available and win.get_realized() and not win.get_visible():
                win.present()

        self.ensure_reachable_in_background()

    def on_daemon_status(self, daemon):
        """Idle detection reported in. Everything that shows it gets told."""
        if self.tray is not None:
            self.tray.set_tooltip(daemon.status)
        win = self.main_window
        if win is not None:
            win.on_daemon_status_changed(daemon)

    def ensure_reachable_in_background(self):
        """Fall back to the portal on desktops that have no tray to offer.

        Only once the tray has settled: before that, "not available" only means
        the answer has not come back yet, and asking the portal prematurely
        would put a permission dialog in front of KDE users whose desktop was
        always going to have a tray.

        Only while the daemon is holding, too. That is the one state where the
        process outlives its window without a tray icon - with no tray the
        window does not hide on close, so closing it really does quit.
        """
        if self.tray is not None and not self.tray.settled:
            return
        if self.tray_available:
            return
        if self.daemon is not None and self.daemon.is_holding:
            background.request()

def main(args):
    app = ScreensaversApplication()
    return app.run(args)
