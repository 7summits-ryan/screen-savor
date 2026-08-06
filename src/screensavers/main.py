import sys
import gi

gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')
from gi.repository import Gtk, Gio, Adw, GLib

from screensavers.window import ScreensaversWindow

class IdleDaemon:
    def __init__(self, app):
        self.app = app
        self.settings = Gio.Settings.new("software._7summits.ScreenSavor")
        self.watch_id = 0
        self.dbus_proxy = None
        self.saver_win = None
        self.is_holding = False
        
        self.settings.connect("changed::daemon-enabled", self.update_daemon)
        self.settings.connect("changed::idle-timeout", self.update_daemon)
        
        # Connect to DBus
        Gio.DBusProxy.new_for_bus(
            Gio.BusType.SESSION,
            Gio.DBusProxyFlags.NONE,
            None,
            "org.gnome.Mutter.IdleMonitor",
            "/org/gnome/Mutter/IdleMonitor/Core",
            "org.gnome.Mutter.IdleMonitor",
            None,
            self.on_proxy_ready
        )

    def on_proxy_ready(self, source_object, result, user_data=None):
        try:
            self.dbus_proxy = Gio.DBusProxy.new_for_bus_finish(result)
            self.dbus_proxy.connect("g-signal", self.on_dbus_signal)
            self.update_daemon()
        except Exception as e:
            print(f"Error connecting to IdleMonitor: {e}")

    def update_daemon(self, *args):
        if self.dbus_proxy is None:
            return

        # Remove old watch
        if self.watch_id != 0:
            try:
                self.dbus_proxy.call_sync("RemoveWatch", GLib.Variant("(u)", (self.watch_id,)), Gio.DBusCallFlags.NONE, -1, None)
            except Exception:
                pass
            self.watch_id = 0

        enabled = self.settings.get_boolean("daemon-enabled")
        
        if enabled and not self.is_holding:
            self.app.hold()
            self.is_holding = True
        elif not enabled and self.is_holding:
            self.app.release()
            self.is_holding = False

        if not enabled:
            return

        timeout_minutes = self.settings.get_int("idle-timeout")
        timeout_ms = timeout_minutes * 60 * 1000

        try:
            res = self.dbus_proxy.call_sync("AddIdleWatch", GLib.Variant("(t)", (timeout_ms,)), Gio.DBusCallFlags.NONE, -1, None)
            self.watch_id = res.unpack()[0]
        except Exception as e:
            print(f"Error setting idle watch: {e}")

    def on_dbus_signal(self, proxy, sender_name, signal_name, parameters):
        if signal_name == "WatchFired":
            watch_id = parameters.unpack()[0]
            if watch_id == self.watch_id:
                self.trigger_screensaver()

    def trigger_screensaver(self):
        if self.saver_win is not None:
            return

        from screensavers.window import SaverWindow
        from screensavers.savers import DVDLogoSaver, MatrixSaver, ColorPulseSaver
        
        savers = {
            "DVD Logo": DVDLogoSaver,
            "Matrix Rain": MatrixSaver,
            "Color Pulse": ColorPulseSaver
        }
        
        saver_name = self.settings.get_string("default-saver")
        saver_cls = savers.get(saver_name, DVDLogoSaver)
        
        self.saver_win = SaverWindow(saver_cls, self.app)
        
        def on_close(*args):
            self.saver_win = None
            
        self.saver_win.connect("close-request", on_close)
        self.saver_win.present()

class ScreensaversApplication(Adw.Application):
    def __init__(self):
        super().__init__(application_id='software._7summits.ScreenSavor',
                         flags=Gio.ApplicationFlags.FLAGS_NONE)
        self.daemon = None

    def do_startup(self):
        Adw.Application.do_startup(self)
        self.daemon = IdleDaemon(self)

    def do_activate(self):
        win = self.props.active_window
        if not win:
            win = ScreensaversWindow(application=self)
        win.present()

def main(args):
    app = ScreensaversApplication()
    return app.run(args)
