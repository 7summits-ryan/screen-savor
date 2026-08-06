"""ext-idle-notify-v1, bound by hand through ctypes.

The compositor tells us when the seat has been idle for a given time, which is
the one thing no D-Bus interface reliably offers: GNOME's ScreenSaver interface
returns NotSupported, and KDE's does too. KWin has implemented this protocol
since Plasma 5.26, so it is the working answer there.

There is no PyGObject binding for Wayland protocol extensions - introspection
covers GDK, not the wire protocol - so the two interface descriptors are built
by hand here. libwayland-client exports the core ones (wl_registry, wl_seat)
as real symbols, so only the two ext-idle-notify interfaces have to be
constructed, and the marshalling itself is still libwayland's.

The connection is our own rather than GDK's. Sharing GDK's display would mean
sharing its event queue, and a stray dispatch from here could swallow events
GTK was waiting on; a second connection to the same compositor costs one fd and
keeps the two entirely separate.
"""

import ctypes

import gi
from gi.repository import GLib

# GLib.unix_fd_add_full is deprecated in favour of GLibUnix.fd_add_full, but
# only the newer PyGObject exposes the latter. Prefer it, fall back quietly.
try:
    gi.require_version('GLibUnix', '2.0')
    from gi.repository import GLibUnix
    _fd_add = GLibUnix.fd_add_full
except (ValueError, ImportError):  # pragma: no cover - depends on the runtime
    GLibUnix = None
    _fd_add = GLib.unix_fd_add_full

_LIB = "libwayland-client.so.0"


class _WlMessage(ctypes.Structure):
    _fields_ = [("name", ctypes.c_char_p),
                ("signature", ctypes.c_char_p),
                ("types", ctypes.POINTER(ctypes.c_void_p))]


class _WlInterface(ctypes.Structure):
    _fields_ = [("name", ctypes.c_char_p),
                ("version", ctypes.c_int),
                ("method_count", ctypes.c_int),
                ("methods", ctypes.POINTER(_WlMessage)),
                ("event_count", ctypes.c_int),
                ("events", ctypes.POINTER(_WlMessage))]


# Callback shapes. wl_registry.global, and the notification's two events.
_GLOBAL = ctypes.CFUNCTYPE(None, ctypes.c_void_p, ctypes.c_void_p,
                           ctypes.c_uint32, ctypes.c_char_p, ctypes.c_uint32)
_GLOBAL_REMOVE = ctypes.CFUNCTYPE(None, ctypes.c_void_p, ctypes.c_void_p,
                                  ctypes.c_uint32)
_NOTIFY = ctypes.CFUNCTYPE(None, ctypes.c_void_p, ctypes.c_void_p)


class _RegistryListener(ctypes.Structure):
    _fields_ = [("global_", _GLOBAL), ("global_remove", _GLOBAL_REMOVE)]


class _NotificationListener(ctypes.Structure):
    _fields_ = [("idled", _NOTIFY), ("resumed", _NOTIFY)]


def _load():
    """Open libwayland and declare the handful of entry points we call."""
    wl = ctypes.CDLL(_LIB)
    wl.wl_display_connect.restype = ctypes.c_void_p
    wl.wl_display_connect.argtypes = [ctypes.c_char_p]
    wl.wl_display_disconnect.argtypes = [ctypes.c_void_p]
    wl.wl_display_get_fd.restype = ctypes.c_int
    wl.wl_display_get_fd.argtypes = [ctypes.c_void_p]
    wl.wl_display_roundtrip.restype = ctypes.c_int
    wl.wl_display_roundtrip.argtypes = [ctypes.c_void_p]
    wl.wl_display_dispatch.restype = ctypes.c_int
    wl.wl_display_dispatch.argtypes = [ctypes.c_void_p]
    wl.wl_display_flush.restype = ctypes.c_int
    wl.wl_display_flush.argtypes = [ctypes.c_void_p]
    wl.wl_proxy_add_listener.restype = ctypes.c_int
    wl.wl_proxy_add_listener.argtypes = [ctypes.c_void_p, ctypes.c_void_p,
                                         ctypes.c_void_p]
    wl.wl_proxy_destroy.argtypes = [ctypes.c_void_p]
    # Both marshallers are variadic; argtypes covers the fixed head only and
    # the protocol arguments follow positionally.
    wl.wl_proxy_marshal_constructor.restype = ctypes.c_void_p
    wl.wl_proxy_marshal_constructor.argtypes = [ctypes.c_void_p, ctypes.c_uint32,
                                                ctypes.c_void_p]
    wl.wl_proxy_marshal_constructor_versioned.restype = ctypes.c_void_p
    wl.wl_proxy_marshal_constructor_versioned.argtypes = [
        ctypes.c_void_p, ctypes.c_uint32, ctypes.c_void_p, ctypes.c_uint32]
    return wl


