"""A system tray icon, spoken as StatusNotifierItem over D-Bus.

With the idle daemon on, the application holds itself alive with no window on
screen. That is invisible: nothing says it is running, closing the window looks
exactly like quitting, and there is no way to get the preferences back or to
stop it. This puts an icon in the tray so a backgrounded Screen Savor can be
seen, reopened and quit.

StatusNotifierItem rather than a GTK status icon because GTK4 removed status
icons outright, and because SNI is what both desktops we care about actually
speak: Plasma implements the watcher itself, and on GNOME the AppIndicator
extension provides one. Where neither exists there is no tray at all, and the
application falls back to the background portal - see screensavers.background.

The protocol is served by hand out of Gio rather than through
libayatana-appindicator. That library is GTK3, it is not in the GNOME runtime
we build against, and pulling GTK3 into a GTK4 process to draw one icon is a
bad trade. Two small interfaces are cheaper:

  org.kde.StatusNotifierItem  the icon itself - what it looks like, what
                              happens when it is clicked.
  com.canonical.dbusmenu      the right-click menu. Nothing synthesises this
                              for us; without it there is no way to quit.

The menu is fixed, which keeps the dbusmenu side to answering questions rather
than tracking changes - no revisions to bump and no LayoutUpdated to emit.
"""

import gi
gi.require_version('Gtk', '4.0')
from gi.repository import Gdk, Gio, GLib, Gtk

APP_ID = "software._7summits.ScreenSavor"
ICON_NAME = f"{APP_ID}-symbolic"
TITLE = "Screen Savor"

WATCHER_NAME = "org.kde.StatusNotifierWatcher"
WATCHER_PATH = "/StatusNotifierWatcher"
ITEM_PATH = "/StatusNotifierItem"
MENU_PATH = "/MenuBar"

# Hosts pick whichever is closest to the size they are drawing. A panel wants
# roughly 22px, and twice that covers a 2x scale factor.
PIXMAP_SIZES = (22, 44)

SNI_XML = """
<node>
  <interface name="org.kde.StatusNotifierItem">
    <property name="Category" type="s" access="read"/>
    <property name="Id" type="s" access="read"/>
    <property name="Title" type="s" access="read"/>
    <property name="Status" type="s" access="read"/>
    <property name="WindowId" type="i" access="read"/>
    <property name="IconName" type="s" access="read"/>
    <property name="IconPixmap" type="a(iiay)" access="read"/>
    <property name="OverlayIconName" type="s" access="read"/>
    <property name="AttentionIconName" type="s" access="read"/>
    <property name="AttentionMovieName" type="s" access="read"/>
    <property name="ToolTip" type="(sa(iiay)ss)" access="read"/>
    <property name="ItemIsMenu" type="b" access="read"/>
    <property name="Menu" type="o" access="read"/>
    <method name="Activate">
      <arg name="x" type="i" direction="in"/>
      <arg name="y" type="i" direction="in"/>
    </method>
    <method name="SecondaryActivate">
      <arg name="x" type="i" direction="in"/>
      <arg name="y" type="i" direction="in"/>
    </method>
    <method name="Scroll">
      <arg name="delta" type="i" direction="in"/>
      <arg name="orientation" type="s" direction="in"/>
    </method>
    <signal name="NewIcon"/>
    <signal name="NewStatus"><arg name="status" type="s"/></signal>
    <signal name="NewToolTip"/>
  </interface>
</node>
"""

