import gi
gi.require_version('Gtk', '4.0')
from gi.repository import Gtk, Gdk, Gio, GLib

SCHEMA_ID = "software._7summits.ScreenSavor"

class SaverWindow(Gtk.Window):
    """A fullscreen, undecorated window running one saver.

    The window does not decide when it goes away - it reports activity to its
    session, which ends the run everywhere at once. On its own it would leave
    the other monitors still covered after a keypress.

    Fullscreening is left to the session too, because which monitor a window
    belongs on is the session's business, not the window's.
    """

    MOTION_THRESHOLD = 10  # px; ignores the jitter of a settling pointer

    def __init__(self, saver_cls, app, on_dismissed, **kwargs):
        super().__init__(application=app, **kwargs)
        self.set_decorated(False)

        # Hide mouse cursor
        self.set_cursor(Gdk.Cursor.new_from_name("none"))

        self._on_dismissed = on_dismissed
        self._dismissed = False

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
        if not self._dismissed:
            self._dismissed = True
            self._on_dismissed(self)
        return True

    def on_motion(self, ctrl, x, y):
        if self.last_x is None or self.last_y is None:
            self.last_x = x
            self.last_y = y
            return

        # Only close if moved significantly
        if (abs(self.last_x - x) > self.MOTION_THRESHOLD
                or abs(self.last_y - y) > self.MOTION_THRESHOLD):
            self.on_activity()


class SaverSession:
    """One screensaver run, across however many monitors are attached.

    Every monitor gets its own window holding its own saver instance, so each
    screen animates independently and is paced by the frame clock of the
    display it is actually on. Tying them together is what this class is for:
    activity on any one screen ends the run on all of them, and monitors that
    appear or vanish mid-run are covered and dropped as they come and go.

    With the multi-monitor setting off, the session puts up a single window and
    leaves the placement to the compositor. That is deliberate rather than
    lazy - GTK4 has no primary monitor to ask for, and a Wayland client cannot
    find out where the pointer is, so there is no better guess available.
    """

    def __init__(self, app, saver_cls, on_finished=None):
        self.app = app
        self.saver_cls = saver_cls
        self.on_finished = on_finished
        self._windows = {}          # Gdk.Monitor -> SaverWindow; None key if single
        self._monitors = None       # the display's monitor list, while watched
        self._monitors_handler = 0
        self._started = False
        self._stopping = False

    # -- lifecycle --------------------------------------------------------

    def start(self):
        if self._started:
            return
        self._started = True

        display = Gdk.Display.get_default()
        if display is None:
            self.stop()
            return

        settings = Gio.Settings.new(SCHEMA_ID)
        if not settings.get_boolean("multi-monitor"):
            self._add_window(None)
            return

        monitors = display.get_monitors()
        for i in range(monitors.get_n_items()):
            self._add_window(monitors.get_item(i))

        if not self._windows:  # a display with no monitors; nothing to cover
            self.stop()
            return

        self._monitors = monitors
        self._monitors_handler = monitors.connect("items-changed",
                                                  self._on_monitors_changed)

    def stop(self):
        if self._stopping:
            return
        self._stopping = True

        if self._monitors_handler:
            self._monitors.disconnect(self._monitors_handler)
            self._monitors = None
            self._monitors_handler = 0

        for win in list(self._windows.values()):
            win.destroy()
        self._windows.clear()

        finished, self.on_finished = self.on_finished, None
        if finished is not None:
            finished(self)

    # -- windows ----------------------------------------------------------

    def _add_window(self, monitor):
        win = SaverWindow(self.saver_cls, self.app, self._on_dismissed)
        self._windows[monitor] = win
        win.connect("close-request", self._on_close_request)
        if monitor is None:
            win.fullscreen()
        else:
            win.fullscreen_on_monitor(monitor)
        win.present()

    def _on_dismissed(self, win):
        if self._stopping:
            return
        # Tearing down the windows from inside one of their own event handlers
        # is asking for trouble; wait for the dispatch to unwind first.
        GLib.idle_add(self._stop_idle)

    def _stop_idle(self):
        self.stop()
        return GLib.SOURCE_REMOVE

    def _on_close_request(self, win):
        # Something outside the app closed a window - end the whole run rather
        # than leave the remaining screens covered. destroy() does not emit
        # this, so stop() tearing the others down will not come back here.
        self.stop()
        return False

    def _on_monitors_changed(self, model, position, removed, added):
        if self._stopping:
            return

        current = {model.get_item(i) for i in range(model.get_n_items())}

        for monitor in list(self._windows):
            if monitor not in current:
                self._windows.pop(monitor).destroy()

        for monitor in current:
            if monitor not in self._windows:
                self._add_window(monitor)

        if not self._windows:  # last monitor went away
            self.stop()
