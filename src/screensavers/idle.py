"""Idle detection, one backend per desktop.

There is no single way to ask how long the session has been idle, so this picks
a backend at start-up by trying each one and keeping the first that answers.
Probing beats reading XDG_CURRENT_DESKTOP: GNOME Shell owns
org.freedesktop.ScreenSaver too and would be misread as KDE on a name check
alone, and a capability probe also picks up the desktops nobody thought to
special-case.

Each backend is asked to prove itself before it is accepted - owning a bus name
or a protocol is not the same as implementing what sits behind it. That is not
a hypothetical: GNOME and KDE both own org.freedesktop.ScreenSaver, and both
answer GetSessionIdleTime with NotSupported.

The three, in the order they are tried:

  ext-idle-notify-v1  a Wayland protocol, so no bus permission is needed at
                      all. Event-driven. KWin and wlroots implement it; Mutter
                      does not.
  Mutter IdleMonitor  GNOME's own D-Bus interface, and the only thing that
                      reports idle time there. Also event-driven.
  freedesktop         polled, and a genuine long shot - see above - but it
                      costs nothing to ask on desktops we have not met.
"""

from gi.repository import Gio, GLib

from screensavers import wlidle


class _Backend:
    """Common D-Bus plumbing. Subclasses supply the name, path and interface."""

    BUS_NAME = None
    OBJECT_PATH = None
    INTERFACE = None
    LABEL = None

    def __init__(self, proxy):
        self.proxy = proxy
        self._on_idle = None

    @classmethod
    def probe(cls, callback):
        """Build the proxy, verify it works, then hand it back or hand back None."""
        def on_proxy(source, result, _data=None):
            try:
                proxy = Gio.DBusProxy.new_for_bus_finish(result)
            except GLib.Error as e:
                print(f"{cls.LABEL}: unavailable ({e.message})")
                callback(None)
                return
            if proxy.get_name_owner() is None:
                print(f"{cls.LABEL}: unavailable (nobody owns {cls.BUS_NAME})")
                callback(None)
                return
            try:
                cls._verify(proxy)
            except GLib.Error as e:
                # The name is taken but the method is not really implemented -
                # exactly what GNOME does with org.freedesktop.ScreenSaver.
                print(f"{cls.LABEL}: unusable ({e.message})")
                callback(None)
                return
            callback(cls(proxy))

        Gio.DBusProxy.new_for_bus(
            Gio.BusType.SESSION, Gio.DBusProxyFlags.NONE, None,
            cls.BUS_NAME, cls.OBJECT_PATH, cls.INTERFACE, None, on_proxy)

    @staticmethod
    def _verify(proxy):
        """Read the idle time. Raises GLib.Error if the backend is no good."""
        raise NotImplementedError

    def arm(self, timeout_ms, on_idle):
        raise NotImplementedError

    def disarm(self):
        raise NotImplementedError


class MutterBackend(_Backend):
    """GNOME. The compositor watches for us and signals when the time is up."""

    BUS_NAME = "org.gnome.Mutter.IdleMonitor"
    OBJECT_PATH = "/org/gnome/Mutter/IdleMonitor/Core"
    INTERFACE = "org.gnome.Mutter.IdleMonitor"
    LABEL = "GNOME (Mutter idle monitor)"

    def __init__(self, proxy):
        super().__init__(proxy)
        self.watch_id = 0
        self._signal_handler = proxy.connect("g-signal", self._on_signal)

    @staticmethod
    def _verify(proxy):
        proxy.call_sync("GetIdletime", None, Gio.DBusCallFlags.NONE, 2000, None)

    def arm(self, timeout_ms, on_idle):
        self.disarm()
        self._on_idle = on_idle
        try:
            res = self.proxy.call_sync("AddIdleWatch",
                                       GLib.Variant("(t)", (timeout_ms,)),
                                       Gio.DBusCallFlags.NONE, -1, None)
            self.watch_id = res.unpack()[0]
        except GLib.Error as e:
            print(f"Error setting idle watch: {e.message}")

    def disarm(self):
        if self.watch_id:
            try:
                self.proxy.call_sync("RemoveWatch",
                                     GLib.Variant("(u)", (self.watch_id,)),
                                     Gio.DBusCallFlags.NONE, -1, None)
            except GLib.Error:
                pass
            self.watch_id = 0

    def _on_signal(self, proxy, sender, signal, parameters):
        if signal == "WatchFired" and self.watch_id:
            if parameters.unpack()[0] == self.watch_id and self._on_idle:
                self._on_idle()


