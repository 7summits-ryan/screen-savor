app_id = 'software._7summits.ScreenSavor'

install_data(
  app_id + '.desktop.in',
  rename: app_id + '.desktop',
  install_dir: join_paths(get_option('datadir'), 'applications')
)

install_data(
  app_id + '.metainfo.xml.in',
  rename: app_id + '.metainfo.xml',
  install_dir: join_paths(get_option('datadir'), 'metainfo')
)

install_data(
  app_id + '.gschema.xml',
  install_dir: join_paths(get_option('datadir'), 'glib-2.0', 'schemas')
)

icondir = join_paths(get_option('datadir'), 'icons', 'hicolor')

install_data(
  join_paths('icons', 'hicolor', 'scalable', 'apps', app_id + '.svg'),
  install_dir: join_paths(icondir, 'scalable', 'apps')
)

# The monochrome variant the tray icon asks for by name. Generated from the
# icon above by build-aux/make-symbolic-icon.py.
install_data(
  join_paths('icons', 'hicolor', 'symbolic', 'apps', app_id + '-symbolic.svg'),
  install_dir: join_paths(icondir, 'symbolic', 'apps')
)

foreach size : ['64x64', '128x128', '256x256', '512x512']
  install_data(
    join_paths('icons', 'hicolor', size, 'apps', app_id + '.png'),
    install_dir: join_paths(icondir, size, 'apps')
  )
endforeach

# Install Comfortaa font for the Video Clock screensaver
install_data(
  join_paths('fonts', 'Comfortaa.ttf'),
  install_dir: join_paths(get_option('datadir'), 'screensavers', 'fonts')
)
