import gi
gi.require_version('Gtk', '4.0')
gi.require_version('Gst', '1.0')
gi.require_version('PangoCairo', '1.0')
from gi.repository import Gtk, Gst, GLib, Pango, PangoCairo, Gdk
import cairo
import functools
import math
import time
import os

from screensavers.base import _AnimatedSaver
from screensavers.weather import (
    OpenMeteoProvider, WeatherFetcher, WeatherLocation, wmo_code_to_icon_name
)

# Initialize GStreamer
Gst.init(None)

# The video that ships with the app, named relative to a data directory rather
# than absolutely: one walk of those covers /app/share inside the sandbox,
# /usr/share for a system install and ~/.local/share for a --user one, because
# Flatpak puts all of them in XDG_DATA_DIRS.
BUNDLED_VIDEO = os.path.join('screensavers', 'videos', 'default-aerial.mp4')

# Rate limits. The floor matters for more than taste - a seek at rate 0 is not a
# stopped video, it is a rejected event.
MIN_SPEED = 0.25
MAX_SPEED = 4.0

DEFAULT_TUNING = {
    "playback_speed": 1.0,
    "show_seconds": 1.0,
    "clock_size": 1.0,
    "position_y": 0.5,
    "glow_intensity": 0.8,
    "background_opacity": 0.3,
    "text_opacity": 0.85,
    "hour_format": 0.0,         # 0=24hr, 1=12hr
    "show_date": 0.0,           # 0=off, 1=on
    "show_weather": 0.0,        # 0=off, 1=on
}

TUNING = dict(DEFAULT_TUNING)

TUNABLES = (
    ("Video", "playback_speed", "Playback Speed",
     "Relative to the speed it was recorded at",
     MIN_SPEED, MAX_SPEED, 0.25, 2),
    ("Clock", "show_seconds", "Show Seconds",
     "Display seconds in addition to hours and minutes", 0.0, 1.0, 1.0, 0),
    ("Clock", "hour_format", "12-Hour Format",
     "Use 12-hour format with AM/PM instead of 24-hour", 0.0, 1.0, 1.0, 0),
    ("Clock", "show_date", "Show Date",
     "Display the date below the time", 0.0, 1.0, 1.0, 0),
    ("Clock", "clock_size", "Clock Size",
     "Scale factor for the clock display", 0.5, 3.0, 0.1, 1),
    ("Clock", "position_y", "Vertical Position",
     "Clock position: 0=top, 0.5=center, 1=bottom", 0.0, 1.0, 0.05, 2),
    ("Weather", "show_weather", "Show Weather",
     "Display current weather in the top right corner", 0.0, 1.0, 1.0, 0),
    ("Appearance", "glow_intensity", "Glow Intensity",
     "Brightness of the glowing effect", 0.0, 1.0, 0.1, 1),
    ("Appearance", "background_opacity", "Background Opacity",
     "Opacity of the frosted glass background", 0.0, 1.0, 0.05, 2),
    ("Appearance", "text_opacity", "Text Opacity",
     "Opacity of the clock text", 0.0, 1.0, 0.05, 2),
)

# Settings that are not numbers, and so cannot ride in the tuning dictionary
# above - that is stored as a{sd}. Each one is a GSettings key of its own, but
# the shape is deliberately the same: the preferences window writes the key and
# the live dictionary together, so a saver already on screen picks the change up
# on its next frame.
#
# Fields: section, GSettings key, title, subtitle, what to show when unset, and
# the MIME types and suffixes the file chooser offers.
FILE_TUNABLES = (
    ("Video", "video-clock-file", "Video File",
     "The looping video played behind the clock",
     "Bundled aerial video",
     ("video/mp4",),
     ("mp4", "m4v")),
)

FILES = {"video-clock-file": ""}
DEFAULT_FILES = dict(FILES)


@functools.cache
def bundled_video_path():
    """Where the video that ships with the app ended up. Resolved once."""
    for data_dir in (GLib.get_user_data_dir(), *GLib.get_system_data_dirs()):
        candidate = os.path.join(data_dir, BUNDLED_VIDEO)
        if os.path.exists(candidate):
            return candidate

    # Nothing installed anywhere, so this is the source tree being run in place.
    local = os.path.abspath(os.path.join(
        os.path.dirname(__file__), '..', '..', 'data', 'videos',
        'default-aerial.mp4'))
    return local if os.path.exists(local) else None


