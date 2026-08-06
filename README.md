# Screen Savor

A fun, lightweight screensaver application for GNOME desktops built with GTK4 and Libadwaita.

## Features

- **Multiple Screensavers**: Choose from DVD Logo, Matrix Rain, and Color Pulse visualizers
- **Idle Detection**: Automatically activates after a configurable idle timeout using GNOME's IdleMonitor
- **Settings**: Configure idle timeout, enable/disable the daemon, and pick your default screensaver
- **Wayland Native**: Built for modern GNOME desktops

## Building

### Flatpak (Recommended)

```bash
flatpak-builder --user --install --force-clean build-dir software._7summits.ScreenSavor.yml
flatpak run software._7summits.ScreenSavor
```

### Requirements

- GNOME Platform/SDK 50
- Python 3
- GTK4
- Libadwaita

### Screenshots

The screenshots referenced by the AppStream metainfo live in `data/screenshots/` and are
regenerated with `build-aux/capture-screenshots.py`, which renders the savers and the main
window offscreen from inside the Flatpak:

```bash
flatpak run --command=python3 --filesystem="$PWD" software._7summits.ScreenSavor \
  "$PWD/build-aux/capture-screenshots.py" "$PWD/data/screenshots"
```

## License

This project is licensed under the GPL-3.0-or-later license. See [COPYING](COPYING) for details.

## Author

Built by [7Summits](https://7summits.software)
