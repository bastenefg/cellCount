# Version 1.3.2 icon validation

This release adds an icon; the counting and segmentation pipeline is unchanged.

- `icon_sizes.png`: visual review of the actual Qt icon at common Windows
  sizes on light and dark backgrounds.
- `source_icon.json`: source MainWindow icon pixels match the supplied ICO at
  all ten sizes, from 16 to 256 pixels.
- `gui_startup.json`: GUI entry-point check of the application/window icon and
  the Windows taskbar application identity.
- `embedded_icon.json`: exact comparison of the EXE's RT_GROUP_ICON and RT_ICON
  resources with every frame in the supplied ICO.
- `release_audit.json`: runtime and editable asset copies, original package
  preservation, dependency notices, and exact ZIP contents.

The generated artwork and its prompt are in `desktop/assets/`. No microscopy
images or user data were used to create it. These checks concern application
branding and packaging; they do not repeat scientific measurements.
