import cairo
import math
import random

from screensavers.base import _AnimatedSaver

# The scene is a port of the DOS-era GORILLA.BAS, which was drawn for a 640x350
# EGA screen. Everything below - sprite art, building widths, window sizes, the
# ballistics constants - is sized for that, so the simulation keeps a 350-row
# logical space and the finished frame is scaled up to the monitor with a
# nearest-neighbour filter. Redrawing the scene at native resolution instead
# would leave the sprites as the only pixellated thing on screen.
LOGICAL_H = 350
GROUND_Y = 335          # rooftops are measured up from here
WIND_Y = 345            # the wind gauge sits in the strip below the city

# Width follows the display's aspect so the skyline spans it without
# letterboxing. The bounds only come into play on unusually shaped screens,
# where the scene is centred instead.
MIN_LOGICAL_W = 320
MAX_LOGICAL_W = 1600


def _rgb(r, g, b):
    return (r / 255.0, g / 255.0, b / 255.0)


SKY = _rgb(0, 0, 173)
GORILLA_COLOR = _rgb(255, 170, 82)
BANANA_COLOR = _rgb(255, 255, 82)
EXPLOSION_COLOR = _rgb(255, 0, 0)
SUN_COLOR = _rgb(255, 255, 0)
LIGHT_WINDOW = _rgb(255, 255, 82)
DARK_WINDOW = _rgb(82, 85, 82)
BUILDING_COLORS = (_rgb(173, 170, 173), _rgb(0, 170, 173), _rgb(173, 0, 0))

# How finely the arc is sampled. The reference walked its trajectory in steps of
# 0.35 and the throw speed was whatever one step per frame came out at, which
# tied the pace of the game to the refresh rate and made the banana jump. Here
# the speed is set directly and the step follows from it, so the arc is sampled
# about sixty times a second however fast a throw is - fine enough that the
# banana flies rather than teleports, and identical on any display. Not offered
# as a setting: it is what keeps motion smooth at whatever the others are set to.
SUBSTEPS_PER_SEC = 60.0
MAX_T_STEP = 0.35

MIN_REDRAW = 1 / 60.0   # a dropped frame mid-throw shows up as a stutter

