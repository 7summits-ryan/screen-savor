import cairo
import math
import random

from screensavers.base import _AnimatedSaver

# Directions: (axis, dx, dy, dz) where axis is 0=X, 1=Y, 2=Z
DIRECTIONS = [
    (0, -1, 0, 0), (0, 1, 0, 0),  # X axis
    (1, 0, -1, 0), (1, 0, 1, 0),  # Y axis
    (2, 0, 0, -1), (2, 0, 0, 1),  # Z axis
]

# Color palette (Tableau colors from the original)
COLORS = [
    (0x1f, 0x77, 0xb4), (0xae, 0xc7, 0xe8), (0xff, 0x7f, 0x0e), (0xff, 0xbb, 0x78),
    (0x2c, 0xa0, 0x2c), (0x98, 0xdf, 0x8a), (0xd6, 0x27, 0x28), (0xff, 0x98, 0x96),
    (0x94, 0x67, 0xbd), (0xc5, 0xb0, 0xd5), (0x8c, 0x56, 0x4b), (0xc4, 0x9c, 0x94),
    (0xe3, 0x77, 0xc2), (0xf7, 0xb6, 0xd2), (0x7f, 0x7f, 0x7f), (0xc7, 0xc7, 0xc7),
    (0x17, 0xbe, 0xcf), (0x9e, 0xda, 0xe5),
]

DEFAULT_TUNING = {
    "pipe_count": 8.0,          # number of pipes
    "grid_size": 16.0,          # size of the 3D grid
    "update_rate": 0.05,        # seconds between pipe updates
    "turn_probability": 0.3,    # chance of turning at each step
    "restart_rate": 30.0,       # seconds between full restarts
    "rotation_speed": 0.15,     # camera rotation speed (radians per second)
    "pipe_thickness": 4.0,      # visual thickness of pipes
    "joint_size": 6.0,          # size of spheres at joints
}

TUNING = dict(DEFAULT_TUNING)

TUNABLES = (
    ("Pipes", "pipe_count", "Number of Pipes",
     "How many pipes grow simultaneously", 1.0, 32.0, 1.0, 0),
    ("Pipes", "grid_size", "Grid Size",
     "Size of the 3D space pipes can occupy", 8.0, 32.0, 1.0, 0),
    ("Pipes", "update_rate", "Growth Speed",
     "Seconds between pipe growth steps (lower = faster)", 0.01, 0.5, 0.01, 2),
    ("Pipes", "turn_probability", "Turn Probability",
     "Chance a pipe turns at each step", 0.0, 1.0, 0.05, 2),
    ("Pipes", "restart_rate", "Restart Interval",
     "Seconds before clearing and starting over", 5.0, 120.0, 5.0, 0),

    ("Visual", "rotation_speed", "Camera Rotation",
     "Speed of camera rotation around the scene", 0.0, 1.0, 0.05, 2),
    ("Visual", "pipe_thickness", "Pipe Thickness",
     "Visual thickness of pipe segments", 1.0, 12.0, 0.5, 1),
    ("Visual", "joint_size", "Joint Size",
     "Size of spheres at pipe joints", 2.0, 16.0, 0.5, 1),
)


class Pipe:
    """A single growing pipe with position, direction, and color."""

    def __init__(self, occupied, grid_size):
        self.occupied = occupied
        self.grid_size = grid_size
        self.color = random.choice(COLORS)
        self.path = []      # list of (x, y, z) grid positions that form the pipe path
        self.restart()

    def restart(self):
        """Start the pipe at a random unoccupied position."""
        size = int(self.grid_size)
        for _ in range(100):  # try up to 100 times
            x = random.randint(-size, size)
            y = random.randint(-size, size)
            z = random.randint(-size, size)
            if (x, y, z) not in self.occupied:
                break

        self.position = (x, y, z)
        self.direction = random.choice(DIRECTIONS)
        self.occupied.add(self.position)
        self.path = [self.position]

    def update(self, turn_probability):
        """Grow the pipe by one segment. Returns True if successful."""
        x, y, z = self.position
        size = int(self.grid_size)

        # Choose direction - prefer continuing straight
        directions = list(DIRECTIONS)
        random.shuffle(directions)
        if random.random() > turn_probability:
            # Prefer current direction
            directions.remove(self.direction)
            directions.insert(0, self.direction)

        # Try each direction until we find a valid one
        for direction in directions:
            axis, dx, dy, dz = direction
            nx, ny, nz = x + dx, y + dy, z + dz

            # Check if position is valid
            if (nx, ny, nz) in self.occupied:
                continue
            if any(n < -size or n > size for n in (nx, ny, nz)):
                continue

            # Valid move - add to path
            self.position = (nx, ny, nz)
            self.occupied.add(self.position)
            self.path.append(self.position)
            self.direction = direction
            return True

        # No valid moves - restart
        self.restart()
        return False