def _build_interfaces(wl):
    """Describe the two ext-idle-notify interfaces to libwayland.

    Everything returned has to outlive every proxy built from it - libwayland
    keeps the pointers, it does not copy - so the caller stashes the whole lot.
    """
    seat = _WlInterface.in_dll(wl, "wl_seat_interface")

    # A types array for argument-less messages. libwayland indexes this even
    # when the signature is empty, so it must be a real array, not NULL.
    none_types = (ctypes.c_void_p * 1)()

    notification = _WlInterface()  # filled in below; referenced just underneath

    # get_idle_notification(new_id, uint timeout, object seat) -> "nuo"
    get_types = (ctypes.c_void_p * 3)(ctypes.addressof(notification), None,
                                      ctypes.addressof(seat))
    notifier_methods = (_WlMessage * 2)(
        _WlMessage(b"destroy", b"", none_types),
        _WlMessage(b"get_idle_notification", b"nuo",
                   ctypes.cast(get_types, ctypes.POINTER(ctypes.c_void_p))),
    )
    notifier = _WlInterface(b"ext_idle_notifier_v1", 1, 2, notifier_methods,
                            0, None)

    notification_methods = (_WlMessage * 1)(
        _WlMessage(b"destroy", b"", none_types))
    notification_events = (_WlMessage * 2)(
        _WlMessage(b"idled", b"", none_types),
        _WlMessage(b"resumed", b"", none_types))
    notification.name = b"ext_idle_notification_v1"
    notification.version = 1
    notification.method_count = 1
    notification.methods = notification_methods
    notification.event_count = 2
    notification.events = notification_events

    return {
        "seat": seat, "notifier": notifier, "notification": notification,
        # Kept only so Python does not collect them out from under libwayland.
        "_keep": (none_types, get_types, notifier_methods,
                  notification_methods, notification_events),
    }


