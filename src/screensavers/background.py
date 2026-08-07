"""Declaring ourselves to the desktop as a background app, through the portal.

This is the fallback for desktops with no tray. On stock GNOME the tray only
exists if the AppIndicator extension is installed, so without it a running
Screen Savor is invisible again - and this time with no icon to quit from.
Asking the background portal at least puts the app in GNOME's Quick Settings
under Background Apps, which lists it by name and offers to quit it.

Only called when screensavers.tray found no watcher. That gate matters on KDE:
xdg-desktop-portal-kde answers RequestBackground with a permission dialog, and
Plasma always has a tray, so an unconditional call would interrupt KDE users to
ask about a fallback they are never going to use.

Portals need nothing in the Flatpak finish-args - org.freedesktop.portal.* is
allowed by default. Outside a sandbox the call simply fails, which is fine:
the Background Apps list is something the shell keeps for Flatpak apps.
"""

from gi.repository import Gio, GLib

PORTAL_NAME = "org.freedesktop.portal.Desktop"
PORTAL_PATH = "/org/freedesktop/portal/desktop"
BACKGROUND_IFACE = "org.freedesktop.portal.Background"

REASON = "Screen Savor watches for idle time while its window is closed"

_requested = False


def request(on_result=None):
    """Ask to keep running in the background. Safe to call more than once.

    autostart is deliberately not requested. Screen Savor should run when the
    user starts it, not follow them into every login - and asking for autostart
    is what turns this from an informational listing into a real permission
    prompt on some desktops.
    """
    global _requested
    if _requested:
        return
    _requested = True

    def on_call(source, result, _data=None):
        try:
            source.call_finish(result)
        except GLib.Error as e:
            # Not in a sandbox, no portal implementation, or the user said no.
            # None of those are worth bothering anyone about.
            print(f"Background portal: not registered ({e.message})")
            if on_result is not None:
                on_result(False)
            return
        print("Background portal: registered as a background app")
        if on_result is not None:
            on_result(True)

    options = {
        "reason": GLib.Variant("s", REASON),
        "autostart": GLib.Variant("b", False),
    }

    bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
    bus.call(PORTAL_NAME, PORTAL_PATH, BACKGROUND_IFACE, "RequestBackground",
             GLib.Variant("(sa{sv})", ("", options)),
             None, Gio.DBusCallFlags.NONE, -1, None, on_call)
