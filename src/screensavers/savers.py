import gi
gi.require_version('Gtk', '4.0')
gi.require_version('Pango', '1.0')
gi.require_version('PangoCairo', '1.0')
from gi.repository import Gtk, Gdk, GLib, Pango, PangoCairo
import cairo
import math
import random

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


class DVDLogoSaver(_AnimatedSaver):
    SPEED = 190.0  # pixels per second on each axis
    LABEL = "DVD"
    FONT_SIZE = 48

    def __init__(self):
        super().__init__()
        self.x = 100.0
        self.y = 100.0
        self.dx = self.SPEED
        self.dy = self.SPEED
        self.width = 200
        self.height = 100
        self.color = (1, 0, 0)

    def advance(self, dt):
        width = self.get_width()
        height = self.get_height()

        if width <= 0 or height <= 0:
            return False

        self.x += self.dx * dt
        self.y += self.dy * dt

        # Clamp on contact as well as reversing: a frame long enough to
        # overshoot the edge would otherwise flip the direction again on the
        # next frame and leave the logo juddering against the border.
        hit = False
        if self.x <= 0:
            self.x, self.dx, hit = 0.0, abs(self.dx), True
        elif self.x + self.width >= width:
            self.x, self.dx, hit = float(width - self.width), -abs(self.dx), True

        if self.y <= 0:
            self.y, self.dy, hit = 0.0, abs(self.dy), True
        elif self.y + self.height >= height:
            self.y, self.dy, hit = float(height - self.height), -abs(self.dy), True

        if hit:
            self.color = (random.random(), random.random(), random.random())

        return True

    def on_draw(self, area, cr, width, height):
        # Black background
        cr.set_source_rgb(0, 0, 0)
        cr.paint()

        # Draw DVD text box
        cr.set_source_rgb(*self.color)
        cr.rectangle(self.x, self.y, self.width, self.height)
        cr.fill()
        
        cr.set_source_rgb(0, 0, 0)
        cr.select_font_face("Sans", 0, 1)
        cr.set_font_size(self.FONT_SIZE)
        # Centre on the glyphs' own ink box. Bearings are what make this exact:
        # they carry the offset from the text origin to the top-left of the ink,
        # which is not the origin the font's line metrics would suggest.
        bearing_x, bearing_y, ink_w, ink_h = cr.text_extents(self.LABEL)[:4]
        cr.move_to(self.x + (self.width - ink_w) / 2.0 - bearing_x,
                   self.y + (self.height - ink_h) / 2.0 - bearing_y)
        cr.show_text(self.LABEL)