MENU_XML = """
<node>
  <interface name="com.canonical.dbusmenu">
    <property name="Version" type="u" access="read"/>
    <property name="TextDirection" type="s" access="read"/>
    <property name="Status" type="s" access="read"/>
    <property name="IconThemePath" type="as" access="read"/>
    <method name="GetLayout">
      <arg name="parentId" type="i" direction="in"/>
      <arg name="recursionDepth" type="i" direction="in"/>
      <arg name="propertyNames" type="as" direction="in"/>
      <arg name="revision" type="u" direction="out"/>
      <arg name="layout" type="(ia{sv}av)" direction="out"/>
    </method>
    <method name="GetGroupProperties">
      <arg name="ids" type="ai" direction="in"/>
      <arg name="propertyNames" type="as" direction="in"/>
      <arg name="properties" type="a(ia{sv})" direction="out"/>
    </method>
    <method name="GetProperty">
      <arg name="id" type="i" direction="in"/>
      <arg name="name" type="s" direction="in"/>
      <arg name="value" type="v" direction="out"/>
    </method>
    <method name="Event">
      <arg name="id" type="i" direction="in"/>
      <arg name="eventId" type="s" direction="in"/>
      <arg name="data" type="v" direction="in"/>
      <arg name="timestamp" type="u" direction="in"/>
    </method>
    <method name="EventGroup">
      <arg name="events" type="a(isvu)" direction="in"/>
      <arg name="idErrors" type="ai" direction="out"/>
    </method>
    <method name="AboutToShow">
      <arg name="id" type="i" direction="in"/>
      <arg name="needUpdate" type="b" direction="out"/>
    </method>
    <method name="AboutToShowGroup">
      <arg name="ids" type="ai" direction="in"/>
      <arg name="updatesNeeded" type="ai" direction="out"/>
      <arg name="idErrors" type="ai" direction="out"/>
    </method>
    <signal name="ItemsPropertiesUpdated">
      <arg name="updatedProps" type="a(ia{sv})"/>
      <arg name="removedProps" type="a(ias)"/>
    </signal>
    <signal name="LayoutUpdated">
      <arg name="revision" type="u"/>
      <arg name="parent" type="i"/>
    </signal>
  </interface>
</node>
"""

# id 0 is the root the layout hangs off; the rest are the entries in order.
MENU_ROOT_ID = 0
MENU_OPEN = 1
MENU_RUN = 2
MENU_SEPARATOR = 3
MENU_QUIT = 4

MENU_ITEMS = (
    (MENU_OPEN, "Open Screen Savor", "standard"),
    (MENU_RUN, "Start Screensaver Now", "standard"),
    (MENU_SEPARATOR, None, "separator"),
    (MENU_QUIT, "Quit", "standard"),
)


def _render_pixmaps():
    """The icon as raw ARGB32, for hosts that cannot resolve the themed name.

    IconName is the good path - it lets the panel recolour the icon to match
    itself - and on both GNOME and KDE it resolves, including from inside the
    Flatpak sandbox, because Flatpak exports app-id-prefixed icons to the host.
    This is for everything else. The icon is only ever drawn on a panel, so it
    is baked white here; a host that wanted its own colour would have used the
    name.

    Returns [] on any failure, which reads to a host as "no pixmap, use the
    name" - the right way to fail.
    """
    display = Gdk.Display.get_default()
    if display is None:
        return []

    theme = Gtk.IconTheme.get_for_display(display)
    if not theme.has_icon(ICON_NAME):
        return []

    paintable = theme.lookup_icon(ICON_NAME, None, PIXMAP_SIZES[0], 1,
                                 Gtk.TextDirection.NONE, 0)
    icon_file = paintable.get_file()
    if icon_file is None:
        return []

    try:
        source = icon_file.load_bytes(None)[0].get_data().decode()
    except (GLib.Error, UnicodeDecodeError) as e:
        print(f"Tray: cannot read {ICON_NAME} ({e})")
        return []

    pixmaps = []
    for size in PIXMAP_SIZES:
        # The icon paints with currentColor off a stylesheet, so recolouring is
        # a substitution, and it declares its own size, so scaling is one too.
        svg = source.replace("color: #232629", "color: #ffffff")
        svg = svg.replace('width="16" height="16"', f'width="{size}" height="{size}"')
        try:
            texture = Gdk.Texture.new_from_bytes(GLib.Bytes.new(svg.encode()))
            downloader = Gdk.TextureDownloader.new(texture)
            # A8R8G8B8 is byte order A,R,G,B - which is what the spec means by
            # ARGB32 in network byte order, so no swapping is needed.
            downloader.set_format(Gdk.MemoryFormat.A8R8G8B8)
            data, stride = downloader.download_bytes()
        except GLib.Error as e:
            print(f"Tray: cannot render {ICON_NAME} at {size}px ({e})")
            return []

        raw, width, height = data.get_data(), texture.get_width(), texture.get_height()
        if stride == width * 4:
            rows = bytes(raw)
        else:
            rows = b"".join(bytes(raw[y * stride:y * stride + width * 4])
                            for y in range(height))
        pixmaps.append((width, height, rows))

    return pixmaps