class IdleNotifier:
    """A Wayland connection with ext-idle-notify-v1 bound and ready to watch."""

    WL_DISPLAY_GET_REGISTRY = 1
    WL_REGISTRY_BIND = 0
    NOTIFIER_GET_IDLE_NOTIFICATION = 1
    DESTROY = 0

    def __init__(self, wl, display, ifaces, notifier, seat):
        self._wl = wl
        self._display = display
        self._ifaces = ifaces
        self._notifier = notifier
        self._seat = seat
        self._notification = None
        self._listener = None      # must outlive the proxy
        self._callbacks = None     # ditto: CFUNCTYPE objects
        self._on_idled = None
        self._source_id = 0

    # -- setup ------------------------------------------------------------

    @classmethod
    def open(cls):
        """Connect and bind, or return None if this compositor cannot do it."""
        try:
            wl = _load()
        except (OSError, AttributeError) as e:
            print(f"ext-idle-notify: libwayland unusable ({e})")
            return None

        display = wl.wl_display_connect(None)
        if not display:
            print("ext-idle-notify: not a Wayland session")
            return None

        ifaces = _build_interfaces(wl)
        registry = wl.wl_proxy_marshal_constructor(
            display, cls.WL_DISPLAY_GET_REGISTRY,
            ctypes.byref(_WlInterface.in_dll(wl, "wl_registry_interface")), None)

        globals_seen = {}

        def on_global(_data, _reg, name, interface, version):
            globals_seen[interface.decode()] = (name, version)

        listener = _RegistryListener(_GLOBAL(on_global),
                                     _GLOBAL_REMOVE(lambda *a: None))
        wl.wl_proxy_add_listener(registry, ctypes.byref(listener), None)
        wl.wl_display_roundtrip(display)

        notifier_global = globals_seen.get("ext_idle_notifier_v1")
        seat_global = globals_seen.get("wl_seat")
        if notifier_global is None or seat_global is None:
            missing = ("ext_idle_notifier_v1" if notifier_global is None
                       else "wl_seat")
            print(f"ext-idle-notify: compositor does not offer {missing}")
            wl.wl_display_disconnect(display)
            return None

        def bind(global_name, iface, version):
            return wl.wl_proxy_marshal_constructor_versioned(
                registry, cls.WL_REGISTRY_BIND, ctypes.byref(iface), version,
                ctypes.c_uint32(global_name), iface.name,
                ctypes.c_uint32(version), None)

        # Bind at version 1 of each: get_idle_notification is all we use, and
        # asking for no more than we need keeps this working on older
        # compositors that only advertise v1.
        notifier = bind(notifier_global[0], ifaces["notifier"], 1)
        seat = bind(seat_global[0], ifaces["seat"], 1)
        if not notifier or not seat:
            print("ext-idle-notify: bind failed")
            wl.wl_display_disconnect(display)
            return None

        wl.wl_display_roundtrip(display)
        return cls(wl, display, ifaces, notifier, seat)

    # -- watching ---------------------------------------------------------

    def watch(self, timeout_ms, on_idled):
        """Ask to be told once the seat has been idle for timeout_ms."""
        self.cancel()
        self._on_idled = on_idled

        def idled(_data, _notif):
            if self._on_idled is not None:
                self._on_idled()

        # The protocol re-arms itself: resumed fires on activity and the
        # compositor starts the timer again, so there is no bookkeeping to do.
        callbacks = _NotificationListener(_NOTIFY(idled),
                                          _NOTIFY(lambda *a: None))

        notification = self._wl.wl_proxy_marshal_constructor(
            self._notifier, self.NOTIFIER_GET_IDLE_NOTIFICATION,
            ctypes.byref(self._ifaces["notification"]), None,
            ctypes.c_uint32(timeout_ms), ctypes.c_void_p(self._seat))
        if not notification:
            print("ext-idle-notify: could not create the notification")
            return False

        self._wl.wl_proxy_add_listener(notification, ctypes.byref(callbacks),
                                       None)
        self._notification = notification
        self._listener = callbacks
        self._callbacks = callbacks
        self._wl.wl_display_flush(self._display)

        if not self._source_id:
            self._source_id = _fd_add(
                GLib.PRIORITY_DEFAULT, self._wl.wl_display_get_fd(self._display),
                GLib.IOCondition.IN, self._on_readable)
        return True

    def _on_readable(self, _fd, _condition):
        # Only ever called with data waiting, so this will not block.
        if self._wl.wl_display_dispatch(self._display) < 0:
            print("ext-idle-notify: connection lost")
            self._source_id = 0
            return GLib.SOURCE_REMOVE
        return GLib.SOURCE_CONTINUE

    def cancel(self):
        self._on_idled = None
        if self._notification:
            self._wl.wl_proxy_marshal_constructor(
                self._notification, self.DESTROY, None, None)
            self._wl.wl_proxy_destroy(self._notification)
            self._wl.wl_display_flush(self._display)
            self._notification = None
        self._listener = None
        self._callbacks = None

    def close(self):
        self.cancel()
        if self._source_id:
            GLib.source_remove(self._source_id)
            self._source_id = 0
        if self._display:
            self._wl.wl_display_disconnect(self._display)
            self._display = None