DEFAULT_TUNING = {
    # throw
    "throw_speed": 3.5,     # t units per second
    "tumble_time": 0.14,    # seconds per quarter turn
    "gravity": 9.8,
    "min_rise": 45.0,       # a throw is always a lob, never a flat line drive
    "top_margin": 16.0,     # closest the top of an arc comes to the top edge
    "wind_max": 15.0,
    # timing
    "aim_pause": 0.35,      # arm held up before the throw
    "shot_pause": 0.55,     # beat between shots
    "victory_wave": 0.16,   # per raised arm
    "victory_waves": 6.0,
    "victory_hold": 0.7,
    "sun_shock": 0.6,
    # damage
    "blast_size": float(LOGICAL_H // 50),
    "blast_time": 0.18,
    "gorilla_blast": 30.0,
    "gorilla_blast_time": 0.5,
    # match
    "start_error": 200.0,   # the first shot of a round is wild
    "error_decay": 35.0,    # and each one after it closes in
    "max_turns": 20.0,      # a round that will not resolve gets a fresh city
}

# Live values. Read on use rather than captured at start-up, so an adjustment
# reaches a saver that is already running.
TUNING = dict(DEFAULT_TUNING)

# What the preferences window offers, as
# (group, key, title, subtitle, lower, upper, step, digits).
TUNABLES = (
    ("Throw", "throw_speed", "Throw Speed",
     "How quickly a banana travels its arc", 0.5, 20.0, 0.5, 1),
    ("Throw", "tumble_time", "Tumble Speed",
     "Seconds per quarter turn of the banana", 0.03, 0.60, 0.01, 2),
    ("Throw", "min_rise", "Minimum Arc Height",
     "How far a banana climbs even on the flattest throw", 5.0, 250.0, 5.0, 0),
    ("Throw", "top_margin", "Arc Headroom",
     "Gap left between the top of an arc and the top of the screen",
     0.0, 250.0, 4.0, 0),
    ("Throw", "gravity", "Gravity",
     "How hard a banana falls once it is past the top of its arc",
     1.0, 30.0, 0.5, 1),
    ("Throw", "wind_max", "Wind Strength",
     "Strongest crosswind a round can be dealt", 0.0, 40.0, 1.0, 0),

    ("Timing", "aim_pause", "Aim Pause",
     "How long a gorilla holds its arm up before throwing", 0.10, 3.0, 0.05, 2),
    ("Timing", "shot_pause", "Pause Between Shots",
     "Beat after a banana lands, before the next throw", 0.10, 4.0, 0.05, 2),
    ("Timing", "victory_wave", "Victory Wave",
     "Seconds per raised arm in the winner's celebration", 0.05, 1.0, 0.01, 2),
    ("Timing", "victory_waves", "Victory Waves",
     "How many arms go up before the next round", 1.0, 20.0, 1.0, 0),
    ("Timing", "victory_hold", "Victory Hold",
     "Pause on the winner before a new city is built", 0.0, 5.0, 0.1, 1),
    ("Timing", "sun_shock", "Sun Reaction",
     "How long the sun stays startled after a near miss", 0.0, 3.0, 0.1, 1),

    ("Damage", "blast_size", "Blast Radius",
     "How much of a building a banana takes out", 1.0, 40.0, 1.0, 0),
    ("Damage", "blast_time", "Blast Duration",
     "How long a building explosion is on screen", 0.05, 2.0, 0.05, 2),
    ("Damage", "gorilla_blast", "Knockout Radius",
     "Size of the explosion when a gorilla is hit", 5.0, 90.0, 5.0, 0),
    ("Damage", "gorilla_blast_time", "Knockout Duration",
     "How long a knockout explosion is on screen", 0.05, 3.0, 0.05, 2),

    ("Match", "start_error", "Opening Inaccuracy",
     "How wild the first throw of a round is", 0.0, 500.0, 10.0, 0),
    ("Match", "error_decay", "Zeroing In",
     "How much accuracy is gained with each throw", 0.0, 200.0, 5.0, 0),
    ("Match", "max_turns", "Throws Per Round",
     "Throws allowed before a fresh city is built anyway", 2.0, 80.0, 1.0, 0),
)


def t_step():
    """Trajectory step for the current throw speed."""
    return min(MAX_T_STEP, TUNING["throw_speed"] / SUBSTEPS_PER_SEC)

AIM, FLIGHT, EXPLODE, VICTORY, SETTLE = range(5)

GOR_DOWN_ASCII = """
          XXXXXXXX
          XXXXXXXX
         XX      XX
         XXXXXXXXXX
         XXX  X  XX
          XXXXXXXX
          XXXXXXXX
           XXXXXX
      XXXXXXXXXXXXXXXX
   XXXXXXXXXXXXXXXXXXXXXX
  XXXXXXXXXXXX XXXXXXXXXXX
 XXXXXXXXXXXXX XXXXXXXXXXXX
 XXXXXXXXXXXX X XXXXXXXXXXX
XXXXX XXXXXX XXX XXXXX XXXXX
XXXXX XXX   XXXXX   XX XXXXX
XXXXX   XXXXXXXXXXXX   XXXXX
 XXXXX  XXXXXXXXXXXX  XXXXX
 XXXXX  XXXXXXXXXXXX  XXXXX
  XXXXX XXXXXXXXXXXX XXXXX
   XXXXXXXXXXXXXXXXXXXXXX
       XXXXXXXXXXXXX
     XXXXXX     XXXXXX
     XXXXX       XXXXX
    XXXXX         XXXXX
    XXXXX         XXXXX
    XXXXX         XXXXX
    XXXXX         XXXXX
    XXXXX         XXXXX
     XXXXX       XXXXX
"""
GOR_LEFT_ASCII = """
   XXXXX
  XXXXX   XXXXXXXX
 XXXXX    XXXXXXXX
 XXXXX   XX      XX
XXXXX    XXXXXXXXXX
XXXXX    XXX  X  XX
XXXXX     XXXXXXXX
 XXXXX    XXXXXXXX
 XXXXX     XXXXXX
  XXXXXXXXXXXXXXXXXXXX
   XXXXXXXXXXXXXXXXXXXXXX
      XXXXXXXX XXXXXXXXXXX
      XXXXXXXX XXXXXXXXXXXX
      XXXXXXX X XXXXXXXXXXX
      XXXXXX XXX XXXXX XXXXX
      XXX   XXXXX   XX XXXXX
        XXXXXXXXXXXX   XXXXX
        XXXXXXXXXXXX  XXXXX
        XXXXXXXXXXXX  XXXXX
        XXXXXXXXXXXX XXXXX
       XXXXXXXXXXXXXXXXXX
       XXXXXXXXXXXXX
     XXXXXX     XXXXXX
     XXXXX       XXXXX
    XXXXX         XXXXX
    XXXXX         XXXXX
    XXXXX         XXXXX
    XXXXX         XXXXX
    XXXXX         XXXXX
     XXXXX       XXXXX
"""
GOR_RIGHT_ASCII = """
                    XXXXX
          XXXXXXXX   XXXXX
          XXXXXXXX    XXXXX
         XX      XX   XXXXX
         XXXXXXXXXX    XXXXX
         XXX  X  XX    XXXXX
          XXXXXXXX     XXXXX
          XXXXXXXX    XXXXX
           XXXXXX     XXXXX
      XXXXXXXXXXXXXXXXXXXX
   XXXXXXXXXXXXXXXXXXXXXX
  XXXXXXXXXXXX XXXXXXX
 XXXXXXXXXXXXX XXXXXXX
 XXXXXXXXXXXX X XXXXXX
XXXXX XXXXXX XXX XXXXX
XXXXX XXX   XXXXX   XX
XXXXX   XXXXXXXXXXXX
 XXXXX  XXXXXXXXXXXX
 XXXXX  XXXXXXXXXXXX
  XXXXX XXXXXXXXXXXX
   XXXXXXXXXXXXXXXXX
       XXXXXXXXXXXXX
     XXXXXX     XXXXXX
     XXXXX       XXXXX
    XXXXX         XXXXX
    XXXXX         XXXXX
    XXXXX         XXXXX
    XXXXX         XXXXX
    XXXXX         XXXXX
     XXXXX       XXXXX
"""
BAN_RIGHT_ASCII = "\n     XX\n    XXX\n   XXX\n   XXX\n   XXX\n   XXX\n   XXX\n    XXX\n     XX\n"
BAN_LEFT_ASCII = "\nXX\nXXX\n XXX\n XXX\n XXX\n XXX\n XXX\nXXX\nXX\n"
BAN_UP_ASCII = "\nXX     XX\nXXXXXXXXX\n XXXXXXX\n  XXXXX\n"
BAN_DOWN_ASCII = "\n  XXXXX\n XXXXXXX\nXXXXXXXXX\nXX     XX\n"
SUN_NORMAL_ASCII = """
                    X
                    X
            X       X       X
             X      X      X
             X      X      X
     X        X     X     X        X
      X        X XXXXXXX X        X
       XX      XXXXXXXXXXX      XX
         X  XXXXXXXXXXXXXXXXX  X
          XXXXXXXXXXXXXXXXXXXXX
  X       XXXXXXXXXXXXXXXXXXXXX       X
   XXXX  XXXXXXXXXXXXXXXXXXXXXXX  XXXX
       XXXXXXXXXX XXXXX XXXXXXXXXX
        XXXXXXXX   XXX   XXXXXXXX
        XXXXXXXXX XXXXX XXXXXXXXX
XXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXX
        XXXXXXXXXXXXXXXXXXXXXXXXX
        XXXXXXXXXXXXXXXXXXXXXXXXX
       XXXXXX XXXXXXXXXXXXX XXXXXX
   XXXX  XXXXX  XXXXXXXXX  XXXXX  XXXX
  X       XXXXXX  XXXXX  XXXXXX       X
          XXXXXXXX     XXXXXXXX
         X  XXXXXXXXXXXXXXXXX  X
       XX      XXXXXXXXXXX      XX
      X        X XXXXXXX X        X
     X        X     X     X        X
             X      X      X
             X      X      X
            X       X       X
                    X
                    X
"""
SUN_SHOCKED_ASCII = """
                    X
                    X
            X       X       X
             X      X      X
             X      X      X
     X        X     X     X        X
      X        X XXXXXXX X        X
       XX      XXXXXXXXXXX      XX
         X  XXXXXXXXXXXXXXXXX  X
          XXXXXXXXXXXXXXXXXXXXX
  X       XXXXXXXXXXXXXXXXXXXXX       X
   XXXX  XXXXXXXXXXXXXXXXXXXXXXX  XXXX
       XXXXXXXXXX XXXXX XXXXXXXXXX
        XXXXXXXX   XXX   XXXXXXXX
        XXXXXXXXX XXXXX XXXXXXXXX
XXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXX
        XXXXXXXXXXXXXXXXXXXXXXXXX
        XXXXXXXXXXXXXXXXXXXXXXXXX
       XXXXXXXXXXXXXXXXXXXXXXXXXXX
   XXXX  XXXXXXXXX     XXXXXXXXX  XXXX
  X       XXXXXXX       XXXXXXX       X
          XXXXXXX       XXXXXXX
         X  XXXXXX     XXXXXX  X
       XX      XXXXXXXXXXX      XX
      X        X XXXXXXX X        X
     X        X     X     X        X
             X      X      X
             X      X      X
            X       X       X
                    X
                    X
"""


def _mask(art):
    """Rasterise an ASCII sprite into an alpha mask, one byte per character."""
    lines = art.split('\n')[1:-1]
    width = max(len(line) for line in lines)
    height = len(lines)

    surface = cairo.ImageSurface(cairo.FORMAT_A8, width, height)
    stride = surface.get_stride()
    data = surface.get_data()
    for y, line in enumerate(lines):
        row = y * stride
        for x, char in enumerate(line):
            if char == 'X':
                data[row + x] = 255
    surface.mark_dirty()
    return surface


# Built once at import and shared by every instance: the masks are resolution
# independent and only ever read, so the savers running on each monitor have no
# reason to rasterise their own.
GORILLA_MASKS = (_mask(GOR_DOWN_ASCII), _mask(GOR_LEFT_ASCII), _mask(GOR_RIGHT_ASCII))
BANANA_MASKS = (_mask(BAN_RIGHT_ASCII), _mask(BAN_UP_ASCII),
                _mask(BAN_LEFT_ASCII), _mask(BAN_DOWN_ASCII))
SUN_MASKS = (_mask(SUN_NORMAL_ASCII), _mask(SUN_SHOCKED_ASCII))

ARMS_DOWN, LEFT_ARM_UP, RIGHT_ARM_UP = 0, 1, 2
GOR_W = GORILLA_MASKS[ARMS_DOWN].get_width()
GOR_H = GORILLA_MASKS[ARMS_DOWN].get_height()
SUN_W = SUN_MASKS[0].get_width()
SUN_H = SUN_MASKS[0].get_height()
SUN_Y = 10


def _stamp(cr, mask, x, y, rgb):
    """Draw a sprite mask in the given colour, one logical pixel per byte.

    The context is scaled up by several times over, so the mask has to be
    sampled with a nearest-neighbour filter - the default would smear each
    sprite pixel into a gradient and lose the pixel art the scene is made of.
    """
    cr.save()
    cr.translate(x, y)
    cr.set_source_rgb(*rgb)
    pattern = cairo.SurfacePattern(mask)
    pattern.set_filter(cairo.Filter.NEAREST)
    cr.mask(pattern)
    cr.restore()


def _disc(cr, cx, cy, radius):
    """Fill a circle out of whole logical pixels.

    Cairo would happily draw a smooth arc, but it is drawn into a scaled context
    and would come out with a clean curve at the display's resolution while
    everything around it stays blocky. Emitting the circle as integer scanlines
    keeps it on the scene's own pixel grid.
    """
    cx, cy = int(cx), int(cy)
    r = int(radius)
    for dy in range(-r, r + 1):
        half = int(math.sqrt(max(0.0, radius * radius - dy * dy)))
        if half:
            cr.rectangle(cx - half, cy + dy, half * 2, 1)
    cr.fill()


class GorillasSaver(_AnimatedSaver):
    """Two gorillas taking turns at each other across a procedural skyline.

    A port of GORILLA.BAS, which was a game with a main loop of its own - it
    blocked for the arm-raise, ran a nested loop per shot and slept between
    frames. None of that survives contact with a frame clock, so the match runs
    as a state machine stepped by elapsed time, and every wait became a
    duration.

    Trajectories still advance in fixed steps rather than by elapsed time
    directly. That keeps collision granularity identical whatever the display's
    refresh rate, and stops a banana tunnelling through a thin building between
    two frames on a slow one.
    """

    # Picked up by the preferences window, which offers a settings dialog for
    # any saver that declares these.
    TUNABLES = TUNABLES
    TUNING = TUNING
    DEFAULT_TUNING = DEFAULT_TUNING
    TUNING_KEY = "gorillas-tuning"

    def __init__(self):
        super().__init__()

        self._size = (0, 0)
        self._lw = 0
        self._scale = 1.0
        self._origin = (0.0, 0.0)

        self._backdrop = None
        self._solid = None      # 1 per logical pixel of standing building
        self._gorillas = []
        self._sun_x = 0
        self._wind = 0

        self._state = SETTLE
        self._timer = 0.0
        self._turn = 0
        self._turns = 0
        self._error = TUNING["start_error"]
        self._redraw_accum = 0.0

        self._t = 0.0
        self._accum = 0.0
        self._vx = self._vy = 0.0
        self._bx = self._by = 0.0
        self._ban = None        # trajectory point, the sprite's centre
        self._ban_shape = 1
        self._spin = 0.0
        self._clear_of_thrower = False

        self._blast = None      # (x, y, radius, size, fatal)
        self._sun_shock = 0.0
        self._winner = None

    # -- scene ------------------------------------------------------------

    def _configure(self, width, height):
        lw = max(MIN_LOGICAL_W,
                 min(MAX_LOGICAL_W, int(round(LOGICAL_H * width / float(height)))))
        scale = min(width / float(lw), height / float(LOGICAL_H))

        self._lw = lw
        self._scale = scale
        # Anchored to the bottom rather than centred: on a screen too narrow for
        # the scene to fill vertically the leftover is sky, and sky belongs
        # above the city, not under it.
        self._origin = ((width - lw * scale) / 2.0, height - LOGICAL_H * scale)
        self._size = (width, height)
        self._sun_x = (lw - SUN_W) // 2

        self._new_round()

    def _build_city(self):
        """Draw a skyline, and record where its rooftops are.

        Returns the backdrop, a per-pixel record of what is solid, and the
        rooftop coordinates the gorillas get placed on.
        """
        lw = self._lw
        surface = cairo.ImageSurface(cairo.FORMAT_RGB24, lw, LOGICAL_H)
        cr = cairo.Context(surface)
        cr.set_antialias(cairo.Antialias.NONE)
        cr.set_source_rgb(*SKY)
        cr.paint()

        solid = bytearray(lw * LOGICAL_H)
        roofs = []

        slope = random.choice(['up', 'down', 'v', 'v', 'v', '^'])
        offset = 15 if slope in ('up', 'v') else 130
        x = 2

        while x < lw - 10:
            if slope == 'up':
                offset += 10
            elif slope == 'down':
                offset -= 10
            elif slope == 'v':
                offset += -20 if x > lw / 2 else 20
            else:
                offset += 20 if x > lw / 2 else -20

            w = min(37 + random.randint(0, 37), lw - x - 2)
            h = max(25, random.randint(10, 120) + offset)
            if w < 12:
                break
            h = min(h, GROUND_Y - 20)

            top = GROUND_Y - h
            cr.set_source_rgb(*random.choice(BUILDING_COLORS))
            cr.rectangle(x + 1, top - 1, w - 1, h - 1)
            cr.fill()

            # Filled a row at a time: a per-pixel loop over a dozen buildings is
            # enough Python to show up as a hitch when a round starts.
            left, right = x + 1, min(lw, x + w)
            span = b'\x01' * (right - left)
            for py in range(max(0, top - 1), min(LOGICAL_H, top + h - 2)):
                solid[py * lw + left:py * lw + right] = span

            for wx in range(3, w - 6, 10):
                for wy in range(3, h - 15, 15):
                    lit = DARK_WINDOW if random.randint(1, 4) == 1 else LIGHT_WINDOW
                    cr.set_source_rgb(*lit)
                    cr.rectangle(x + 1 + wx, top + 1 + wy, 4, 7)
                    cr.fill()

            roofs.append((x, top))
            x += w

        return surface, solid, roofs

    def _place_gorillas(self, roofs):
        """Stand a gorilla on a rooftop near each end of the skyline."""
        n = len(roofs)
        left = random.randint(1, max(1, min(2, n - 3)))
        right = random.randint(max(left + 1, n - 3), n - 2)

        placed = []
        for index in (left, right):
            width = roofs[index + 1][0] - roofs[index][0]
            placed.append((roofs[index][0] + width // 2 - GOR_W // 2,
                           roofs[index][1] - GOR_H - 1))
        return placed

    def _new_round(self):
        for _ in range(8):
            backdrop, solid, roofs = self._build_city()
            if len(roofs) >= 4:
                break
        else:
            return  # a screen too narrow to hold a match; leave the last one up

        self._backdrop = backdrop
        self._solid = solid
        self._gorillas = self._place_gorillas(roofs)
        gust = max(0, int(TUNING["wind_max"]))
        self._wind = random.randint(min(5, gust), gust) * random.choice([1, -1])
        self._turn = random.randint(0, 1)
        self._turns = 0
        self._error = TUNING["start_error"]
        self._winner = None
        self._ban = None
        self._blast = None
        self._sun_shock = 0.0
        self._enter(AIM)

    # -- match ------------------------------------------------------------

    def _enter(self, state):
        self._state = state
        self._timer = 0.0

    def _celebration(self):
        """How long the winner spends waving before the next round."""
        return TUNING["victory_wave"] * max(1, int(TUNING["victory_waves"]))

    def _aim(self, target):
        """Pick a throw that lands near the target, allowing for the wind.

        The reference drew its flight time at random and let the height of the
        arc fall out of that. Height is what a lob is read by, though, and at
        the times it picked every throw peaked well above the top of the screen
        - a banana would leave at one roof, vanish for a second or two, and drop
        back in. So the apex is chosen first, somewhere in the visible sky, and
        the flight time is solved for from it.
        """
        tx = target[0] + GOR_W / 2.0 + random.uniform(-self._error, self._error)
        ty = target[1] + GOR_H / 2.0 + random.uniform(-self._error, self._error)

        gravity = TUNING["gravity"]
        min_rise = TUNING["min_rise"]

        # How far the banana climbs above the throwing hand. The headroom is a
        # hard ceiling that the minimum cannot push through, in either order the
        # two are set - a throw leaving the top of the screen is the whole thing
        # being avoided here. Under it, the arc still has to out-climb a target
        # standing higher up, and is never a flat line drive.
        ceiling = max(1.0, self._by - TUNING["top_margin"])
        floor = min(ceiling, max(min_rise, self._by - ty + min_rise))
        rise = random.uniform(floor, ceiling)

        vy = math.sqrt(2.0 * gravity * rise)
        # y(t) = by - vy t + g t^2 / 2, solved for y(T) = ty. The larger root is
        # the one on the way back down; the smaller would have the banana still
        # climbing when it arrives.
        disc = max(0.0, vy * vy - 2.0 * gravity * (self._by - ty))
        flight = (vy + math.sqrt(disc)) / gravity

        vx = (tx - self._bx - 0.5 * (self._wind / 5.0) * flight ** 2) / flight
        return vx, vy

    def _launch(self):
        start = self._gorillas[self._turn]
        target = self._gorillas[1 - self._turn]

        # The banana leaves the hand that went up, which is the one on the
        # gorilla's outside edge - so the throw arcs back over its own head.
        # Set before aiming: the arc is measured from the hand, not the sprite.
        self._bx = start[0] + (GOR_W if self._turn else 0)
        self._by = start[1] - 20

        self._vx, self._vy = self._aim(target)
        self._error = max(0.0, self._error - TUNING["error_decay"])

        self._t = 0.0
        self._accum = 0.0
        self._ban = (self._bx, self._by)
        self._ban_shape = 1
        self._spin = 0.0
        self._clear_of_thrower = False
        self._turns += 1
        self._enter(FLIGHT)

    def _overlaps(self, x, y, w, h, box):
        bx, by = box
        return (x < bx + GOR_W and x + w > bx
                and y < by + GOR_H and y + h > by)

    def _banana_box(self):
        """The banana's sprite and where it sits, as a rect.

        The four sprites are different shapes - two of them lie down - so the
        trajectory point is the banana's centre. Anchoring each by its top-left
        corner instead makes it jerk sideways on every quarter turn.
        """
        mask = BANANA_MASKS[self._ban_shape]
        w, h = mask.get_width(), mask.get_height()
        return mask, self._ban[0] - w / 2.0, self._ban[1] - h / 2.0, w, h

    def _step(self, step):
        """Advance the banana one trajectory step and see what it ran into."""
        self._t += step
        t = self._t
        x = self._bx + self._vx * t + 0.5 * (self._wind / 5.0) * t * t
        y = self._by - self._vy * t + 0.5 * TUNING["gravity"] * t * t
        self._ban = (x, y)

        if x < 0 or x > self._lw or y > LOGICAL_H:
            self._miss()
            return

        _mask, left, top, w, h = self._banana_box()

        if (self._sun_x <= x <= self._sun_x + SUN_W
                and SUN_Y <= y <= SUN_Y + SUN_H):
            self._sun_shock = TUNING["sun_shock"]

        thrower = self._gorillas[self._turn]
        if not self._clear_of_thrower and not self._overlaps(left, top, w, h, thrower):
            self._clear_of_thrower = True

        # Gorillas are drawn over the skyline rather than into it, so they are
        # not in the solid mask and have to be tested in their own right. The
        # reference only checked them once the terrain had already registered a
        # hit, which let a banana pass clean through one standing clear of its
        # roof.
        for index, gorilla in enumerate(self._gorillas):
            if index == self._turn and not self._clear_of_thrower:
                continue
            if self._overlaps(left, top, w, h, gorilla):
                self._explode(x, y, max(1, int(TUNING["gorilla_blast"])),
                              TUNING["gorilla_blast_time"], 1 - index)
                return

        blast = max(1, int(TUNING["blast_size"]))

        lw = self._lw
        solid = self._solid
        for cy in range(max(0, int(top)), min(int(top) + h, LOGICAL_H)):
            row = cy * lw
            for cx in range(max(0, int(left)), min(int(left) + w, lw)):
                if solid[row + cx]:
                    self._explode(x, y, blast, TUNING["blast_time"], None)
                    return

    def _explode(self, x, y, size, duration, winner):
        self._ban = None
        self._winner = winner
        self._blast = (x, y, 1.0, size, duration)
        self._enter(EXPLODE)

    def _carve(self, x, y, radius):
        """Blow the crater out of both the picture and the collision map."""
        cr = cairo.Context(self._backdrop)
        cr.set_antialias(cairo.Antialias.NONE)
        cr.set_source_rgb(*SKY)
        _disc(cr, x, y, radius)

        lw = self._lw
        solid = self._solid
        cx, cy = int(x), int(y)
        r = int(radius)
        limit = radius * radius
        for py in range(max(0, cy - r), min(LOGICAL_H, cy + r + 1)):
            row = py * lw
            dy2 = (py - cy) ** 2
            for px in range(max(0, cx - r), min(lw, cx + r + 1)):
                if (px - cx) ** 2 + dy2 <= limit:
                    solid[row + px] = 0

    def _miss(self):
        self._ban = None
        self._enter(SETTLE)

    # -- simulation -------------------------------------------------------

    def advance(self, dt):
        if not self._lw:
            return False  # nothing is sized until the first draw

        self._timer += dt
        redraw = False

        if self._sun_shock > 0.0:
            self._sun_shock -= dt
            if self._sun_shock <= 0.0:
                redraw = True

        state = self._state
        if state == AIM:
            if self._timer >= TUNING["aim_pause"]:
                self._launch()
                redraw = True

        elif state == FLIGHT:
            # Tumbling runs on its own clock rather than once per trajectory
            # step, which at this sampling rate would be a blur.
            spin = TUNING["tumble_time"]
            self._spin += dt
            if self._spin >= spin:
                self._spin = 0.0
                self._ban_shape = (self._ban_shape + 1) % 4
                redraw = True

            step = t_step()
            self._accum += dt * TUNING["throw_speed"]
            # MAX_DT bounds this to a handful of steps, so a stalled frame
            # cannot leave the banana teleporting across the screen.
            while self._accum >= step and self._state == FLIGHT:
                self._accum -= step
                self._step(step)
                redraw = True

        elif state == EXPLODE:
            x, y, _radius, size, duration = self._blast
            progress = min(1.0, self._timer / duration)
            self._blast = (x, y, max(1.0, size * progress), size, duration)
            redraw = True
            if progress >= 1.0:
                self._carve(x, y, size)
                self._blast = None
                if self._winner is None:
                    self._enter(SETTLE)
                else:
                    self._enter(VICTORY)

        elif state == VICTORY:
            wave = TUNING["victory_wave"]
            if self._timer < self._celebration():
                # Only the frames where the raised arm actually swaps are worth
                # drawing; the rest of the scene is standing still.
                redraw = int(self._timer / wave) != int((self._timer - dt) / wave)
            elif self._timer >= self._celebration() + TUNING["victory_hold"]:
                self._new_round()
                redraw = True

        elif state == SETTLE:
            if self._timer >= TUNING["shot_pause"]:
                self._turn = 1 - self._turn
                if self._turns >= TUNING["max_turns"]:
                    # Two gorillas burrowing into the same building would go on
                    # all night; give them somewhere else to stand.
                    self._new_round()
                else:
                    self._enter(AIM)
                redraw = True

        if not redraw:
            return False

        # Every frame rescales the whole scene to the display, which is not
        # worth doing at a fast panel's refresh rate for an animation this slow.
        self._redraw_accum += dt
        if self._redraw_accum < MIN_REDRAW:
            return False
        self._redraw_accum = 0.0
        return True

    # -- drawing ----------------------------------------------------------

    def on_draw(self, area, cr, width, height):
        if width <= 0 or height <= 0:
            return

        if (width, height) != self._size:
            self._configure(width, height)
        if self._backdrop is None:
            return

        cr.set_antialias(cairo.Antialias.NONE)
        cr.set_source_rgb(*SKY)
        cr.paint()

        cr.translate(*self._origin)
        cr.scale(self._scale, self._scale)

        cr.set_source_surface(self._backdrop, 0, 0)
        cr.get_source().set_filter(cairo.Filter.NEAREST)
        cr.rectangle(0, 0, self._lw, LOGICAL_H)
        cr.fill()

        _stamp(cr, SUN_MASKS[1 if self._sun_shock > 0.0 else 0],
               self._sun_x, SUN_Y, SUN_COLOR)

        for index, gorilla in enumerate(self._gorillas):
            _stamp(cr, GORILLA_MASKS[self._arms(index)],
                   gorilla[0], gorilla[1], GORILLA_COLOR)

        if self._ban is not None:
            mask, left, top, _w, _h = self._banana_box()
            _stamp(cr, mask, round(left), round(top), BANANA_COLOR)

        if self._blast is not None:
            x, y, radius, _size, _duration = self._blast
            cr.set_source_rgb(*EXPLOSION_COLOR)
            _disc(cr, x, y, radius)

        if self._wind:
            cr.set_source_rgb(*EXPLOSION_COLOR)
            span = self._wind * 3
            cr.rectangle(self._lw // 2 + min(0, span), WIND_Y, abs(span), 1)
            cr.fill()

    def _arms(self, index):
        """Which pose a gorilla is standing in this frame."""
        if self._state == AIM and index == self._turn:
            return LEFT_ARM_UP if index == 0 else RIGHT_ARM_UP
        if self._state == VICTORY and index == self._winner:
            if self._timer < self._celebration():
                wave = int(self._timer / TUNING["victory_wave"])
                return LEFT_ARM_UP if wave % 2 else RIGHT_ARM_UP
        return ARMS_DOWN
