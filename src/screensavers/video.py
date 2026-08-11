import gi
gi.require_version('Gtk', '4.0')
gi.require_version('Gst', '1.0')
gi.require_version('PangoCairo', '1.0')
from gi.repository import Gtk, Gst, GLib, Pango, PangoCairo
import cairo
import math
import time

from screensavers.base import _AnimatedSaver
from screensavers.weather import (
    OpenMeteoProvider, WeatherFetcher, WeatherLocation
)

# Initialize GStreamer
Gst.init(None)

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
    "pad_hour": 0.0,            # 0=4:01, 1=04:01
    "show_date": 0.0,           # 0=off, 1=on
    "show_weather": 0.0,        # 0=off, 1=on
    "weather_opacity": 0.9,     # temperature text opacity
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
    ("Clock", "pad_hour", "Leading Zero",
     "Pad the hour to two digits, so 04:01 rather than 4:01",
     0.0, 1.0, 1.0, 0),
    ("Clock", "show_date", "Show Date",
     "Display the date below the time", 0.0, 1.0, 1.0, 0),
    ("Clock", "clock_size", "Clock Size",
     "Scale factor for the clock display", 0.5, 3.0, 0.1, 1),
    ("Clock", "position_y", "Vertical Position",
     "Clock position: 0=top, 0.5=center, 1=bottom", 0.0, 1.0, 0.05, 2),
    ("Weather", "show_weather", "Show Weather",
     "Display current weather in the top right corner", 0.0, 1.0, 1.0, 0),
    ("Weather", "weather_opacity", "Weather Opacity",
     "Opacity of the temperature reading", 0.0, 1.0, 0.05, 2),
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
     "Black background",
     ("video/mp4",),
     ("mp4", "m4v")),
)

