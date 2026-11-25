# Pinned ParamFace host/ARM port

Uses actual MIT ParamFace core from zziying/stackchan-face-editor commit
`5ef10f1da988dad7be001dd23a8bc5743866e93f`; no copied replacement face format.
`tools/paramface_source.py` fetches/verifies the clean exact checkout and records
source hashes/license under ignored `build/paramface`. The only derived upstream
code is its host M5GFX shim: arc raster loops are clipped to the viewport. An
unmodified-shim control binary verifies identical preset pixels.

```sh
make -C native/paramface host arm asan
python3 -m unittest tests.test_paramface
```

`libcammy_paramface.so` serves the serialized host adapter in `host/cammy_face.py`.
`render-arm` is a static ARMv7 hard-float executable with the existing legacy-stat
shim. It reads a JSON file and writes one RGB PPM to stdout after bounded ticks.
It opens no framebuffer, display, audio or motor node and has no network code.
The wrapper bounds sizes, expression indices, tick intervals and external drives;
the host additionally admits bounded finite/unique-key schema1/2 JSON. The
existing upstream `/status`, `/face` and expression vocabulary guide app controls.

Ten focused tests pass: six real presets, six expressions, driven mouth/gaze,
animation, invalid-load preservation, invalid inputs, independent/concurrent
contexts, viewport work bounds, original-shim pixel preservation,18 ARM/qemu
preset-expression comparisons and six ASan/UBSan animation runs. ASan compilation
retains an upstream ArduinoJson `maybe-uninitialized` warning in its iterator
sentinel; only that vendor translation unit permits that warning. Cammy wrapper
and renderer build with warnings treated as errors. Runtime sanitizer checks
passed; this does not claim every third-party parser path is memory-safe.

## Real camera CPU proof — 2026-11-15

A verified exclusive RAM copy rendered the cat/happy preset at356×400 for240
33.333ms animation steps. Uptime delta3.25s (~73.8 rendered frames/s in this one
CPU-only sample); final427,215B PPM exactly matched the host. Native PID/start,
boot and stock hashes stayed unchanged. RAM files/directory were removed and
absence verified. No firmware, init, native executable or LCD was written.

Evidence: `private/paramface/ram-render.json`. This proves executable/rendering
compatibility on the real processor, not screen visibility, LCD refresh rate,
coexistence under sustained media load or a physical robot face. The owned
LCD/window/display-layer integration remains a separate gate.