class MatrixSaver(_AnimatedSaver):
    """Matrix rain.

    Characters sit in a fixed grid and never move; what travels down a column is
    the highlight - a near-white head trailing a green tail that fades out behind
    it. Drops have their own length, speed and brightness, and glyphs flicker in
    place, which is what the effect actually looks like on screen.

    Every glyph is rasterised once into an A8 mask at start-up, so a frame is a
    few hundred alpha blits grouped by colour rather than a few hundred text
    layouts. Motion is grid-stepped, so the widget only redraws on a step
    (~25/sec) instead of on every frame the compositor asks for.
    """

    TICK_MS = 40             # animation step; also the effective frame rate
    MIN_LEN = 8
    MAX_LEN = 30
    SHADES = 14              # gradient steps down a tail
    DENSITY = 0.72           # share of columns raining at any one time
    GLITCH_RATE = 0.005      # share of grid cells that mutate per step
    HEAD_RGB = (0.82, 1.0, 0.86)
    BODY_RGB = (0.10, 1.0, 0.34)
    TIERS = (1.0, 0.76, 0.52)   # per-drop brightness, picked at spawn
    TIER_WEIGHTS = (6, 3, 2)

    # Halfwidth katakana - the shapes the film's rain is built from - plus the
    # digits and latin caps that are mixed in with them. Anything the font stack
    # can't render is dropped when the atlas is built.
    CHARS = ("".join(chr(c) for c in range(0xFF66, 0xFF9E))
             + "0123456789"
             + "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
             + ":=*+<>|/\\¢¥")

    def __init__(self):
        super().__init__()

        self._size = (0, 0)
        self._scale = 0
        self._atlas_key = None
        self._glyphs = []
        self._glows = []
        self._pool = []
        self._cell_w = 1
        self._cell_h = 1
        self._cols = 0
        self._rows = 0
        self._chars = []
        self._drops = []
        self._active = []     # newest drop per column, for spacing
        self._gap = []        # empty rows to leave before reusing a column
        self._colors = []
        self._tables = []     # (tier, length) -> colour bucket per tail depth
        self._buckets = []
        self._accum = 0.0

    # -- setup ------------------------------------------------------------

    def _build_atlas(self, font_px, scale):
        """Rasterise each character into a uniform cell as an alpha mask.

        Returns the sharp masks, a matching set of quarter-resolution masks used
        as a cheap bloom (Cairo's bilinear filter blurs them on the way back up),
        a weighted pool of glyph indices, and the cell size.
        """
        fontmap = PangoCairo.FontMap.get_default()
        layout = Pango.Layout(fontmap.create_context())
        desc = Pango.FontDescription("Monospace")
        desc.set_absolute_size(font_px * Pango.SCALE)
        layout.set_font_description(desc)

        # Latin digits set the grid; fallback fonts for katakana come with their
        # own metrics and get scaled into the same cell below.
        layout.set_text("0", -1)
        cw, ch = layout.get_pixel_size()
        cell_w = max(2, cw)
        cell_h = max(2, int(ch * 1.06))

        glyphs = []
        glows = []
        pool = []
        for char in self.CHARS:
            layout.set_text(char, -1)
            if layout.get_unknown_glyphs_count() > 0:
                continue
            ink, _logical = layout.get_pixel_extents()
            if ink.width <= 0 or ink.height <= 0:
                continue

            code = ord(char)
            kana = 0xFF66 <= code <= 0xFF9D
            # Fit the ink box into the cell and centre it, so mismatched
            # fallback metrics still line up on the grid.
            fit = min(1.0, cell_w * 0.94 / ink.width, cell_h * 0.86 / ink.height)

            pair = []
            for res in (scale, scale / 4.0):
                surface = cairo.ImageSurface(
                    cairo.FORMAT_A8,
                    max(1, math.ceil(cell_w * res)),
                    max(1, math.ceil(cell_h * res)))
                surface.set_device_scale(res, res)
                cr = cairo.Context(surface)
                cr.translate((cell_w - ink.width * fit) / 2.0,
                             (cell_h - ink.height * fit) / 2.0)
                if kana:
                    # The film's glyphs are mirrored katakana.
                    cr.translate(ink.width * fit, 0)
                    cr.scale(-fit, fit)
                else:
                    cr.scale(fit, fit)
                cr.move_to(-ink.x, -ink.y)
                cr.set_source_rgba(1, 1, 1, 1)
                PangoCairo.show_layout(cr, layout)
                surface.flush()
                pair.append(surface)

            # Weight the pool so the rain reads as katakana with digits mixed
            # through it, rather than as scrolling ASCII.
            pool.extend([len(glyphs)] * (5 if kana else 2 if char.isdigit() else 1))
            glyphs.append(pair[0])
            glows.append(pair[1])

        return glyphs, glows, pool, cell_w, cell_h

    def _build_colors(self):
        """One colour per (brightness tier, tail depth), flattened into buckets.

        Bucket layout per tier: index 0 is the head, 1..SHADES the tail.
        """
        colors = []
        for tier in self.TIERS:
            colors.append(tuple(min(1.0, c * tier) for c in self.HEAD_RGB))
            for shade in range(self.SHADES):
                # Falls off faster than linear so tails end in near-black
                # instead of a flat grey band.
                fade = (1.0 - shade / (self.SHADES - 1)) ** 1.45
                colors.append(tuple(c * fade * tier for c in self.BODY_RGB))

        # Bucket index per tail depth, precomputed for every drop length.
        span = self.SHADES + 1
        tables = []
        for t in range(len(self.TIERS)):
            base = t * span
            per_len = [None] * (self.MAX_LEN + 1)
            for length in range(self.MIN_LEN, self.MAX_LEN + 1):
                row = [base]
                for depth in range(1, length):
                    shade = (depth - 1) * self.SHADES // max(1, length - 1)
                    row.append(base + 1 + min(self.SHADES - 1, shade))
                per_len[length] = tuple(row)
            tables.append(per_len)

        return colors, tables

    def _configure(self, width, height, scale):
        font_px = max(13, min(30, round(height / 46.0)))
        if (font_px, scale) != self._atlas_key:
            (self._glyphs, self._glows, self._pool,
             self._cell_w, self._cell_h) = self._build_atlas(font_px, scale)
            self._atlas_key = (font_px, scale)
        if not self._glyphs:  # no usable font; nothing to draw
            self._cols = self._rows = 0
            self._size = (width, height)
            self._scale = scale
            return

        self._colors, self._tables = self._build_colors()
        self._buckets = [[] for _ in self._colors]

        self._cols = max(1, int(width / self._cell_w) + 1)
        self._rows = max(1, int(height / self._cell_h) + 1)
        self._size = (width, height)
        self._scale = scale

        pool = self._pool
        n = len(pool)
        randrange = random.randrange
        self._chars = [[pool[randrange(n)] for _ in range(self._rows)]
                       for _ in range(self._cols)]

        # Seed with drops already in flight, otherwise the saver opens on a
        # single wall of heads marching down from the top edge.
        self._drops = []
        self._active = [None] * self._cols
        self._gap = [0] * self._cols
        for col in range(self._cols):
            if random.random() < self.DENSITY:
                self._spawn(col, random.uniform(0, self._rows + self.MAX_LEN))

    # -- simulation -------------------------------------------------------

    def _spawn(self, col, y):
        drop = [col,                                    # column
                y,                                      # head row (float)
                random.uniform(0.30, 0.95),             # rows per step
                random.randint(self.MIN_LEN, self.MAX_LEN),
                random.choices(range(len(self.TIERS)), self.TIER_WEIGHTS)[0]]
        self._drops.append(drop)
        self._active[col] = drop
        self._gap[col] = random.randint(2, 18)

    def advance(self, dt):
        """Accumulate dt and, when a step is due, move the rain down one row.

        Motion is grid-stepped, so frames between steps would render identically
        and are skipped - this is what holds the saver to ~25 redraws a second
        no matter how fast the display runs.
        """
        step = self.TICK_MS / 1000.0
        self._accum += dt
        if self._accum < step:
            return False
        self._accum -= step
        if self._accum > step:  # resync rather than replay a backlog
            self._accum = 0.0

        rows = self._rows
        if not rows:
            return False

        drops = self._drops
        chars = self._chars
        pool = self._pool
        n_pool = len(pool)
        randrange = random.randrange

        alive = []
        for drop in drops:
            drop[1] += drop[2]
            head = int(drop[1])
            if head - drop[3] < rows:
                alive.append(drop)
            # The leading character re-rolls every step - that flicker is most
            # of what sells the effect.
            if 0 <= head < rows:
                chars[drop[0]][head] = pool[randrange(n_pool)]
        self._drops = alive

        # Scattered mutations so settled tails keep shimmering.
        for _ in range(int(self._cols * rows * self.GLITCH_RATE)):
            chars[randrange(self._cols)][randrange(rows)] = pool[randrange(n_pool)]

        # Top the rain back up, a few columns per step so heads don't line up.
        wanted = int(self._cols * self.DENSITY)
        budget = max(1, self._cols // 20)
        attempts = 4 * budget
        while len(self._drops) < wanted and budget and attempts:
            attempts -= 1
            col = randrange(self._cols)
            last = self._active[col]
            if last is None or last[1] - last[3] >= self._gap[col]:
                self._spawn(col, 0.0)
                budget -= 1

        return True

    # -- drawing ----------------------------------------------------------

    def on_draw(self, area, cr, width, height):
        if width <= 0 or height <= 0:
            return

        scale = max(1, self.get_scale_factor())
        if (width, height) != self._size or scale != self._scale:
            self._configure(width, height, scale)

        cr.set_source_rgb(0, 0, 0)
        cr.paint()

        if not self._cols:
            return

        # Group the frame's glyphs by colour so the source is set a few dozen
        # times per frame instead of once per glyph.
        buckets = self._buckets
        for bucket in buckets:
            del bucket[:]

        rows = self._rows
        cell_w = self._cell_w
        cell_h = self._cell_h
        chars = self._chars
        tables = self._tables

        for col, y, _speed, length, tier in self._drops:
            head = int(y)
            x = col * cell_w
            col_chars = chars[col]
            table = tables[tier][length]
            for depth in range(length):
                row = head - depth
                if row < 0:
                    break
                if row < rows:
                    buckets[table[depth]].append((x, row * cell_h, col_chars[row]))

        glyphs = self._glyphs
        colors = self._colors
        mask = cr.mask_surface

        # Bloom under the heads first, so the sharp glyphs land on top of it.
        glows = self._glows
        span = self.SHADES + 1
        for tier in range(len(self.TIERS)):
            bucket = buckets[tier * span]
            if not bucket:
                continue
            r, g, b = colors[tier * span]
            cr.set_source_rgba(r, g, b, 0.45)
            for gx, gy, char in bucket:
                mask(glows[char], gx, gy)

        for i, bucket in enumerate(buckets):
            if not bucket:
                continue
            cr.set_source_rgb(*colors[i])
            for gx, gy, char in bucket:
                mask(glyphs[char], gx, gy)

class ColorPulseSaver(_AnimatedSaver):
    RATE = 1.25         # radians per second
    MIN_REDRAW = 1 / 60  # seconds between repaints

    def __init__(self):
        super().__init__()
        self.time = 0.0
        # The colour is held at 8-bit precision, which is all the display can
        # show. Every frame is a full-screen fill - 33MB of writes at 4K - and
        # the pulse is far too slow to be worth that at a fast panel's refresh
        # rate, so repaints are capped and identical colours are skipped.
        self.rgb = (0, 0, 0)
        self._accum = 0.0

    def advance(self, dt):
        self.time += dt * self.RATE
        self._accum += dt
        if self._accum < self.MIN_REDRAW:
            return False
        self._accum = 0.0

        rgb = tuple(int(((math.sin(self.time + phase) + 1) / 2) * 255)
                    for phase in (0, 2, 4))
        if rgb == self.rgb:
            return False
        self.rgb = rgb
        return True

    def on_draw(self, area, cr, width, height):
        if width <= 0 or height <= 0:
            return

        r, g, b = self.rgb
        cr.set_source_rgb(r / 255.0, g / 255.0, b / 255.0)
        cr.paint()