class TrayIcon:
    """The tray item, from owning a bus name to answering the menu.

    Registration is two things happening in any order: this process acquiring
    its own name, and a watcher being there to register with. Both are tracked
    and the registration is made once both hold, which also makes the watcher
    coming back for free - and it does come back, every time GNOME Shell or its
    extension is reloaded, dropping every item it knew about. Without watching
    for that, the icon disappears on the first extension reload and never
    returns.
    """

    def __init__(self, app, on_open, on_run_saver, on_quit):
        self.app = app
        self._actions = {
            MENU_OPEN: on_open,
            MENU_RUN: on_run_saver,
            MENU_QUIT: on_quit,
        }

        self.available = False
        # Whether we have heard anything definite yet. "No tray" and "have not
        # looked yet" are the same value of `available` but must not lead to
        # the same decisions - asking the background portal too early would
        # prompt KDE users about a fallback their desktop does not need.
        self.settled = False
        self.on_available_changed = None

        self._connection = None
        # The spec's own suggestion is org.kde.StatusNotifierItem-<pid>-<n>,
        # which Flatpak cannot express: its own-name wildcards only match
        # dot-separated subnames, and that form separates with hyphens. A
        # subname of the application id needs no permission at all - Flatpak
        # grants app-id.* by default - and both watchers we care about take
        # whatever bus name they are handed. The application is single-instance,
        # so nothing has to be unique per process.
        self._bus_name = f"{APP_ID}.StatusNotifierItem"
        self._owner_id = 0
        self._watcher_id = 0
        self._registrations = []
        self._name_acquired = False
        self._watcher_present = False
        self._pixmaps = None      # rendered once, on first ask
        self._tooltip = ""

        self._sni_info = Gio.DBusNodeInfo.new_for_xml(SNI_XML).interfaces[0]
        self._menu_info = Gio.DBusNodeInfo.new_for_xml(MENU_XML).interfaces[0]

    # -- lifecycle --------------------------------------------------------

    def start(self):
        if self._owner_id:
            return
        self._owner_id = Gio.bus_own_name(
            Gio.BusType.SESSION, self._bus_name, Gio.BusNameOwnerFlags.NONE,
            self._on_bus_acquired, self._on_name_acquired, self._on_name_lost)
        self._watcher_id = Gio.bus_watch_name(
            Gio.BusType.SESSION, WATCHER_NAME, Gio.BusNameWatcherFlags.NONE,
            self._on_watcher_appeared, self._on_watcher_vanished)

    def stop(self):
        if self._watcher_id:
            Gio.bus_unwatch_name(self._watcher_id)
            self._watcher_id = 0
        for registration in self._registrations:
            self._connection.unregister_object(registration)
        self._registrations = []
        if self._owner_id:
            Gio.bus_unown_name(self._owner_id)
            self._owner_id = 0
        self._connection = None
        self._name_acquired = False
        self._watcher_present = False
        self.settled = False
        self._update_state(False, settled=False)

    def set_tooltip(self, text):
        """Second line of the tooltip - used for the idle daemon's status."""
        if text == self._tooltip:
            return
        self._tooltip = text
        self._emit("NewToolTip", None)

    def _update_state(self, available, settled=True):
        """Record where we stand and tell whoever is listening.

        Fires even when nothing changed. The listener's work is cheap and
        idempotent, and it saves reasoning about which of several paths to a
        failed registration got there first.
        """
        self.available = available
        self.settled = settled
        if self.on_available_changed is not None:
            self.on_available_changed(self)

    # -- registration -----------------------------------------------------

    def _on_bus_acquired(self, connection, name):
        self._connection = connection
        try:
            self._registrations = [
                connection.register_object(ITEM_PATH, self._sni_info,
                                           self._on_sni_method,
                                           self._on_sni_property, None),
                connection.register_object(MENU_PATH, self._menu_info,
                                           self._on_menu_method,
                                           self._on_menu_property, None),
            ]
        except GLib.Error as e:
            print(f"Tray: cannot export the item ({e.message})")

    def _on_name_acquired(self, connection, name):
        self._name_acquired = True
        self._register_with_watcher()

    def _on_name_lost(self, connection, name):
        self._name_acquired = False
        self._update_state(False)

    def _on_watcher_appeared(self, connection, name, owner):
        # Not available yet - the watcher still has to accept the item.
        self._watcher_present = True
        self._register_with_watcher()

    def _on_watcher_vanished(self, connection, name):
        # Nothing is drawing the icon any more, so there is no tray to minimize
        # to. Whoever is listening needs to know before they hide a window.
        # This also fires once at start-up when no watcher is running, which is
        # what settles the question of whether this desktop has a tray at all.
        self._watcher_present = False
        self._update_state(False)

    def _register_with_watcher(self):
        if not (self._name_acquired and self._watcher_present):
            return

        def on_registered(source, result, _data=None):
            try:
                source.call_finish(result)
            except GLib.Error as e:
                print(f"Tray: watcher refused the item ({e.message})")
                self._update_state(False)
                return
            self._update_state(True)

        self._connection.call(
            WATCHER_NAME, WATCHER_PATH, WATCHER_NAME,
            "RegisterStatusNotifierItem",
            GLib.Variant("(s)", (self._bus_name,)),
            None, Gio.DBusCallFlags.NONE, -1, None, on_registered)

    def _emit(self, signal, parameters):
        if self._connection is None or not self._registrations:
            return
        try:
            self._connection.emit_signal(None, ITEM_PATH,
                                         "org.kde.StatusNotifierItem",
                                         signal, parameters)
        except GLib.Error as e:
            print(f"Tray: cannot emit {signal} ({e.message})")

    # -- StatusNotifierItem -----------------------------------------------

    def _icon_pixmaps(self):
        if self._pixmaps is None:
            self._pixmaps = _render_pixmaps()
        return self._pixmaps

    def _on_sni_property(self, connection, sender, path, interface, prop):
        if prop == "Category":
            return GLib.Variant("s", "ApplicationStatus")
        if prop == "Id":
            return GLib.Variant("s", APP_ID)
        if prop == "Title":
            return GLib.Variant("s", TITLE)
        if prop == "Status":
            return GLib.Variant("s", "Active")
        if prop == "WindowId":
            return GLib.Variant("i", 0)
        if prop == "IconName":
            return GLib.Variant("s", ICON_NAME)
        if prop == "IconPixmap":
            return GLib.Variant("a(iiay)", self._icon_pixmaps())
        if prop == "ToolTip":
            return GLib.Variant("(sa(iiay)ss)",
                                (ICON_NAME, [], TITLE, self._tooltip))
        if prop == "ItemIsMenu":
            # False, so a left click reaches Activate instead of opening the
            # menu. The menu is still there on right click.
            return GLib.Variant("b", False)
        if prop == "Menu":
            return GLib.Variant("o", MENU_PATH)
        if prop in ("OverlayIconName", "AttentionIconName", "AttentionMovieName"):
            return GLib.Variant("s", "")
        return None

    def _on_sni_method(self, connection, sender, path, interface, method,
                       parameters, invocation):
        if method == "Activate":
            self._actions[MENU_OPEN]()
            invocation.return_value(None)
        elif method in ("SecondaryActivate", "Scroll"):
            invocation.return_value(None)
        else:
            invocation.return_dbus_error("org.freedesktop.DBus.Error.UnknownMethod",
                                         f"No such method {method}")

    # -- dbusmenu ---------------------------------------------------------

    def _on_menu_property(self, connection, sender, path, interface, prop):
        if prop == "Version":
            return GLib.Variant("u", 4)
        if prop == "TextDirection":
            return GLib.Variant("s", "ltr")
        if prop == "Status":
            return GLib.Variant("s", "normal")
        if prop == "IconThemePath":
            return GLib.Variant("as", [])
        return None

    @staticmethod
    def _item_properties(item_id):
        for menu_id, label, kind in MENU_ITEMS:
            if menu_id != item_id:
                continue
            if kind == "separator":
                return {"type": GLib.Variant("s", "separator")}
            return {
                "label": GLib.Variant("s", label),
                "enabled": GLib.Variant("b", True),
                "visible": GLib.Variant("b", True),
            }
        return {}

    def _layout(self):
        children = [
            GLib.Variant("(ia{sv}av)",
                         (menu_id, self._item_properties(menu_id), []))
            for menu_id, _label, _kind in MENU_ITEMS
        ]
        return (MENU_ROOT_ID,
                {"children-display": GLib.Variant("s", "submenu")},
                children)

    def _on_menu_method(self, connection, sender, path, interface, method,
                        parameters, invocation):
        if method == "GetLayout":
            # The revision never moves because the menu never changes, so a
            # host that caches the layout stays correct forever.
            invocation.return_value(
                GLib.Variant("(u(ia{sv}av))", (1, self._layout())))

        elif method == "GetGroupProperties":
            ids = parameters.unpack()[0]
            wanted = ids or [menu_id for menu_id, _l, _k in MENU_ITEMS]
            invocation.return_value(GLib.Variant(
                "(a(ia{sv}))",
                ([(menu_id, self._item_properties(menu_id)) for menu_id in wanted],)))

        elif method == "GetProperty":
            item_id, name = parameters.unpack()
            value = self._item_properties(item_id).get(name)
            if value is None:
                invocation.return_dbus_error(
                    "org.freedesktop.DBus.Error.InvalidArgs",
                    f"No property {name} on item {item_id}")
            else:
                invocation.return_value(GLib.Variant("(v)", (value,)))

        elif method == "Event":
            item_id, event_id = parameters.unpack()[:2]
            invocation.return_value(None)
            if event_id == "clicked":
                self._activate(item_id)

        elif method == "EventGroup":
            events = parameters.unpack()[0]
            invocation.return_value(GLib.Variant("(ai)", ([],)))
            for item_id, event_id, _data, _timestamp in events:
                if event_id == "clicked":
                    self._activate(item_id)

        elif method == "AboutToShow":
            invocation.return_value(GLib.Variant("(b)", (False,)))

        elif method == "AboutToShowGroup":
            invocation.return_value(GLib.Variant("(aiai)", ([], [])))

        else:
            invocation.return_dbus_error("org.freedesktop.DBus.Error.UnknownMethod",
                                         f"No such method {method}")

    def _activate(self, item_id):
        action = self._actions.get(item_id)
        if action is None:
            return
        # Let the D-Bus reply go out before the action runs - Quit tears down
        # the connection this call arrived on, and starting a saver puts up
        # fullscreen windows.
        GLib.idle_add(lambda: (action(), GLib.SOURCE_REMOVE)[1])
