import gi
gi.require_version('Gtk', '4.0')
gi.require_version('Gst', '1.0')
gi.require_version('PangoCairo', '1.0')
from gi.repository import Gtk, Gst, GLib, Pango, PangoCairo
import cairo
import math
import time
import os

from screensavers.base import _AnimatedSaver

# Initialize GStreamer
Gst.init(None)

DEFAULT_TUNING = {
    "show_seconds": 1.0,
    "clock_size": 1.0,
    "position_y": 0.5,
    "glow_intensity": 0.8,
    "background_opacity": 0.3,
    "text_opacity": 0.85,
    "hour_format": 0.0,         # 0=24hr, 1=12hr
}

TUNING = dict(DEFAULT_TUNING)

TUNABLES = (
    ("Clock", "show_seconds", "Show Seconds",
     "Display seconds in addition to hours and minutes", 0.0, 1.0, 1.0, 0),
    ("Clock", "hour_format", "12-Hour Format",
     "Use 12-hour format with AM/PM instead of 24-hour", 0.0, 1.0, 1.0, 0),
    ("Clock", "clock_size", "Clock Size",
     "Scale factor for the clock display", 0.5, 3.0, 0.1, 1),
    ("Clock", "position_y", "Vertical Position",
     "Clock position: 0=top, 0.5=center, 1=bottom", 0.0, 1.0, 0.05, 2),
    ("Appearance", "glow_intensity", "Glow Intensity",
     "Brightness of the glowing effect", 0.0, 1.0, 0.1, 1),
    ("Appearance", "background_opacity", "Background Opacity",
     "Opacity of the frosted glass background", 0.0, 1.0, 0.05, 2),
    ("Appearance", "text_opacity", "Text Opacity",
     "Opacity of the clock text", 0.0, 1.0, 0.05, 2),
)


