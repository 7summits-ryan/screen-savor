import gi
gi.require_version('Gtk', '4.0')
from gi.repository import Gtk, GLib


class _AnimatedSaver(Gtk.DrawingArea):
    """Base class that drives an animation off the widget's frame clock.

    The frame clock only runs while the widget is actually on screen, so a saver
    that is hidden or occluded costs nothing and resumes cleanly when it comes
    back - a GLib timeout has to tear itself down on unmap and never restarts.
    It also paces to the real display rather than a fixed 60Hz guess.

    Subclasses get the seconds elapsed since the last frame and return whether
    anything changed, so a frame that would render identically is never drawn.
    """

    MAX_DT = 0.1  # after a stall, carry on as if one slow frame had passed

    def __init__(self):
        super().__init__()
        self.set_draw_func(self.on_draw)
        self._last_frame = 0
        self.add_tick_callback(self._on_tick)

    def advance(self, dt):
        """Step the animation by dt seconds. Returns True if a redraw is due."""
        raise NotImplementedError

    def _on_tick(self, widget, clock):
        now = clock.get_frame_time()
        if self._last_frame:
            dt = min((now - self._last_frame) / 1000000.0, self.MAX_DT)
        else:
            dt = 0.0
        self._last_frame = now
        if self.advance(dt):
            self.queue_draw()
        return GLib.SOURCE_CONTINUE
