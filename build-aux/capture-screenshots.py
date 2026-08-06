#!/usr/bin/env python3
"""Offscreen screenshot capture for the metainfo. Not installed; dev tool only."""
import sys, os, math, random
import cairo
import gi
gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')
from gi.repository import Gtk, Adw, GLib, Gdk

sys.path.insert(1, '/app/share/screensavers')
from screensavers.savers import DVDLogoSaver, MatrixSaver
from screensavers.window import ScreensaversWindow

OUT = sys.argv[1]
W, H = 1920, 1080
os.makedirs(OUT, exist_ok=True)
random.seed(7)


def render_saver(saver, frames, path, setup=None):
    """Drive a DrawingArea's draw func straight onto an image surface."""
    surface = cairo.ImageSurface(cairo.FORMAT_RGB24, W, H)
    cr = cairo.Context(surface)
    if setup:
        setup(saver)
    for _ in range(frames):
        saver.on_draw(saver, cr, W, H)
    surface.write_to_png(path)
    print("wrote", path)


def dvd_setup(s):
    # Park the logo mid-flight rather than at its spawn corner.
    s.x, s.y = 1180, 690
    s.color = (0.98, 0.51, 0.18)


render_saver(DVDLogoSaver(), 1, f"{OUT}/dvd-logo.png", dvd_setup)
# Matrix needs many frames for the trails to build up through the fade layer.
render_saver(MatrixSaver(), 90, f"{OUT}/matrix-rain.png")
# ColorPulseSaver is a single full-screen cr.paint(), so a capture of it is just a
# flat colour field. Not worth shipping as a store screenshot.


class CaptureApp(Adw.Application):
    def __init__(self):
        super().__init__(application_id='software._7summits.ScreenSavor.Capture')

    def do_activate(self):
        win = ScreensaversWindow(application=self)
        win.set_default_size(760, 720)
        win.present()
        GLib.timeout_add(1200, self.grab, win)

    def grab(self, win):
        paintable = Gtk.WidgetPaintable.new(win)
        w, h = win.get_width(), win.get_height()
        snap = Gtk.Snapshot()
        paintable.snapshot(snap, w, h)
        node = snap.to_node()
        renderer = win.get_native().get_renderer()
        tex = renderer.render_texture(node, None)
        tex.save_to_png(f"{OUT}/main-window.png")
        print("wrote", f"{OUT}/main-window.png", w, h)
        self.quit()
        return False


CaptureApp().run([])
