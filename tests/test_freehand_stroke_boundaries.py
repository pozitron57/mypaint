"""Replay pen/mouse events through FreehandMode without opening GTK windows."""

import importlib.util
import itertools
import json
from pathlib import Path
import sys
import types
import unittest
from unittest.mock import patch

import numpy as np


ROOT = Path(__file__).resolve().parents[1]
BUTTON1 = 1 << 8


class Event:
    def __init__(self, kind, time, x, y, pressure=None, state=0, button=1):
        self.type = kind
        self.time, self.x, self.y = time, x, y
        self.pressure, self.state, self.button = pressure, state, button

    def get_axis(self, axis):
        return self.pressure if axis == "pressure" else None

    def get_source_device(self):
        return "test pen" if self.pressure is not None else "test mouse"


def load_freehand():
    """Import the actual mode and interpolator with only the UI stubbed."""
    import gui

    class InteractionMode:
        def __init__(self, **kwargs):
            pass

        def button_press_cb(self, *args):
            return False

        def button_release_cb(self, *args):
            return False

    mode = types.ModuleType("gui.mode")
    mode.InteractionMode = InteractionMode
    mode.BrushworkModeMixin = type("BrushworkModeMixin", (), {})
    mode.ScrollableModeMixin = type("ScrollableModeMixin", (), {})
    mode.PaintingModeOptionsWidgetBase = object
    mode.Behavior = types.SimpleNamespace(PAINT_FREEHAND=1, CHANGE_VIEW=2)
    bindings = types.ModuleType("lib.gibindings")
    bindings.Gtk = types.SimpleNamespace()
    bindings.Gdk = types.SimpleNamespace(
        EventType=types.SimpleNamespace(BUTTON_PRESS="press"),
        AxisUse=types.SimpleNamespace(PRESSURE="pressure", XTILT="xtilt",
                                      YTILT="ytilt", WHEEL="wheel"),
        ModifierType=types.SimpleNamespace(BUTTON1_MASK=BUTTON1),
    )
    ids = itertools.count(1)
    bindings.GLib = types.SimpleNamespace(
        PRIORITY_DEFAULT_IDLE=200, idle_add=lambda *args, **kwargs: next(ids),
    )
    tdw = types.ModuleType("gui.tileddrawwidget")
    tdw.TiledDrawWidget = object
    helpers = types.ModuleType("lib.helpers")
    helpers.clamp = lambda value, low, high: min(high, max(low, value))
    drawutils = types.ModuleType("gui.drawutils")
    # Every test supplies all axes, so no missing-axis interpolation is needed.
    drawutils.spline_4p = None
    slider = types.ModuleType("gui.sliderwidget")
    slider.InputSlider = object
    replacements = {m.__name__: m for m in
                    (mode, bindings, tdw, helpers, drawutils, slider)}
    spec = importlib.util.spec_from_file_location(
        "gui._freehand_stroke_boundary_tests", ROOT / "gui/freehand.py"
    )
    subject = importlib.util.module_from_spec(spec)
    with patch.dict(sys.modules, replacements), patch.object(gui, "mode", mode, create=True):
        spec.loader.exec_module(subject)
    # The imported module keeps its own gui.mode reference during each test.
    subject.gui = types.SimpleNamespace(mode=mode)
    return subject


class FreehandStrokeBoundaryTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.subject = load_freehand()

    def setUp(self):
        self.mode = self.subject.FreehandMode()
        self.mode._drawing_state = {}
        self.mode._debug = False
        self.started, self.ended, self.painted = [], [], []
        self.boundaries = []
        self.mode.brushwork_begin = lambda model, abrupt=False: self.boundaries.append(abrupt)
        self.mode.doc = types.SimpleNamespace(
            input_stroke_started=self.started.append,
            input_stroke_ended=self.ended.append,
        )
        layer = types.SimpleNamespace(get_paintable=lambda: True)
        layers = types.SimpleNamespace(current=layer)
        self.tdw = types.SimpleNamespace(
            doc=types.SimpleNamespace(layer_stack=layers, _layers=layers,
                                      brush=types.SimpleNamespace(reset=lambda: None)),
            app=types.SimpleNamespace(
                device_monitor=types.SimpleNamespace(device_used=lambda device: True),
                fakepressure=0.5, fakerotation=0.0, pressure_mapping=None,
                preferences={"input.barrel_rotation_offset": 0.0},
            ),
            is_sensitive=True, scale=1.0, rotation=0.0, mirrored=False,
            display_to_model=lambda x, y: (x, y),
            set_last_painting_pos=lambda pos: None,
        )
        # SimpleNamespace isn't hashable; the real canvas is.
        self.tdw = type("Canvas", (), vars(self.tdw))()
        self.tdw.display_to_model = lambda x, y: (x, y)
        self.tdw.set_last_painting_pos = lambda pos: None
        self.mode.stroke_to = lambda model, *data: self.painted.append(data)

    def send(self, event, drain=True):
        if event.type == "press":
            self.mode.button_press_cb(self.tdw, event)
        elif event.type == "release":
            self.mode.button_release_cb(self.tdw, event)
        else:
            self.mode.motion_notify_cb(self.tdw, event)
        if drain:
            self.drain()

    def drain(self):
        state = self.mode._get_drawing_state(self.tdw)
        while state.motion_processing_cbid is not None:
            self.mode._motion_queue_idle_cb(self.tdw)

    def first_stroke(self):
        for event in [Event("motion", 100, 40, 300, 0.0),
                      Event("press", 110, 40, 300, 0.7),
                      Event("motion", 120, 40, 290, 0.7, BUTTON1),
                      Event("motion", 130, 40, 10, 0.7, BUTTON1)]:
            self.send(event)

    def assert_no_bridge(self):
        for previous, current in zip(self.painted, self.painted[1:]):
            # stroke_to data is (dtime, x, y, pressure, ...).
            if abs(current[1] - previous[1]) > 100:
                self.assertEqual((previous[3], current[3]), (0.0, 0.0),
                                 "The pen moved between contacts with pressure still active")
        self.assertTrue(any(p[1] >= 480 and p[3] > 0 for p in self.painted),
                        "The second contact must still paint")

    def second_stroke(self, time=160):
        for event in [Event("press", time, 480, 150, 0.7),
                      Event("motion", time + 10, 480, 150, 0.7, BUTTON1),
                      Event("motion", time + 20, 481, 150, 0.7, BUTTON1),
                      Event("release", time + 30, 481, 150, 0.7, BUTTON1),
                      Event("motion", time + 40, 481, 150, 0.0)]:
            self.send(event)

    def test_release_without_zero_pressure_motion_ends_the_stroke(self):
        self.first_stroke()
        self.send(Event("release", 140, 40, 10, 0.7, BUTTON1))
        self.assertTrue(self.painted)
        self.assertEqual(self.painted[-1][1:4], (40, 10, 0.0))
        self.second_stroke()
        self.assert_no_bridge()
        self.assertEqual((len(self.started), len(self.ended)), (2, 2))
        self.assertEqual(self.boundaries, [True, True])

    def test_missing_release_is_recovered_at_next_pen_press(self):
        self.first_stroke()
        self.second_stroke()
        self.assert_no_bridge()

    def test_hover_with_released_button_recovers_a_lost_release(self):
        self.first_stroke()
        self.send(Event("motion", 140, 480, 150, 0.0))
        self.second_stroke()
        self.assert_no_bridge()

    def test_fast_tap_repositions_without_a_pressure_ramp_between_contacts(self):
        self.first_stroke()
        self.send(Event("release", 140, 40, 10, 0.0, BUTTON1))
        self.second_stroke(time=141)
        self.assert_no_bridge()

    def test_held_pen_keeps_evdev_zero_pressure_workaround(self):
        self.first_stroke()
        self.send(Event("motion", 140, 40, 9, 0.0, BUTTON1))
        state = self.mode._get_drawing_state(self.tdw)
        self.assertEqual(state.last_good_raw_pressure, 0.7)
        self.send(Event("motion", 150, 40, 8, 0.7, BUTTON1))
        self.send(Event("release", 160, 40, 8, 0.0, BUTTON1))
        nonzero = [p for p in self.painted if p[3] > 0]
        self.assertTrue(any(p[2] == 9 for p in nonzero))

    def test_release_bypasses_a_pressure_curve_with_nonzero_minimum(self):
        self.tdw.app.pressure_mapping = lambda pressure: 0.2 + 0.8 * pressure
        self.first_stroke()
        self.send(Event("release", 140, 40, 10, 0.7, BUTTON1))
        self.assertEqual(self.painted[-1][3], 0.0)

    def test_equal_timestamps_do_not_leave_pen_up_buffered(self):
        self.first_stroke()
        for event in [Event("motion", 140, 40, 9, 0.7, BUTTON1),
                      Event("motion", 140, 40, 8, 0.7, BUTTON1),
                      Event("release", 140, 40, 8, 0.7, BUTTON1)]:
            self.send(event, drain=False)
        self.drain()
        self.assertEqual(self.painted[-1][1:4], (40, 8, 0.0))
        self.assertEqual(self.mode._get_drawing_state(self.tdw)._zero_dtime_motions, [])

    def test_mouse_and_trackpad_clicks_still_paint_and_release(self):
        for event in [Event("motion", 100, 40, 300),
                      Event("press", 110, 40, 300),
                      Event("motion", 120, 40, 290, state=BUTTON1),
                      Event("release", 130, 40, 290, state=BUTTON1),
                      Event("motion", 140, 40, 290)]:
            self.send(event)
        self.assertTrue(any(p[3] == 0.5 for p in self.painted))
        self.assertEqual(self.painted[-1][3], 0.0)

    def test_pending_old_motion_finishes_before_the_new_brush_reset(self):
        actions = []
        self.mode.brushwork_begin = lambda model, abrupt=False: actions.append("reset")

        def paint(model, *data):
            actions.append(data[1:4])
            self.painted.append(data)

        self.mode.stroke_to = paint
        for event in [Event("motion", 100, 40, 300, 0.0),
                      Event("press", 110, 40, 300, 0.7),
                      Event("motion", 120, 40, 290, 0.7, BUTTON1),
                      Event("motion", 130, 40, 10, 0.7, BUTTON1),
                      Event("press", 160, 480, 150, 0.7),
                      Event("motion", 170, 480, 150, 0.7, BUTTON1),
                      Event("release", 180, 480, 150, 0.7, BUTTON1)]:
            self.send(event, drain=False)
        self.drain()
        last_reset = len(actions) - 1 - actions[::-1].index("reset")
        self.assertIn((40, 10, 0.7), actions[:last_reset])
        self.assertIn((40, 10, 0.0), actions[:last_reset])
        self.assertTrue(all(item[0] >= 480 for item in actions[last_reset + 1:]))
        self.assert_no_bridge()

    def test_native_brush_engine_does_not_draw_a_bridge(self):
        try:
            from lib import mypaintlib as engine
        except ImportError:
            self.skipTest("Build the native MyPaint extension to run the pixel-level check")
        class Surface:
            def __init__(self):
                self.tiles = {}
                self.backend = engine.TiledSurface(self)

            def _get_tile_numpy(self, tx, ty, readonly):
                key = (tx, ty)
                if key not in self.tiles:
                    self.tiles[key] = np.zeros((engine.TILE_SIZE, engine.TILE_SIZE, 4), dtype=np.uint16)
                return self.tiles[key]

        settings = json.loads((ROOT / "personal-profile/data/brushes/classic/pen.myb").read_text())["settings"]
        inputs = {info["name"]: i for i, info in enumerate(engine.get_libmypaint_brush_inputs())}
        for release_received in (True, False):
            with self.subTest(release_received=release_received):
                self.setUp()
                surface = Surface()
                brush = engine.PythonBrush()
                for index, info in enumerate(engine.get_libmypaint_brush_settings()):
                    value = settings.get(info["cname"], {"base_value": info["default"], "inputs": {}})
                    brush.set_base_value(index, value["base_value"])
                    for name, points in value.get("inputs", {}).items():
                        if name not in inputs:
                            continue
                        brush.set_mapping_n(index, inputs[name], len(points))
                        for number, (x, y) in enumerate(points):
                            brush.set_mapping_point(index, inputs[name], number, x, y)

                # Match Brushwork(abrupt_start=True): reset, prime at zero
                # pressure at the new position, then feed the recorded sample.
                first = [True]
                self.mode.brushwork_begin = lambda model, abrupt=False: first.__setitem__(0, True)

                def paint(model, *data):
                    dtime, x, y, pressure, xt, yt, zoom, rotation, barrel = data
                    surface.backend.begin_atomic()
                    if first[0]:
                        brush.new_stroke()
                        brush.reset()
                        brush.stroke_to(surface.backend, x, y, 0.0, xt, yt, 10.0, zoom, rotation, barrel, True)
                        first[0] = False
                    brush.stroke_to(surface.backend, x, y, pressure, xt, yt, dtime, zoom, rotation, barrel, True)
                    surface.backend.end_atomic()

                self.mode.stroke_to = paint
                self.first_stroke()
                if release_received:
                    self.send(Event("release", 140, 40, 10, 0.7, BUTTON1))
                for event in [Event("press", 160, 480, 150, 0.7),
                              Event("motion", 170, 480, 150, 0.7, BUTTON1),
                              Event("motion", 180, 490, 150, 0.7, BUTTON1),
                              Event("motion", 190, 500, 150, 0.7, BUTTON1),
                              Event("release", 200, 500, 150, 0.7, BUTTON1),
                              Event("motion", 210, 500, 150, 0.0)]:
                    self.send(event)
                image = np.zeros((384, 576, 4), dtype=np.uint16)
                for (tx, ty), pixels in surface.tiles.items():
                    x, y = tx * engine.TILE_SIZE, ty * engine.TILE_SIZE
                    if 0 <= x <= 576 - engine.TILE_SIZE and 0 <= y <= 384 - engine.TILE_SIZE:
                        image[y:y + engine.TILE_SIZE, x:x + engine.TILE_SIZE] = pixels
                self.assertEqual(np.count_nonzero(image[:, 180:350, 3]), 0,
                                 "The brush engine painted between the two contacts")
                self.assertGreater(np.count_nonzero(image[130:170, 460:530, 3]), 0,
                                   "The second contact must still paint with smoothing enabled")


if __name__ == "__main__":
    unittest.main()
