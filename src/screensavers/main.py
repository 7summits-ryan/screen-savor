import sys
import gi

gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')
from gi.repository import Gtk, Gio, Adw, GLib

from screensavers import idle
from screensavers.savers import saver_for_name
from screensavers.session import SaverSession
from screensavers.window import ScreensaversWindow

class IdleDaemon:
    """Starts the screensaver once the session has been idle long enough.

    Which desktop we are on decides how idle time is discovered, so the backend
    is probed for at start-up rather than assumed - see screensavers.idle. Until
    that probe lands there is nothing to arm, and if it comes back empty the
    daemon stays inert and says so through `status`.
    """

    def __init__(self, app):
        self.app = app
        self.settings = Gio.Settings.new("software._7summits.ScreenSavor")
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
        elif not enabled and self.is_holding:
            self.app.release()
            self.is_holding = False

        if not enabled:
            return

        timeout_minutes = self.settings.get_int("idle-timeout")
        self.backend.arm(timeout_minutes * 60 * 1000, self.trigger_screensaver)

    def trigger_screensaver(self):
        if self.session is not None:
            return

        saver_cls = saver_for_name(self.settings.get_string("default-saver"))

        def on_finished(session):
            if self.session is session:
                self.session = None

        self.session = SaverSession(self.app, saver_cls, on_finished=on_finished)
        self.session.start()

class ScreensaversApplication(Adw.Application):
    def __init__(self):
        super().__init__(application_id='software._7summits.ScreenSavor',
                         flags=Gio.ApplicationFlags.FLAGS_NONE)
        self.daemon = None

    def do_startup(self):
        Adw.Application.do_startup(self)
        self.daemon = IdleDaemon(self)

    def do_activate(self):
        # Saver windows belong to the application too, so active_window can be
        # one of them - launching the app again while the saver is up would
        # re-present a fullscreen saver instead of the controls.
        win = next((w for w in self.get_windows()
                    if isinstance(w, ScreensaversWindow)), None)
        if not win:
            win = ScreensaversWindow(application=self)
        win.present()

def main(args):
    app = ScreensaversApplication()
    return app.run(args)
