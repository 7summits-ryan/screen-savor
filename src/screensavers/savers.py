import gi
gi.require_version('Gtk', '4.0')
from gi.repository import Gtk, Gdk, GLib
import math
import random

class DVDLogoSaver(Gtk.DrawingArea):
    def __init__(self):
        super().__init__()
        self.set_draw_func(self.on_draw)
        self.x = 100
        self.y = 100
        self.dx = 3
        self.dy = 3
        self.width = 200
        self.height = 100
        self.color = (1, 0, 0)
        
        GLib.timeout_add(16, self.update) # ~60fps

    def update(self):
        if not self.get_mapped():
            return False
        
        width = self.get_width()
        height = self.get_height()

        if width <= 0 or height <= 0:
            return True

        self.x += self.dx
        self.y += self.dy

        hit = False
        if self.x <= 0 or self.x + self.width >= width:
            self.dx *= -1
            hit = True
            
        if self.y <= 0 or self.y + self.height >= height:
            self.dy *= -1
            hit = True

        if hit:
            self.color = (random.random(), random.random(), random.random())

        self.queue_draw()
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
        cr.set_font_size(48)
        # Center text roughly
        cr.move_to(self.x + 30, self.y + 65)
        cr.show_text("DVD")

class MatrixSaver(Gtk.DrawingArea):
    def __init__(self):
        super().__init__()
        self.set_draw_func(self.on_draw)
        self.columns = []
        self.font_size = 20
        self.initialized = False
        
        GLib.timeout_add(50, self.update) # ~20fps

    def init_columns(self, width):
        num_cols = int(width / self.font_size)
        self.columns = [random.randint(0, 50) * -self.font_size for _ in range(num_cols)]
        self.initialized = True

    def update(self):
        if not self.get_mapped():
            return False
        self.queue_draw()
        return True

    def on_draw(self, area, cr, width, height):
        if width <= 0 or height <= 0:
            return
            
        if not self.initialized or len(self.columns) != int(width / self.font_size):
            self.init_columns(width)

        # Fade effect
        cr.set_source_rgba(0, 0, 0, 0.1)
        cr.rectangle(0, 0, width, height)
        cr.fill()

        cr.set_source_rgb(0, 1, 0)
        cr.select_font_face("Monospace", 0, 0)
        cr.set_font_size(self.font_size)

        chars = "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789@#$%^&*"
        for i in range(len(self.columns)):
            char = random.choice(chars)
            x = i * self.font_size
            y = self.columns[i]
            
            if y > 0:
                cr.move_to(x, y)
                cr.show_text(char)
            
            if y > height and random.random() > 0.975:
                self.columns[i] = 0
            else:
                self.columns[i] += self.font_size

class ColorPulseSaver(Gtk.DrawingArea):
    def __init__(self):
        super().__init__()
        self.set_draw_func(self.on_draw)
        self.time = 0
        
        GLib.timeout_add(16, self.update)

    def update(self):
        if not self.get_mapped():
            return False
        self.time += 0.02
        self.queue_draw()
        return True

    def on_draw(self, area, cr, width, height):
        if width <= 0 or height <= 0:
            return

        r = (math.sin(self.time) + 1) / 2
        g = (math.sin(self.time + 2) + 1) / 2
        b = (math.sin(self.time + 4) + 1) / 2

        cr.set_source_rgb(r, g, b)
        cr.paint()