FILES = {"video-clock-file": ""}
DEFAULT_FILES = dict(FILES)


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
        self._giving_up = False     # nothing left to play; draw black
        self._speed = 1.0           # the rate the pipeline is actually at
        self._speed_refusals = 0

        # Weather
        self.weather_fetcher = None
        self.weather_data = None
        self.last_temp_str = None  # Cache temperature string
        if self.WEATHER_LOCATION.is_valid():
            provider = OpenMeteoProvider()
            self.weather_fetcher = WeatherFetcher(
                provider, self.WEATHER_LOCATION, self._on_weather_update
            )

        # Cached Pango objects to avoid recreating every frame
        self._time_layout = None
        self._date_layout = None
        self._weather_layout = None
        self._cached_time_font_size = None
        self._cached_date_font_size = None

        # A window torn down by GTK itself never reaches SaverWindow.destroy,
        # so the pipeline gets a second way out. Both are idempotent.
        self.connect("unrealize", lambda *_: self.teardown())

        # Start video pipeline with initial settings
        self._setup_video()
        self._apply_initial_speed()

    # -- video ------------------------------------------------------------

    def _wanted_path(self):
        """The file that should be playing, or None for no video at all.

        Deliberately never touches the disk: this runs once a frame, and the
        stored path is usually a document-portal one, where a stat is a round
        trip through FUSE. Whether the file actually opens is the pipeline's
        business, and _on_error's.
        """
        if self._setting and self._setting != self._rejected:
            return self._setting
        return None

    def _setup_video(self):
        """Build and start the pipeline, if there is a video to play.

        Nothing chosen is an ordinary state rather than a failure - the app
        ships no video of its own - and it draws as a black backdrop for the
        clock. There is nothing to retry in that case, so say so and stop.
        """
        video_path = self._wanted_path()
        if not video_path:
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
            # sync=true is what paces the video: without it the sink renders
            # frames as fast as they decode, so playback runs at whatever speed
            # the machine happens to manage rather than the speed it was shot
            # at. The shallow queue is the part that keeps latency down - a draw
            # that falls behind drops stale frames instead of backing the
            # decoder up.
            'appsink name=sink emit-signals=true sync=true max-buffers=1 drop=true'
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

                # A fresh pipeline always starts at rate 1; _apply_initial_speed
                # seeks it to the wanted speed as soon as the preroll allows.
                self._speed = 1.0
                self._speed_refusals = 0

                ret = self.pipeline.set_state(Gst.State.PLAYING)
                if ret == Gst.StateChangeReturn.FAILURE:
                    # A file that has gone since it was chosen fails here rather
                    # than on the bus: filesrc cannot open it, so the state
                    # change never gets far enough to report an error message.
                    print(f"Video Clock: cannot play {video_path}")
                    self._rejected = video_path
                    self._giving_up = True
                    self._teardown_pipeline()
                else:
                    self._path = video_path
                    print(f"Video Clock: Video pipeline started for {video_path}")

        except Exception as e:
            print(f"Video Clock: failed to setup video: {e}")
            self._giving_up = True
            self._teardown_pipeline()

    def _apply_initial_speed(self):
        """Apply playback speed setting once pipeline is ready."""
        if self.pipeline is None:
            return

        speed = min(max(TUNING["playback_speed"], MIN_SPEED), MAX_SPEED)
        if abs(speed - 1.0) < 1e-6:
            return  # Already at default speed

        # Retry a few times as pipeline may not be ready immediately
        attempts = [0]  # Use list for closure
        def try_apply():
            if self._apply_speed(speed):
                self._speed = speed
                return GLib.SOURCE_REMOVE
            attempts[0] += 1
            if attempts[0] > 10:
                return GLib.SOURCE_REMOVE
            return GLib.SOURCE_CONTINUE

        GLib.timeout_add(100, try_apply)

    def teardown(self):
        """Let go of everything GTK knows nothing about. Idempotent.

        Called when the run ends, so the weather polling stops here too - unlike
        _teardown_pipeline, which a video failure uses and which has no business
        taking the weather overlay down with it.
        """
        if self.weather_fetcher is not None:
            self.weather_fetcher.stop()
            self.weather_fetcher = None
        self._teardown_pipeline()

    def _teardown_pipeline(self):
        """Stop the pipeline and let go of it. Idempotent.

        Order matters. The bus watch is a source on the main context that keeps
        the pipeline alive, and the sink is still emitting into a callback bound
        to this widget; both have to be cut before the state change, or a frame
        arrives halfway through the shutdown.
        """
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

        # Set to NULL asynchronously to avoid blocking
        pipeline.set_state(Gst.State.NULL)

    def _restart_video(self):
        self._teardown_pipeline()
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

        The file the user chose is the only candidate - moved, deleted, or in a
        codec this machine cannot decode - so there is nothing to fall back to
        but the black backdrop. Retrying it would only produce the same error
        every frame, so the file is marked rejected and the pipeline let go.
        """
        err, debug = msg.parse_error()
        print(f"Video Clock: GStreamer error: {err}, {debug}")

        if self._path is not None:
            self._rejected = self._path
        self._giving_up = True

        # Not from inside the bus dispatch that is running right now: taking
        # the bus watch down under its own handler is asking for it.
        GLib.idle_add(self._teardown_pipeline_idle)
        return True

    def _teardown_pipeline_idle(self):
        self._teardown_pipeline()
        return GLib.SOURCE_REMOVE

    # -- weather ----------------------------------------------------------

    def _on_weather_update(self, data):
        """Called when weather data arrives or refreshes."""
        self.weather_data = data
        self.last_temp_str = None  # clear cached temp string

    # -- animation --------------------------------------------------------

    def advance(self, dt):
        """Always redraw for smooth video playback."""
        # Only regenerate time string when it actually changes
        current_time = time.localtime()
        show_seconds = TUNING["show_seconds"] > 0.5
        use_12hr = TUNING["hour_format"] > 0.5
        pad_hour = TUNING["pad_hour"] > 0.5

        # Create time key based on what components are displayed. The settings
        # belong in it as much as the clock does: a switch flipped mid-run has
        # to produce a new string, and only the key decides whether one is built.
        if show_seconds:
            time_key = (current_time.tm_hour, current_time.tm_min, current_time.tm_sec,
                        use_12hr, pad_hour)
        else:
            time_key = (current_time.tm_hour, current_time.tm_min, use_12hr, pad_hour)

        if not hasattr(self, '_last_time_key') or self._last_time_key != time_key:
            self._last_time_key = time_key

            # %-I and %-H are the glibc way of asking for an unpadded number.
            # Assembled rather than spelled out as four literals, which is what
            # a third switch would turn into.
            hour = ("%I" if use_12hr else "%H") if pad_hour else \
                   ("%-I" if use_12hr else "%-H")
            fmt = f"{hour}:%M:%S" if show_seconds else f"{hour}:%M"
            if use_12hr:
                fmt += " %p"

            self.last_time_str = time.strftime(fmt, current_time)

        # Date string - only regenerate when day changes
        show_date = TUNING["show_date"] > 0.5
        if show_date:
            date_key = (current_time.tm_year, current_time.tm_mon, current_time.tm_mday)
            if not hasattr(self, '_last_date_key') or self._last_date_key != date_key:
                self._last_date_key = date_key
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
            # No video: none chosen, or the chosen one would not play. Black
            # rather than something decorative - this is also the first frame or
            # two of every run, before the pipeline has prerolled, and anything
            # brighter would flash.
            cr.set_source_rgb(0, 0, 0)
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

            # Reuse buffer if same size, otherwise allocate new one
            buffer_size = len(map_info.data)
            if not hasattr(self, '_video_buffer') or len(self._video_buffer) != buffer_size:
                self._video_buffer = bytearray(buffer_size)

            # Fast copy using buffer protocol
            self._video_buffer[:] = map_info.data

            surface = cairo.ImageSurface.create_for_data(
                self._video_buffer,
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
            # GOOD rather than the default: the frame is being scaled to cover
            # the screen every draw, and BEST costs more than it shows.
            cr.get_source().set_filter(cairo.Filter.GOOD)

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

        # Cache TUNING lookups used multiple times
        clock_scale = TUNING["clock_size"]
        pos_y = TUNING["position_y"]
        bg_opacity = TUNING["background_opacity"]
        glow = TUNING["glow_intensity"]
        text_opacity = TUNING["text_opacity"]
        show_date = TUNING["show_date"] > 0.5

        # Create or reuse Pango layout for time
        font_size = int(120 * clock_scale)
        if self._time_layout is None or self._cached_time_font_size != font_size:
            self._time_layout = PangoCairo.create_layout(cr)
            font_desc = Pango.FontDescription(f"Comfortaa Bold {font_size}")
            self._time_layout.set_font_description(font_desc)
            self._cached_time_font_size = font_size
            self._rendered_time_str = None  # Force text update on layout recreate

        time_layout = self._time_layout

        # Only update text if it changed
        if self.last_time_str != getattr(self, '_rendered_time_str', None):
            self._rendered_time_str = self.last_time_str
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
            date_font_size = int(40 * clock_scale)
            if self._date_layout is None or self._cached_date_font_size != date_font_size:
                self._date_layout = PangoCairo.create_layout(cr)
                date_font_desc = Pango.FontDescription(f"Comfortaa {date_font_size}")
                self._date_layout.set_font_description(date_font_desc)
                self._cached_date_font_size = date_font_size
                self._rendered_date_str = None  # Force text update on layout recreate

            date_layout = self._date_layout

            # Only update text if it changed
            if self.last_date_str != getattr(self, '_rendered_date_str', None):
                self._rendered_date_str = self.last_date_str
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
        """Draw temperature in the top-right corner."""
        if not self.weather_data or self.weather_data.temperature is None:
            return

        # Cache weather opacity
        weather_opacity = TUNING["weather_opacity"]

        # Cache temperature string (only regenerate when temp changes)
        temp_c = self.weather_data.temperature
        temp_str = f"{round(temp_c)}°"
        if not hasattr(self, 'last_temp_str'):
            self.last_temp_str = None
        if temp_str != self.last_temp_str:
            self.last_temp_str = temp_str

        # Create or reuse layout for temperature text
        if self._weather_layout is None:
            self._weather_layout = PangoCairo.create_layout(cr)
            font_size = 32
            font_desc = Pango.FontDescription(f"Comfortaa Bold {font_size}")
            self._weather_layout.set_font_description(font_desc)
            self._rendered_temp_str = None  # Force text update on layout recreate

        temp_layout = self._weather_layout

        # Only update text if it changed
        if self.last_temp_str != getattr(self, '_rendered_temp_str', None):
            self._rendered_temp_str = self.last_temp_str
            temp_layout.set_text(self.last_temp_str, -1)

        # Get dimensions
        temp_ink, temp_logical = temp_layout.get_pixel_extents()
        temp_width = temp_logical.width
        temp_height = temp_logical.height

        # Positioning
        padding = 24

        x = width - temp_width - padding
        y = padding

        # Draw temperature
        cr.save()
        cr.set_source_rgba(0.95, 0.97, 1.0, weather_opacity)
        cr.move_to(x, y)
        PangoCairo.show_layout(cr, temp_layout)
        cr.restore()
