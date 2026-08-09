# Brand asset contract

The renderer uses `assets/brand/moki-logo-horizontal.png` when present. Supply a transparent PNG with the approved horizontal M icon and MOKI wordmark; the renderer scales it inside a 255 by 64 pixel maximum box without distortion.

Until that asset is supplied, the renderer extracts the same lockup from `moki-brand-reference.png`, the user-provided MOKI brand board. Do not change the crop coordinates without a new template version and a visual regression approval.
