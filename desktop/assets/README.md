# Application icon

- `live-dead-cell-counter.png`: original generated RGBA artwork with transparent
  corners, used as the master image.
- `live-dead-cell-counter.ico`: Windows icon encoded from the same artwork at
  16, 20, 24, 32, 40, 48, 64, 96, 128 and 256 pixels.

The intact green cell and damaged red cell identify LIVE/DEAD microscopy. The
artwork was created with the built-in image-generation tool on 2026-09-14.
ICO conversion only resamples and encodes the artwork; it preserves its alpha.
No microscopy images or user data were used.

To regenerate the ICO after updating the PNG, run from the project root using
the project environment:

```python
from PIL import Image

image = Image.open("desktop/assets/live-dead-cell-counter.png")
image.save("desktop/assets/live-dead-cell-counter.ico", format="ICO",
           sizes=[(s, s) for s in (16, 20, 24, 32, 40, 48, 64, 96, 128, 256)])
```

## Generation prompt

Use case: logo-brand. Create ONE finished Windows desktop application icon for
a microscopy LIVE/DEAD cell analysis tool. Square 1024 x 1024 image with
genuinely transparent background around the icon, no mockup, no lettering, no
words, no watermark. Strong, simple, professional scientific app mark, easily
recognized at 16–48 pixels. A compact dark deep-teal circular microscope field
badge, large in frame with about 5% transparent outer padding. Within it show
two very bold stylized round cells, side by side: a luminous emerald/lime GREEN
live cell on the left with an intact circular membrane and one clean bright
nucleus, and a vivid coral/RED dead cell on the right with a visibly
interrupted/broken membrane and one darker nucleus. Both equally prominent,
with a little overlap between the cells so the two-color live/dead meaning is
immediate. Keep silhouettes thick, smooth, confident, high contrast. Use flat
vector-like illustration with at most subtle tonal depth, crisp edges, large
uncomplicated shapes, no tiny dots or fine textures, no photorealism, no long
shadows, no extra laboratory objects. Harmonize with an app whose sidebar is
dark teal #163b40 and accents are green #1c7967. The cells should occupy most of
the badge, readable as a paired green/red cell analysis symbol even at taskbar
size. Preserve actual alpha transparency outside the circular badge.
