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
flatpak-builder --user --install --force-clean build-dir software.sevensummits.screensavor.yml
flatpak run software.sevensummits.screensavor
```

### Requirements

- GNOME Platform/SDK 46
- Python 3
- GTK4
- Libadwaita

## License

This project is licensed under the GPL-3.0-or-later license. See [COPYING](COPYING) for details.

## Author

Built by [7Summits](https://7summits.software)
