pkgdatadir = join_paths(get_option('prefix'), get_option('datadir'), meson.project_name())
moduledir = join_paths(pkgdatadir, 'screensavers')

python_sources = [
  'screensavers/__init__.py',
  'screensavers/main.py',
  'screensavers/window.py',
  'screensavers/savers.py',
  'screensavers/session.py',
  'screensavers/idle.py',
  'screensavers/wlidle.py',
]

python_installation.install_sources(python_sources, subdir: 'screensavers')

conf = configuration_data()
conf.set('PYTHON', python_installation.full_path())
conf.set('VERSION', meson.project_version())
conf.set('localedir', join_paths(get_option('prefix'), get_option('localedir')))
conf.set('pkgdatadir', pkgdatadir)

configure_file(
  input: 'software._7summits.ScreenSavor.in',
  output: 'software._7summits.ScreenSavor',
  configuration: conf,
  install: true,
  install_dir: get_option('bindir'),
  install_mode: 'rwxr-xr-x'
)
