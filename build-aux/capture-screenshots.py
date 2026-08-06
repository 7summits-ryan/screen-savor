#!/usr/bin/env python3
"""Offscreen screenshot capture for the metainfo. Not installed; dev tool only."""
import sys, os, math, random
import cairo
import gi
gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')
from gi.repository import Gtk, Adw, GLib, Gdk

sys.path.insert(1, '/app/share/screensavers')
from screensavers.savers import DVDLogoSaver, MatrixSaver, GorillasSaver
from screensavers.gorillas import FLIGHT, LOGICAL_H
from screensavers.window import ScreensaversWindow

OUT = sys.argv[1]
W, H = 1920, 1080
os.makedirs(OUT, exist_ok=True)
random.seed(7)


def render_saver(saver, frames, path, setup=None):
    """Drive a DrawingArea's draw func straight onto an image surface.

    There is no frame clock offscreen, so the savers are stepped by hand. The
    first draw is what sizes and seeds them, so draw and step alternate. The
    step is long enough to guarantee the Matrix saver advances a row each time.
    """
    surface = cairo.ImageSurface(cairo.FORMAT_RGB24, W, H)
    if setup:
        setup(saver)
    for _ in range(frames):
        # A fresh context per frame, the way GTK hands one to the draw func -
        # a saver that transforms the context would otherwise compound it.
        saver.on_draw(saver, cairo.Context(surface), W, H)
        saver.advance(1 / 20)
    surface.write_to_png(path)
    print("wrote", path)


def dvd_setup(s):
    # Park the logo mid-flight rather than at its spawn corner.
    s.x, s.y = 1180, 690
    s.color = (0.98, 0.51, 0.18)


def render_gorillas(path):
    """Catch the match with a banana actually in the air.

    Stepping a fixed number of frames would land wherever it lands - most of
    them are two gorillas standing still - so run the simulation on until a
    throw is over the city, then draw that.
    """
    random.seed(3)
    saver = GorillasSaver()
    surface = cairo.ImageSurface(cairo.FORMAT_RGB24, W, H)
    saver.on_draw(saver, cairo.Context(surface), W, H)

    for _ in range(6000):
        saver.advance(1 / 60)
        if saver._state != FLIGHT or saver._ban is None:
            continue
        x, y = saver._ban
        if 0 < x < saver._lw and LOGICAL_H * 0.2 < y < LOGICAL_H * 0.55:
            break

    saver.on_draw(saver, cairo.Context(surface), W, H)
    surface.write_to_png(path)
    print("wrote", path)


render_saver(DVDLogoSaver(), 1, f"{OUT}/dvd-logo.png", dvd_setup)
# Matrix opens with drops already in flight; a few steps just varies the frame.
render_saver(MatrixSaver(), 40, f"{OUT}/matrix-rain.png")
render_gorillas(f"{OUT}/gorillas.png")
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