def project_3d(x, y, z, angle, distance):
    """Simple 3D projection with rotation around Y axis."""
    # Rotate around Y axis
    cos_a, sin_a = math.cos(angle), math.sin(angle)
    rx = x * cos_a - z * sin_a
    rz = x * sin_a + z * cos_a
    ry = y

    # Perspective projection
    scale = distance / (distance + rz)
    return rx * scale, ry * scale, scale


class PipesSaver(_AnimatedSaver):
    """3D pipes growing through a cubic grid."""

    TUNABLES = TUNABLES
    TUNING = TUNING
    DEFAULT_TUNING = DEFAULT_TUNING
    TUNING_KEY = "pipes-tuning"

    def __init__(self):
        super().__init__()
        self.pipes = []
        self.occupied = set()
        self.time = 0.0
        self.last_update = 0.0
        self.last_restart = 0.0
        self.rotation_angle = 0.0
        self._configure()

    def _configure(self):
        """Initialize or restart the pipe system."""
        pipe_count = max(1, int(TUNING["pipe_count"]))
        self.occupied.clear()
        self.pipes = [Pipe(self.occupied, TUNING["grid_size"])
                      for _ in range(pipe_count)]
        self.last_update = 0.0
        self.last_restart = 0.0

    def advance(self, dt):
        """Update animation state."""
        self.time += dt
        self.rotation_angle += dt * TUNING["rotation_speed"]

        # Check for full restart
        restart_rate = TUNING["restart_rate"]
        if restart_rate > 0 and self.time - self.last_restart >= restart_rate:
            self.last_restart = self.time
            self._configure()
            return True

        # Check for pipe update
        update_rate = TUNING["update_rate"]
        if self.time - self.last_update >= update_rate:
            self.last_update = self.time
            turn_prob = TUNING["turn_probability"]
            for pipe in self.pipes:
                pipe.update(turn_prob)
            return True

        # Check if camera is rotating
        if TUNING["rotation_speed"] > 0:
            return True

        return False

    def on_draw(self, area, cr, width, height):
        """Render the 3D pipes."""
        if width <= 0 or height <= 0:
            return

        # Black background
        cr.set_source_rgb(0, 0, 0)
        cr.paint()

        # Setup coordinate system
        cx, cy = width / 2.0, height / 2.0
        scale_factor = min(width, height) / 50.0
        distance = TUNING["grid_size"] * 1.5
        thickness = TUNING["pipe_thickness"]
        joint_size = TUNING["joint_size"]

        # Draw each pipe as a continuous path
        for pipe in self.pipes:
            if len(pipe.path) < 2:
                continue

            # Convert color from 0-255 to 0-1 range
            r, g, b = pipe.color
            r, g, b = r / 255.0, g / 255.0, b / 255.0

            # Draw pipe segments connecting each point in the path
            for i in range(len(pipe.path) - 1):
                x1, y1, z1 = pipe.path[i]
                x2, y2, z2 = pipe.path[i + 1]

                # Project both endpoints
                px1, py1, depth1 = project_3d(x1, y1, z1, self.rotation_angle, distance)
                px2, py2, depth2 = project_3d(x2, y2, z2, self.rotation_angle, distance)

                sx1 = cx + px1 * scale_factor
                sy1 = cy + py1 * scale_factor
                sx2 = cx + px2 * scale_factor
                sy2 = cy + py2 * scale_factor

                # Average depth for brightness
                avg_depth = (depth1 + depth2) / 2.0
                brightness = 0.4 + 0.6 * avg_depth
                cr.set_source_rgb(r * brightness, g * brightness, b * brightness)

                # Draw segment as a thick line
                cr.set_line_width(thickness * avg_depth)
                cr.set_line_cap(cairo.LINE_CAP_ROUND)
                cr.move_to(sx1, sy1)
                cr.line_to(sx2, sy2)
                cr.stroke()

            # Draw joints (spheres) at each path point for smooth corners
            for x, y, z in pipe.path:
                px, py, depth = project_3d(x, y, z, self.rotation_angle, distance)
                sx = cx + px * scale_factor
                sy = cy + py * scale_factor

                brightness = 0.4 + 0.6 * depth
                cr.set_source_rgb(r * brightness, g * brightness, b * brightness)
                cr.arc(sx, sy, joint_size * depth / 2, 0, 2 * math.pi)
                cr.fill()
