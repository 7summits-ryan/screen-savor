# Screen Savor

A fun, lightweight screensaver application built with GTK4 and Libadwaita.

## Features

- **Multiple Screensavers**: Choose from DVD Logo, Matrix Rain, Gorillas, Pipes, Video Clock,
  and Color Pulse visualizers
- **Video Clock**: A clock over a looping video. Point it at any MP4 of your own and set the
  playback speed; it falls back to the video that ships with the app
- **Idle Detection**: Automatically activates after a configurable idle timeout. Uses the
  `ext-idle-notify-v1` Wayland protocol on KDE Plasma and wlroots compositors, and GNOME's
  Mutter IdleMonitor on GNOME, which implements no idle protocol. The backend is probed
  for at start-up rather than guessed from the desktop name, and the preferences window
  reports which one was found, or that the desktop supports none of them
- **Multi-Monitor**: Covers every connected display, each with its own animation, and
  follows monitors as they are plugged in and out. Can be limited to a single screen
- **Settings**: Configure idle timeout, enable/disable the daemon, and pick your default screensaver
- **Wayland Native**: Built for modern Wayland desktops

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
