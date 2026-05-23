"""Pixel-align the AI-painted "after" frame to the "before" aerial.

Why
---
nano-banana (Gemini Flash Image) is *asked* to keep the same framing
(prompt RULE 5, "FRAMING LOCK"), but LLM image models don't guarantee
pixel geometry: the returned frame is frequently shifted, slightly
re-cropped or re-scaled relative to the input. The before→after reveal
is a plain ``xfade=wipedown`` crossfade between the two full frames, so
even a few-pixel drift makes the roof visibly *jump* as the wipe passes.

``normalize_to_output_dimensions`` only forces both frames to the same
*size* — it does nothing about the *content* drifting inside that frame,
and a naive resize on a re-cropped frame actually stretches the roof.

This module fixes the drift directly. Because the prompt also forces the
model to preserve EVERY pixel except the roof (RULE 1), the two frames
are ~identical everywhere outside the roof — that large unchanged context
(roads, parking lines, neighbouring buildings, the ground) is exactly
what an intensity-based registration locks onto. We estimate the small
rigid+scale transform that best maps the after frame back onto the
before frame (OpenCV ECC, ``MOTION_AFFINE``) and warp the after frame by
it. The roof, being a small fraction of the frame, doesn't pull the
solution.

Safety
------
* Same-size inputs only — caller normalises first; we re-size defensively.
* The estimated affine is *validated*: if it implies an implausible
  scale (>±18%) or strong shear, we reject it (the model probably
  changed the scene too much for a rigid map) and return the after
  unchanged. Better a tiny static drift than a warped roof.
* Any failure (ECC non-convergence, decode error, OpenCV missing) →
  return the after bytes unchanged. Alignment is an enhancement, never
  a hard dependency of the render pipeline.
"""

from __future__ import annotations

import io

import numpy as np
from PIL import Image

from ..core.logging import get_logger

log = get_logger(__name__)

# Reject an estimated transform whose scale departs from 1.0 by more than
# this — a legit framing drift is small; a big scale means ECC latched
# onto the changed roof or the scene genuinely differs.
_MAX_SCALE_DRIFT = 0.18
# Reject visible shear (off-diagonal asymmetry of the linear part).
_MAX_SHEAR = 0.08
# ECC optimiser budget. 50 iterations on a downscaled image converges in
# a couple hundred ms; the termination eps stops earlier when stable.
_ECC_ITERATIONS = 60
_ECC_EPS = 1e-4
# ECC is run on a downscaled grayscale pair for speed + robustness to
# high-freq panel texture; the resulting translation is rescaled back.
_ECC_WORK_W = 640