class VideoClockSaver(_AnimatedSaver):
    """Clock over a looping video, in Comfortaa."""

    TUNABLES = TUNABLES
    TUNING = TUNING
    DEFAULT_TUNING = DEFAULT_TUNING
    TUNING_KEY = "video-clock-tuning"

    FILE_TUNABLES = FILE_TUNABLES
    FILES = FILES
    DEFAULT_FILES = DEFAULT_FILES

    WEATHER_LOCATION = WeatherLocation()  # Class attribute for weather location

    # How many frames a pipeline may refuse a speed change before we stop
    # asking. Refusals are normally just the preroll not being finished yet.
    SPEED_ATTEMPTS = 60

    def __init__(self):
        super().__init__()
        self.last_time_str = ""
        self.last_date_str = ""
        self.pipeline = None
        self.video_sink = None
        self.current_sample = None
        self.video_width = 0
        self.video_height = 0

        self._setting = (FILES["video-clock-file"] or "").strip()
        self._path = None           # what the current pipeline is playing
        self._rejected = None       # a file this machine could not decode
        self._giving_up = False     # even the bundled video failed
        self._speed = 1.0           # the rate the pipeline is actually at
        self._speed_refusals = 0

        # Weather
        self.weather_fetcher = None
        self.weather_data = None
        self.weather_icon_texture = None
        self.weather_icon_surface = None  # Cached Cairo surface for icon
        self.last_temp_str = None  # Cache temperature string
        if self.WEATHER_LOCATION.is_valid():
            provider = OpenMeteoProvider()
            self.weather_fetcher = WeatherFetcher(
                provider, self.WEATHER_LOCATION, self._on_weather_update
            )

        # A window torn down by GTK itself never reaches SaverWindow.destroy,
        # so the pipeline gets a second way out. Both are idempotent.
        self.connect("unrealize", lambda *_: self.teardown())

        self._setup_video()

    # -- video ------------------------------------------------------------

    def _wanted_path(self):
        """The file that should be playing.

        Deliberately never touches the disk: this runs once a frame, and the
        stored path is usually a document-portal one, where a stat is a round
        trip through FUSE. Whether the file actually opens is the pipeline's
        business, and _on_error's.
        """
        if self._setting and self._setting != self._rejected:
            return self._setting
        return bundled_video_path()

    def _setup_video(self):
        """Build and start the pipeline for whichever video is wanted."""
        video_path = self._wanted_path()
        if not video_path:
            print("Video Clock: video not found")
            self._giving_up = True
            return

        # location is set on the element rather than written into the launch
        # line: the path comes from the user now, and a quote or a backslash in
        # a filename would otherwise be read as pipeline syntax.
        pipeline_str = (
            'filesrc name=src ! '
            'decodebin ! '
            'videoconvert ! '
            'video/x-raw,format=BGRA ! '
            'appsink name=sink emit-signals=true sync=true max-buffers=2 drop=false'
        )

        try:
            self.pipeline = Gst.parse_launch(pipeline_str)
            self.pipeline.get_by_name('src').set_property('location', video_path)
            self.video_sink = self.pipeline.get_by_name('sink')

            if self.video_sink:
                self.video_sink.set_property('emit-signals', True)
                self.video_sink.connect('new-sample', self._on_new_sample)

                bus = self.pipeline.get_bus()
                bus.add_signal_watch()
                bus.connect('message::eos', self._on_eos)
                bus.connect('message::error', self._on_error)

                # A fresh pipeline always starts at rate 1; _sync_video seeks it
                # to the wanted speed as soon as the preroll allows.
                self._speed = 1.0
                self._speed_refusals = 0

                ret = self.pipeline.set_state(Gst.State.PLAYING)
                if ret == Gst.StateChangeReturn.FAILURE:
                    print("Video Clock: Unable to set pipeline to playing state")
                    self.teardown()
                else:
                    self._path = video_path
                    print(f"Video Clock: Video pipeline started for {video_path}")

        except Exception as e:
            print(f"Video Clock: failed to setup video: {e}")
            self.teardown()

    def teardown(self):
        """Stop the pipeline and let go of it. Idempotent.

        Order matters. The bus watch is a source on the main context that keeps
        the pipeline alive, and the sink is still emitting into a callback bound
        to this widget; both have to be cut before the state change, or a frame
        arrives halfway through the shutdown.
        """
        if self.weather_fetcher is not None:
            self.weather_fetcher.stop()
            self.weather_fetcher = None

        pipeline, self.pipeline = self.pipeline, None
        self.current_sample = None
        self._path = None
        if pipeline is None:
            return

        if self.video_sink is not None:
            self.video_sink.set_property('emit-signals', False)
            self.video_sink = None

        bus = pipeline.get_bus()
        if bus is not None:
            bus.remove_signal_watch()

        pipeline.set_state(Gst.State.NULL)

    def _restart_video(self):
        self.teardown()
        self._setup_video()
        return GLib.SOURCE_REMOVE

    def _sync_video(self):
        """Catch up with settings changed while this saver is on screen."""
        setting = (FILES["video-clock-file"] or "").strip()
        if setting != self._setting:
            # A fresh choice earns a fresh chance, even where it names the same
            # file that failed a moment ago and has since been put right.
            self._setting = setting
            self._rejected = None
            self._giving_up = False
            self._restart_video()
            return

        if self._giving_up:
            return

        if self.pipeline is None:
            self._setup_video()
            return

        speed = min(max(TUNING["playback_speed"], MIN_SPEED), MAX_SPEED)
        if abs(speed - self._speed) < 1e-6:
            return

        if self._apply_speed(speed):
            self._speed = speed
            self._speed_refusals = 0
        else:
            # Usually the preroll simply is not finished, so try again next
            # frame - but a video that will never seek must not be asked sixty
            # times a second for the rest of the run.
            self._speed_refusals += 1
            if self._speed_refusals > self.SPEED_ATTEMPTS:
                print(f"Video Clock: this video will not play at {speed}x")
                self._speed = speed
                self._speed_refusals = 0

    def _apply_speed(self, speed):
        """Seek in place at a new rate. Returns whether the pipeline took it.

        A pipeline has no rate property. A rate reaches GStreamer only as a
        field of a seek event, so changing speed means seeking to where playback
        already is - which is legal only once prerolled. get_state with a zero
        timeout answers immediately rather than waiting, and says ASYNC while the
        preroll is still in flight, which is exactly the case to sit out.
        """
        ret, state, _pending = self.pipeline.get_state(0)
        if ret != Gst.StateChangeReturn.SUCCESS or state not in (
                Gst.State.PLAYING, Gst.State.PAUSED):
            return False

        ok, position = self.pipeline.query_position(Gst.Format.TIME)
        if not ok:
            position = 0

        # ACCURATE rather than KEY_UNIT: nudging the speed must not throw the
        # picture back to the previous keyframe.
        return self.pipeline.seek(
            speed, Gst.Format.TIME,
            Gst.SeekFlags.FLUSH | Gst.SeekFlags.ACCURATE,
            Gst.SeekType.SET, position,
            Gst.SeekType.END, 0)

    def _on_new_sample(self, sink):
        """Callback when a new video frame is available.

        Runs on a GStreamer streaming thread, not the main one. The single
        attribute write is all the handover there is - on_draw takes its own
        reference to whatever sample is current and works from that.
        """
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
                    print(f"Video Clock: Video frame {width}x{height}")
        return Gst.FlowReturn.OK

    def _on_eos(self, bus, msg):
        """Loop the video, at whatever speed is currently set.

        Not seek_simple: that is defined as a seek at rate 1, so every time the
        video came round it would quietly undo the playback speed.
        """
        if self.pipeline is not None:
            self.pipeline.seek(self._speed, Gst.Format.TIME,
                               Gst.SeekFlags.FLUSH | Gst.SeekFlags.KEY_UNIT,
                               Gst.SeekType.SET, 0,
                               Gst.SeekType.END, 0)
        return True

    def _on_error(self, bus, msg):
        """Handle GStreamer errors.

        A file the user chose is the likely casualty - moved, deleted, or in a
        codec this machine cannot decode - so fall back to the one that ships
        with the app rather than leaving a bare gradient up. If that is what
        failed, there is nowhere left to fall back to.
        """
        err, debug = msg.parse_error()
        print(f"Video Clock: GStreamer error: {err}, {debug}")

        bundled = bundled_video_path()
        if self._path is not None and self._path != bundled and bundled:
            self._rejected = self._path
            # Not from inside the bus dispatch that is running right now:
            # taking the bus watch down under its own handler is asking for it.
            GLib.idle_add(self._restart_video)
        else:
            self._giving_up = True
        return True

    # -- weather ----------------------------------------------------------

    def _on_weather_update(self, data):
        """Called when weather data arrives or refreshes."""
        self.weather_data = data
        self.weather_icon_texture = None  # force reload on next draw
        self.weather_icon_surface = None  # clear cached surface
        self.last_temp_str = None  # clear cached temp string

    def _load_weather_icon(self, icon_name):
        """Load a symbolic icon from the theme and cache the texture."""
        if not icon_name:
            return None

        try:
            display = self.get_display()
            if not display:
                return None

            theme = Gtk.IconTheme.get_for_display(display)
            paintable = theme.lookup_icon(
                icon_name, None, 48, 1,
                self.get_direction(),
                Gtk.IconLookupFlags.FORCE_SYMBOLIC
            )

            if paintable:
                return paintable.download_texture()
        except Exception as e:
            print(f"Video Clock: failed to load icon {icon_name}: {e}")

        return None

    # -- animation --------------------------------------------------------

    def advance(self, dt):
        """Always redraw for smooth video playback."""
        self._sync_video()

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

        # Date string
        show_date = TUNING["show_date"] > 0.5
        if show_date:
            self.last_date_str = time.strftime("%A %-d %B", current_time)
        else:
            self.last_date_str = ""

        # Always return True for smooth video
        return True

    def on_draw(self, area, cr, width, height):
        if width <= 0 or height <= 0:
            return

        # Draw video frame if available
        sample = self.current_sample
        if sample and self.video_width > 0 and self.video_height > 0:
            self._draw_video_frame(cr, sample, width, height)
        else:
            # Fallback gradient background
            gradient = cairo.LinearGradient(0, 0, 0, height)
            gradient.add_color_stop_rgb(0, 0.2, 0.4, 0.8)
            gradient.add_color_stop_rgb(1, 0.6, 0.8, 1.0)
            cr.set_source(gradient)
            cr.paint()

        # Draw clock overlay
        self._draw_clock(cr, width, height)

        # Draw weather overlay
        if TUNING["show_weather"] > 0.5:
            self._draw_weather(cr, width, height)

    def _draw_video_frame(self, cr, sample, width, height):
        """Draw the given video frame scaled to cover the screen."""
        buffer = sample.get_buffer()

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
            print(f"Video Clock: Error drawing video frame: {e}")
        finally:
            buffer.unmap(map_info)

    def _draw_clock(self, cr, width, height):
        """Draw the clock (and optional date) using Comfortaa font."""
        if not self.last_time_str:
            return

        clock_scale = TUNING["clock_size"]
        pos_y = TUNING["position_y"]
        bg_opacity = TUNING["background_opacity"]
        glow = TUNING["glow_intensity"]
        text_opacity = TUNING["text_opacity"]

        # Create Pango layout for time
        time_layout = PangoCairo.create_layout(cr)
        font_size = int(120 * clock_scale)
        font_desc = Pango.FontDescription(f"Comfortaa Bold {font_size}")
        time_layout.set_font_description(font_desc)
        time_layout.set_text(self.last_time_str, -1)

        # Get time dimensions
        ink_rect, logical_rect = time_layout.get_pixel_extents()
        time_width = logical_rect.width
        time_height = logical_rect.height

        # Date layout if enabled
        date_layout = None
        date_width = 0
        date_height = 0
        if self.last_date_str:
            date_layout = PangoCairo.create_layout(cr)
            date_font_size = int(40 * clock_scale)
            date_font_desc = Pango.FontDescription(f"Comfortaa {date_font_size}")
            date_layout.set_font_description(date_font_desc)
            date_layout.set_text(self.last_date_str, -1)
            ink_rect, logical_rect = date_layout.get_pixel_extents()
            date_width = logical_rect.width
            date_height = logical_rect.height

        # Total height and width for centering
        total_width = max(time_width, date_width)
        spacing = 20 * clock_scale if date_layout else 0
        total_height = time_height + spacing + date_height

        # Calculate position
        x = (width - time_width) / 2
        y = pos_y * (height - total_height)

        date_x = (width - date_width) / 2 if date_layout else 0
        date_y = y + time_height + spacing

        # Draw frosted glass background
        if bg_opacity > 0:
            padding = 40 * clock_scale
            corner_radius = 30 * clock_scale
            bx = (width - total_width) / 2 - padding
            by = y - padding
            bw = total_width + 2 * padding
            bh = total_height + 2 * padding

            cr.new_sub_path()
            cr.arc(bx + bw - corner_radius, by + corner_radius, corner_radius, -math.pi/2, 0)
            cr.arc(bx + bw - corner_radius, by + bh - corner_radius, corner_radius, 0, math.pi/2)
            cr.arc(bx + corner_radius, by + bh - corner_radius, corner_radius, math.pi/2, math.pi)
            cr.arc(bx + corner_radius, by + corner_radius, corner_radius, math.pi, 3*math.pi/2)
            cr.close_path()

            cr.set_source_rgba(1, 1, 1, bg_opacity * 0.2)
            cr.fill()

        # Draw glow layers for time
        if glow > 0:
            for blur in range(5):
                alpha = glow * 0.4 * (5 - blur) / 5
                blur_offset = blur * 3
                cr.save()
                cr.move_to(x - blur_offset, y - blur_offset)
                cr.set_source_rgba(0.95, 0.97, 1.0, alpha)
                PangoCairo.show_layout(cr, time_layout)
                cr.restore()

        # Draw solid time text
        cr.move_to(x, y)
        cr.set_source_rgba(0.95, 0.97, 1.0, text_opacity)
        PangoCairo.show_layout(cr, time_layout)

        # Draw date if present
        if date_layout:
            # Glow for date (smaller, subtler)
            if glow > 0:
                for blur in range(3):
                    alpha = glow * 0.3 * (3 - blur) / 3
                    blur_offset = blur * 2
                    cr.save()
                    cr.move_to(date_x - blur_offset, date_y - blur_offset)
                    cr.set_source_rgba(0.95, 0.97, 1.0, alpha)
                    PangoCairo.show_layout(cr, date_layout)
                    cr.restore()

            # Solid date text (slightly more transparent than time)
            cr.move_to(date_x, date_y)
            cr.set_source_rgba(0.95, 0.97, 1.0, text_opacity * 0.85)
            PangoCairo.show_layout(cr, date_layout)

    def _draw_weather(self, cr, width, height):
        """Draw weather icon and temperature in the top-right corner."""
        if not self.weather_data or self.weather_data.temperature is None:
            return

        # Cache temperature string (only regenerate when temp changes)
        temp_c = self.weather_data.temperature
        temp_str = f"{round(temp_c)}°"
        if temp_str != self.last_temp_str:
            self.last_temp_str = temp_str

        # Load and cache icon surface (only once per weather update)
        if self.weather_icon_surface is None and self.weather_data.weather_code is not None:
            if self.weather_icon_texture is None:
                icon_name = wmo_code_to_icon_name(
                    self.weather_data.weather_code,
                    self.weather_data.is_day
                )
                self.weather_icon_texture = self._load_weather_icon(icon_name)

            if self.weather_icon_texture:
                try:
                    # Pre-render the icon surface at the target size
                    icon_size = 40
                    scale = icon_size / self.weather_icon_texture.get_width()

                    self.weather_icon_surface = cairo.ImageSurface(
                        cairo.FORMAT_ARGB32, icon_size, icon_size
                    )
                    icon_cr = cairo.Context(self.weather_icon_surface)
                    icon_cr.scale(scale, scale)

                    temp_surface = Gdk.cairo_surface_create_from_texture(
                        self.weather_icon_texture
                    )
                    icon_cr.set_source_surface(temp_surface, 0, 0)
                    icon_cr.paint()
                except Exception as e:
                    print(f"Video Clock: failed to pre-render weather icon: {e}")
                    self.weather_icon_surface = None

        # Create layout for temperature text
        layout = PangoCairo.create_layout(cr)
        font_size = 32
        font_desc = Pango.FontDescription(f"Comfortaa Bold {font_size}")
        layout.set_font_description(font_desc)
        layout.set_text(self.last_temp_str, -1)

        # Get text dimensions
        ink_rect, logical_rect = layout.get_pixel_extents()
        temp_width = logical_rect.width
        temp_height = logical_rect.height

        # Icon dimensions
        icon_size = 40
        spacing = 12
        padding = 24

        # Position in top-right corner
        total_width = icon_size + spacing + temp_width
        x = width - total_width - padding
        y = padding

        # Draw pre-rendered icon (much faster than scaling every frame)
        if self.weather_icon_surface:
            cr.save()
            cr.set_source_surface(self.weather_icon_surface, x, y)
            cr.paint_with_alpha(0.9)
            cr.restore()

        # Draw temperature text
        text_x = x + icon_size + spacing
        text_y = y + (icon_size - temp_height) / 2
        cr.move_to(text_x, text_y)
        cr.set_source_rgba(0.95, 0.97, 1.0, 0.9)
        PangoCairo.show_layout(cr, layout)