class FreedesktopBackend(_Backend):
    """Last resort. Nothing signals us, so the idle time is polled.

    This was written for KDE and turns out not to work there: Plasma owns the
    name but answers NotSupported, the same as GNOME. It is kept because it is
    cheap and might yet be the only thing some desktop offers, but anything
    current is expected to match one of the two backends above.

    Mutter's watch re-arms itself once the user is active again; polling has to
    do that bookkeeping by hand, which is what _fired is for - without it every
    poll past the timeout would fire again.
    """

    BUS_NAME = "org.freedesktop.ScreenSaver"
    OBJECT_PATH = "/org/freedesktop/ScreenSaver"
    INTERFACE = "org.freedesktop.ScreenSaver"
    LABEL = "org.freedesktop.ScreenSaver (KDE and others)"

    POLL_MS = 10000  # a screensaver timeout is minutes; 10s is plenty fine

    def __init__(self, proxy):
        super().__init__(proxy)
        self.source_id = 0
        self.timeout_ms = 0
        self._fired = False

    @staticmethod
    def _verify(proxy):
        proxy.call_sync("GetSessionIdleTime", None,
                        Gio.DBusCallFlags.NONE, 2000, None)

    def idle_time_ms(self):
        try:
            res = self.proxy.call_sync("GetSessionIdleTime", None,
                                       Gio.DBusCallFlags.NONE, 2000, None)
            return res.unpack()[0]
        except GLib.Error:
            return 0

    def arm(self, timeout_ms, on_idle):
        self.disarm()
        self._on_idle = on_idle
        self.timeout_ms = timeout_ms
        self._fired = False
        self.source_id = GLib.timeout_add(self.POLL_MS, self._poll)

    def disarm(self):
        if self.source_id:
            GLib.source_remove(self.source_id)
            self.source_id = 0

    def _poll(self):
        idle = self.idle_time_ms()
        if idle < self.timeout_ms:
            self._fired = False      # user is back; ready to fire again
        elif not self._fired:
            self._fired = True
            if self._on_idle:
                self._on_idle()
        return GLib.SOURCE_CONTINUE


class WaylandBackend:
    """KDE and wlroots. The compositor signals us through ext-idle-notify-v1.

    Not a _Backend: there is no D-Bus proxy here, only a Wayland connection, so
    it shares the arm/disarm/probe shape without the plumbing.
    """

    LABEL = "ext-idle-notify-v1 (KDE and wlroots)"

    def __init__(self, notifier):
        self.notifier = notifier

    @classmethod
    def probe(cls, callback):
        # Binding either works or it does not, so unlike the D-Bus backends
        # there is nothing to wait for - answer straight away.
        notifier = wlidle.IdleNotifier.open()
        callback(cls(notifier) if notifier is not None else None)

    def arm(self, timeout_ms, on_idle):
        self.notifier.watch(timeout_ms, on_idle)

    def disarm(self):
        self.notifier.cancel()


# Tried in order. The Wayland protocol first: it is the standard, it is
# event-driven, and it needs no bus permission at all. Mutter second, because
# GNOME implements no idle protocol and only its own interface reports an idle
# time. The freedesktop interface is last and rarely works - GNOME and KDE both
# own the name while returning NotSupported - but it costs nothing to ask.
BACKENDS = (WaylandBackend, MutterBackend, FreedesktopBackend)


def detect(callback):
    """Find a working idle backend, then call callback(backend_or_None)."""
    remaining = list(BACKENDS)

    def try_next(_backend=None):
        if _backend is not None:
            print(f"Idle detection: using {_backend.LABEL}")
            callback(_backend)
            return
        if not remaining:
            print("Idle detection: no supported backend on this desktop")
            callback(None)
            return
        remaining.pop(0).probe(try_next)

    try_next()