def align_after_to_before(before_png: bytes, after_png: bytes) -> bytes:
    """Return ``after_png`` warped to pixel-align with ``before_png``.

    Both inputs must already be the same pixel size (call
    ``normalize_to_output_dimensions`` first). On any problem the original
    ``after_png`` is returned unchanged — this never raises.
    """
    try:
        import cv2  # local import: keeps cv2 off the hot path when unused
    except Exception:  # noqa: BLE001 — cv2 not installed / failed to load
        log.warning("align.cv2_unavailable")
        return after_png

    try:
        before = Image.open(io.BytesIO(before_png)).convert("RGB")
        after = Image.open(io.BytesIO(after_png)).convert("RGB")
        if before.size != after.size:
            # Defensive: bring after to before's exact size before aligning.
            after = after.resize(before.size, Image.LANCZOS)

        full_w, full_h = before.size
        before_np = np.asarray(before)
        after_np = np.asarray(after)

        # ---- ECC on a downscaled grayscale pair ----------------------
        scale = _ECC_WORK_W / float(full_w)
        work_w = _ECC_WORK_W
        work_h = max(1, int(round(full_h * scale)))

        ref_gray = cv2.cvtColor(before_np, cv2.COLOR_RGB2GRAY)
        mov_gray = cv2.cvtColor(after_np, cv2.COLOR_RGB2GRAY)
        ref_small = cv2.resize(ref_gray, (work_w, work_h), interpolation=cv2.INTER_AREA)
        mov_small = cv2.resize(mov_gray, (work_w, work_h), interpolation=cv2.INTER_AREA)
        # Note: NO equalizeHist — posterising a smooth aerial breaks ECC
        # convergence ("non-overlapped" error). A light Gaussian via the
        # filt arg below is enough to stabilise the gradient.

        criteria = (
            cv2.TERM_CRITERIA_EPS | cv2.TERM_CRITERIA_COUNT,
            _ECC_ITERATIONS,
            _ECC_EPS,
        )

        # Stage 1 — pure TRANSLATION. This is the most robust ECC mode and
        # recovers the dominant nano-banana drift (a horizontal/vertical
        # shift). We keep its result as the safe fallback.
        warp_tr = np.eye(2, 3, dtype=np.float32)
        try:
            cv2.findTransformECC(
                ref_small, mov_small, warp_tr, cv2.MOTION_TRANSLATION, criteria, None, 5
            )
        except cv2.error as exc:
            log.info("align.ecc_no_convergence", stage="translation", err=str(exc)[:100])
            return after_png

        # Stage 2 — refine to AFFINE, seeded from the translation estimate,
        # to also absorb small scale/rotation. If it diverges or yields an
        # implausible linear part, we fall back to the translation-only warp.
        warp_af = np.eye(2, 3, dtype=np.float32)
        warp_af[0, 2] = warp_tr[0, 2]
        warp_af[1, 2] = warp_tr[1, 2]
        use_warp = warp_tr
        try:
            cv2.findTransformECC(
                ref_small, mov_small, warp_af, cv2.MOTION_AFFINE, criteria, None, 5
            )
            a, b = float(warp_af[0, 0]), float(warp_af[0, 1])
            c, d = float(warp_af[1, 0]), float(warp_af[1, 1])
            sx = (a * a + c * c) ** 0.5
            sy = (b * b + d * d) ** 0.5
            shear = abs(a * b + c * d) / (sx * sy + 1e-9)
            if (
                abs(sx - 1.0) <= _MAX_SCALE_DRIFT
                and abs(sy - 1.0) <= _MAX_SCALE_DRIFT
                and shear <= _MAX_SHEAR
            ):
                use_warp = warp_af
            else:
                log.info(
                    "align.affine_rejected_keep_translation",
                    sx=round(sx, 4),
                    sy=round(sy, 4),
                    shear=round(shear, 4),
                )
        except cv2.error:
            # Affine refinement diverged — keep the robust translation warp.
            log.info("align.affine_diverged_keep_translation")

        # ---- Rescale translation to full res and warp the colour after --
        # The linear part is scale-invariant; only the translation column
        # (in downscaled px) must be multiplied back up to full-res px.
        warp_full = use_warp.copy()
        warp_full[0, 2] /= scale
        warp_full[1, 2] /= scale

        # ECC returns the warp mapping reference→after coords, so we must
        # resample the after with WARP_INVERSE_MAP to land it back in the
        # before's frame. (Plain INTER_LINEAR would invert the matrix and
        # double the drift instead of cancelling it.)
        aligned = cv2.warpAffine(
            after_np,
            warp_full,
            (full_w, full_h),
            flags=cv2.INTER_LINEAR + cv2.WARP_INVERSE_MAP,
            borderMode=cv2.BORDER_REPLICATE,
        )

        out = Image.fromarray(aligned, mode="RGB")
        buf = io.BytesIO()
        out.save(buf, format="PNG", optimize=True, compress_level=6)
        log.info(
            "align.ok",
            mode="affine" if use_warp is warp_af else "translation",
            tx=round(float(warp_full[0, 2]), 1),
            ty=round(float(warp_full[1, 2]), 1),
        )
        return buf.getvalue()
    except Exception as exc:  # noqa: BLE001 — never break the render
        log.warning("align.failed", err=str(exc)[:160])
        return after_png