class AerialClockSaver(_AnimatedSaver):
    """Beautiful clock with video background using Comfortaa font."""

    TUNABLES = TUNABLES
    TUNING = TUNING
    DEFAULT_TUNING = DEFAULT_TUNING
    TUNING_KEY = "aerial-clock-tuning"

    def __init__(self):
        super().__init__()
        self.last_time_str = ""
        self.pipeline = None
        self.video_sink = None
        self.current_sample = None
        self.video_width = 0
        self.video_height = 0
        self._setup_video()

    def _find_video_path(self):
        """Find the default aerial video."""
        installed_path = '/app/share/screensavers/videos/default-aerial.mp4'
        if os.path.exists(installed_path):
            return installed_path

        local_path = os.path.join(os.path.dirname(__file__), '..', '..',
                                  'data', 'videos', 'default-aerial.mp4')
        local_path = os.path.abspath(local_path)
        if os.path.exists(local_path):
            return local_path

        return None

    def _setup_video(self):
        """Setup GStreamer pipeline for video playback."""
        video_path = self._find_video_path()
        if not video_path:
            print("Aerial Clock: video not found")
            return

        # Pipeline with proper sync for correct playback speed
        pipeline_str = (
            f'filesrc location="{video_path}" ! '
            'decodebin ! '
            'videoconvert ! '
            'video/x-raw,format=BGRA ! '
            'appsink name=sink emit-signals=true sync=true max-buffers=2 drop=false'
        )

        try:
            self.pipeline = Gst.parse_launch(pipeline_str)
            self.video_sink = self.pipeline.get_by_name('sink')

            if self.video_sink:
                self.video_sink.set_property('emit-signals', True)
                self.video_sink.connect('new-sample', self._on_new_sample)

                bus = self.pipeline.get_bus()
                bus.add_signal_watch()
                bus.connect('message::eos', self._on_eos)
                bus.connect('message::error', self._on_error)

                ret = self.pipeline.set_state(Gst.State.PLAYING)
                if ret == Gst.StateChangeReturn.FAILURE:
                    print("Aerial Clock: Unable to set pipeline to playing state")
                    self.pipeline = None
                else:
                    print(f"Aerial Clock: Video pipeline started for {video_path}")

        except Exception as e:
            print(f"Aerial Clock: failed to setup video: {e}")
            self.pipeline = None

    def _on_new_sample(self, sink):
        """Callback when a new video frame is available."""
        sample = sink.emit('pull-sample')
        if sample:
            self.current_sample = sample
            caps = sample.get_caps()
            if caps:
                structure = caps.get_structure(0)
                width = structure.get_value('width')
                height = structure.get_value('height')
                if width != self.video_width or height != self.video_height:
                    self.video_width = width
                    self.video_height = height
                    print(f"Aerial Clock: Video frame {width}x{height}")
        return Gst.FlowReturn.OK

    def _on_eos(self, bus, msg):
        """Loop video when it reaches the end."""
        if self.pipeline:
            self.pipeline.seek_simple(Gst.Format.TIME,
                                     Gst.SeekFlags.FLUSH | Gst.SeekFlags.KEY_UNIT,
                                     0)
        return True

    def _on_error(self, bus, msg):
        """Handle GStreamer errors."""
        err, debug = msg.parse_error()
        print(f"Aerial Clock: GStreamer error: {err}, {debug}")
        return True

    def advance(self, dt):
        """Always redraw for smooth video playback."""
        current_time = time.localtime()
        show_seconds = TUNING["show_seconds"] > 0.5
        use_12hr = TUNING["hour_format"] > 0.5

        if use_12hr:
            # 12-hour format with AM/PM
            if show_seconds:
                time_str = time.strftime("%I:%M:%S %p", current_time)
            else:
                time_str = time.strftime("%I:%M %p", current_time)
        else:
            # 24-hour format
            if show_seconds:
                time_str = time.strftime("%H:%M:%S", current_time)
            else:
                time_str = time.strftime("%H:%M", current_time)

        self.last_time_str = time_str
        # Always return True for smooth video
        return True

    def on_draw(self, area, cr, width, height):
        if width <= 0 or height <= 0:
            return

        # Draw video frame if available
        if self.current_sample and self.video_width > 0 and self.video_height > 0:
            self._draw_video_frame(cr, width, height)
        else:
            # Fallback gradient background
            gradient = cairo.LinearGradient(0, 0, 0, height)
            gradient.add_color_stop_rgb(0, 0.2, 0.4, 0.8)
            gradient.add_color_stop_rgb(1, 0.6, 0.8, 1.0)
            cr.set_source(gradient)
            cr.paint()

        # Draw clock overlay
        self._draw_clock(cr, width, height)

    def _draw_video_frame(self, cr, width, height):
        """Draw the current video frame scaled to cover the screen."""
        buffer = self.current_sample.get_buffer()

        success, map_info = buffer.map(Gst.MapFlags.READ)
        if not success:
            return

        try:
            stride = cairo.ImageSurface.format_stride_for_width(
                cairo.FORMAT_ARGB32, self.video_width)

            # Copy to writable buffer
            data = bytearray(map_info.data)

            surface = cairo.ImageSurface.create_for_data(
                data,
                cairo.FORMAT_ARGB32,
                self.video_width,
                self.video_height,
                stride
            )

            # Scale to cover
            scale = max(width / self.video_width, height / self.video_height)
            scaled_w = self.video_width * scale
            scaled_h = self.video_height * scale
            offset_x = (width - scaled_w) / 2
            offset_y = (height - scaled_h) / 2

            cr.save()
            cr.translate(offset_x, offset_y)
            cr.scale(scale, scale)
            cr.set_source_surface(surface, 0, 0)
            cr.paint()
            cr.restore()

        except Exception as e:
            print(f"Aerial Clock: Error drawing video frame: {e}")
        finally:
            buffer.unmap(map_info)

    def _draw_clock(self, cr, width, height):
        """Draw the clock using Comfortaa font."""
        if not self.last_time_str:
            return

        clock_scale = TUNING["clock_size"]
        pos_y = TUNING["position_y"]
        bg_opacity = TUNING["background_opacity"]
        glow = TUNING["glow_intensity"]
        text_opacity = TUNING["text_opacity"]

        # Create Pango layout for text
        layout = PangoCairo.create_layout(cr)

        # Load Comfortaa font
        font_size = int(120 * clock_scale)
        font_desc = Pango.FontDescription(f"Comfortaa Bold {font_size}")
        layout.set_font_description(font_desc)
        layout.set_text(self.last_time_str, -1)

        # Get text dimensions
        ink_rect, logical_rect = layout.get_pixel_extents()
        text_width = logical_rect.width
        text_height = logical_rect.height

        # Calculate position
        x = (width - text_width) / 2
        y = pos_y * (height - text_height)

        # Draw frosted glass background
        if bg_opacity > 0:
            padding = 40 * clock_scale
            corner_radius = 30 * clock_scale
            bx = x - padding
            by = y - padding
            bw = text_width + 2 * padding
            bh = text_height + 2 * padding

            cr.new_sub_path()
            cr.arc(bx + bw - corner_radius, by + corner_radius, corner_radius, -math.pi/2, 0)
            cr.arc(bx + bw - corner_radius, by + bh - corner_radius, corner_radius, 0, math.pi/2)
            cr.arc(bx + corner_radius, by + bh - corner_radius, corner_radius, math.pi/2, math.pi)
            cr.arc(bx + corner_radius, by + corner_radius, corner_radius, math.pi, 3*math.pi/2)
            cr.close_path()

            cr.set_source_rgba(1, 1, 1, bg_opacity * 0.2)
            cr.fill()

        # Draw glow layers
        if glow > 0:
            for blur in range(5):
                alpha = glow * 0.4 * (5 - blur) / 5
                blur_offset = blur * 3
                cr.save()
                cr.move_to(x - blur_offset, y - blur_offset)
                cr.set_source_rgba(0.95, 0.97, 1.0, alpha)
                PangoCairo.show_layout(cr, layout)
                cr.restore()

        # Draw solid text (tunable opacity)
        cr.move_to(x, y)
        cr.set_source_rgba(0.95, 0.97, 1.0, text_opacity)
        PangoCairo.show_layout(cr, layout)
