"""
tests for config.py's settings round-trip: defaults, and that a yaml file
missing newer fields (like multi_monitor, added after some users already had
a config.yaml on disk) doesn't blow up load_config.
"""

import pytest
import yaml

from orvix.config import (
    CalibrationBox,
    Settings,
    delete_profile,
    list_profiles,
    load_config,
    load_profile,
    save_config,
    save_profile,
)


def test_save_config_writes_atomically_no_leftover_tmp_file(tmp_path):
    path = tmp_path / "config.yaml"
    save_config(Settings(pinch_threshold=0.6), path)

    assert load_config(path).pinch_threshold == 0.6
    leftover = [p for p in tmp_path.iterdir() if p != path]
    assert leftover == []


def test_load_config_clamps_out_of_range_thresholds(tmp_path):
    path = tmp_path / "config.yaml"
    save_config(Settings(pinch_threshold=1.5, grab_release_threshold=-0.2), path)

    loaded = load_config(path)

    assert loaded.pinch_threshold == 1.0
    assert loaded.grab_release_threshold == 0.0


def test_load_config_clamps_out_of_range_percent(tmp_path):
    path = tmp_path / "config.yaml"
    save_config(Settings(volume_step_percent=250, volume_max_percent=-10), path)

    loaded = load_config(path)

    assert loaded.volume_step_percent == 100
    assert loaded.volume_max_percent == 0


def test_load_config_clamps_negative_durations(tmp_path):
    path = tmp_path / "config.yaml"
    save_config(Settings(dwell_click_seconds=-1.5, confirm_hold_seconds=-0.1), path)

    loaded = load_config(path)

    assert loaded.dwell_click_seconds == 0.0
    assert loaded.confirm_hold_seconds == 0.0


def test_load_config_clamps_zero_or_negative_step_fields(tmp_path):
    # zoom_step_mm/volume_step_deg feed a `while resid >= step` loop in
    # extra_gestures.py -- a step of 0 or negative never advances resid past
    # itself, so that loop spins forever the instant a zoom/volume gesture
    # fires. unlike the other numeric fields, 0 itself is the dangerous
    # value here, not just negative, so this needs its own floor above zero.
    path = tmp_path / "config.yaml"
    save_config(Settings(zoom_step_mm=0.0, volume_step_deg=-5.0), path)

    loaded = load_config(path)

    assert loaded.zoom_step_mm == 0.1
    assert loaded.volume_step_deg == 0.1


def test_load_config_clamps_radial_dead_zone_px(tmp_path):
    # RadialMenu's wheel window is only 460px (overlay.py's _BOX), centered on
    # the pointer -- a dead zone anywhere near that radius makes _wedge_at()
    # never return a hovered wedge, so every pinch just dismisses and dwell
    # can never accumulate. negative doesn't crash but silently removes the
    # dead zone the docstring says exists on purpose. clamp both directions.
    path = tmp_path / "config.yaml"
    save_config(Settings(radial_dead_zone_px=5000.0), path)

    loaded = load_config(path)

    assert loaded.radial_dead_zone_px == 200.0

    path2 = tmp_path / "config2.yaml"
    save_config(Settings(radial_dead_zone_px=-10.0), path2)

    loaded2 = load_config(path2)

    assert loaded2.radial_dead_zone_px == 0.0


def test_load_config_clamps_radial_open_fields(tmp_path):
    # circle_detector.py's CircleDetector fires when abs(winding) >= threshold
    # and treats a loop as real only once radius >= min_radius. a sweep
    # threshold or min radius of 0 (or negative) means those checks are
    # basically always true, so the "you have to actually sweep a deliberate
    # full loop" gate stops meaning anything and the wheel could pop open from
    # ordinary hand jitter. floor both well above zero.
    path = tmp_path / "config.yaml"
    save_config(
        Settings(radial_open_sweep_deg=-50.0, radial_open_min_radius_mm=0.0), path
    )

    loaded = load_config(path)

    assert loaded.radial_open_sweep_deg == 10.0
    assert loaded.radial_open_min_radius_mm == 1.0


def test_load_config_clamps_dwell_click_radius(tmp_path):
    # _DwellClicker.feed re-arms the hover timer whenever the hand drifts more
    # than dwell_click_radius_mm from the anchor point. a zero or negative
    # radius means basically any tremor counts as drifting off, so the dwell
    # timer never accumulates and hover-to-click silently stops working.
    path = tmp_path / "config.yaml"
    save_config(Settings(dwell_click_radius_mm=-5.0), path)

    loaded = load_config(path)

    assert loaded.dwell_click_radius_mm == 0.5


def test_load_config_clamps_one_euro_filter_fields(tmp_path):
    # one_euro_filter.py's _smoothing_factor computes r / (r + 1) where
    # r = 2*pi*cutoff*t_e -- a negative enough one_euro_min_cutoff drives
    # cutoff past -1/(2*pi*t_e), landing r at exactly -1 and hitting a
    # ZeroDivisionError that crashes the whole gesture dispatch thread.
    # floor min_cutoff above zero (not just non-negative, since cutoff=0
    # freezes the cursor forever at rest) and beta at non-negative, which is
    # enough to guarantee cutoff can never drop below the floored min_cutoff.
    path = tmp_path / "config.yaml"
    save_config(Settings(one_euro_min_cutoff=-20.0, one_euro_beta=-5.0), path)

    loaded = load_config(path)

    assert loaded.one_euro_min_cutoff == 0.01
    assert loaded.one_euro_beta == 0.0


def test_load_config_fixes_release_threshold_not_lower_than_trigger(tmp_path):
    # pinch_release_threshold/grab_release_threshold have to stay below their
    # trigger threshold for the hysteresis to do anything -- if a hand-edited
    # config.yaml has them swapped or equal, gesture_interpreter.py's DOWN
    # state would see "released" on the very next frame after "started", so
    # a pinch or grab can never actually hold or drag no matter how long you
    # keep your fingers together.
    path = tmp_path / "config.yaml"
    save_config(
        Settings(
            pinch_threshold=0.75,
            pinch_release_threshold=0.9,
            grab_threshold=0.6,
            grab_release_threshold=0.6,
        ),
        path,
    )

    loaded = load_config(path)

    assert loaded.pinch_release_threshold < loaded.pinch_threshold
    assert loaded.grab_release_threshold < loaded.grab_threshold


def test_load_config_swaps_inverted_calibration_axis(tmp_path):
    # coord_mapper's _map_range doesn't raise if a calibration axis's min/max
    # are swapped, it just silently inverts that axis's cursor motion -- a
    # hand-edited config.yaml with x_min/x_max transposed would look like the
    # cursor moving backwards on just that one axis, with no error to explain
    # why. swap them back into order instead of clamping to a default, since
    # both are presumably real measurements just recorded in the wrong fields.
    path = tmp_path / "config.yaml"
    save_config(
        Settings(calibration=CalibrationBox(x_min=150.0, x_max=-150.0)),
        path,
    )

    loaded = load_config(path)

    assert loaded.calibration.x_min == -150.0
    assert loaded.calibration.x_max == 150.0


def test_load_config_falls_back_when_calibration_axis_is_not_a_number(tmp_path):
    # unlike the Settings fields walked by _clamp_field, CalibrationBox isn't
    # covered by any of the _*_FIELDS tuples, so a hand-edited
    # `calibration: {x_min: "high"}` reached _sanitize_calibration_axis_order's
    # `lo <= hi` as a raw string and crashed load_config outright instead of
    # falling back like every other bad value in this file.
    path = tmp_path / "config.yaml"
    path.write_text(yaml.safe_dump({"calibration": {"x_min": "high", "y_max": "low"}}))

    loaded = load_config(path)

    default = CalibrationBox()
    assert loaded.calibration.x_min == default.x_min
    assert loaded.calibration.y_max == default.y_max
    # the good side of each pair is untouched
    assert loaded.calibration.x_max == default.x_max
    assert loaded.calibration.y_min == default.y_min


def test_load_config_fixes_tilt_full_not_above_deadzone(tmp_path):
    # TiltCoordMapper._deflection computes
    # `span = max(1e-6, tilt_full - tilt_deadzone)`, which only guards the
    # degenerate divide-by-zero case -- if tilt_deadzone is at or above
    # tilt_full, span floors at 1e-6 and the scaled deflection explodes to a
    # huge number the instant you clear the deadzone, clamping straight to
    # 1.0. tilt mode stops being a smooth ramp and becomes an all-or-nothing
    # snap to max speed right at the deadzone edge.
    path = tmp_path / "config.yaml"
    save_config(Settings(tilt_deadzone=0.6, tilt_full=0.15), path)

    loaded = load_config(path)

    assert loaded.tilt_full > loaded.tilt_deadzone


def test_load_config_falls_back_when_tilt_fields_are_not_numbers(tmp_path):
    # tilt_full/tilt_deadzone aren't covered by any of the _*_FIELDS tuples
    # either, so _sanitize_tilt_deadzone_order's `>` comparison crashed
    # load_config outright on a hand-edited non-numeric value.
    path = tmp_path / "config.yaml"
    path.write_text(yaml.safe_dump({"tilt_full": "nope"}))

    loaded = load_config(path)

    assert loaded.tilt_full == Settings().tilt_full


def test_load_config_fixes_freeze_threshold_not_lower_than_pinch(tmp_path):
    # _freezing_for_click only holds the cursor still while pinch_state is
    # still IDLE and pinch_strength has crossed pinch_freeze_threshold. if
    # freeze_threshold is at or above pinch_threshold, the real pinch always
    # fires first and moves pinch_state out of IDLE before pinch_strength
    # ever reaches freeze_threshold, so the anti-drift freeze silently never
    # engages again.
    path = tmp_path / "config.yaml"
    save_config(Settings(pinch_threshold=0.75, pinch_freeze_threshold=0.9), path)

    loaded = load_config(path)

    assert loaded.pinch_freeze_threshold < loaded.pinch_threshold


def test_load_config_leaves_freeze_threshold_opt_out_alone(tmp_path):
    # a freeze_threshold of 0 (or below) is a deliberate opt-out already
    # handled by _freezing_for_click itself -- it must not get pulled up
    # into the valid range by the same fix that guards the ordering case.
    path = tmp_path / "config.yaml"
    save_config(Settings(pinch_threshold=0.75, pinch_freeze_threshold=0.0), path)

    loaded = load_config(path)

    assert loaded.pinch_freeze_threshold == 0.0


def test_load_config_falls_back_on_wrong_type_threshold(tmp_path):
    # a hand-edited config.yaml can hold any YAML scalar, not just the right
    # type -- `pinch_threshold: "high"` parses fine as a string and used to
    # raise TypeError out of load_config's clamp step (str vs float
    # comparison), crashing `orvix cli` outright since only the GUI wraps
    # load_config in a try/except.
    path = tmp_path / "config.yaml"
    path.write_text(yaml.safe_dump({"pinch_threshold": "high"}))

    loaded = load_config(path)

    assert loaded.pinch_threshold == Settings().pinch_threshold


def test_load_config_drops_unrecognized_top_level_key(tmp_path):
    # a typo'd field name (pinch_threshhold vs pinch_threshold) or a stray
    # key from a different orvix version used to crash Settings(**raw)
    # before _sanitize_settings ever got a chance to run -- unlike a wrong
    # *value*, which the clamp/sanitize tests above already cover.
    path = tmp_path / "config.yaml"
    path.write_text(yaml.safe_dump({"pinch_threshhold": 0.9, "cursor_mode": "relative"}))

    loaded = load_config(path)

    assert loaded.cursor_mode == "relative"
    assert loaded.pinch_threshold == Settings().pinch_threshold


def test_load_config_drops_unrecognized_calibration_key(tmp_path):
    path = tmp_path / "config.yaml"
    path.write_text(yaml.safe_dump({"calibration": {"x_min": 1.0, "bogus_field": 2.0}}))

    loaded = load_config(path)

    assert loaded.calibration.x_min == 1.0


def test_load_config_falls_back_when_calibration_is_not_a_mapping(tmp_path):
    # a hand-edited `calibration: [1, 2, 3]` or `calibration: nope` used to
    # crash _drop_unknown_keys with an AttributeError (no .items()) before
    # CalibrationBox(**calibration_raw) ever ran.
    path = tmp_path / "config.yaml"
    path.write_text(yaml.safe_dump({"calibration": [1, 2, 3], "cursor_mode": "relative"}))

    loaded = load_config(path)

    assert loaded.calibration == Settings().calibration
    assert loaded.cursor_mode == "relative"


def test_load_config_falls_back_when_top_level_is_not_a_mapping(tmp_path):
    # a config.yaml whose entire contents got replaced by a bare list or
    # string used to crash `raw.pop("calibration", ...)` before any
    # sanitizing ever ran.
    path = tmp_path / "config.yaml"
    path.write_text("- 1\n- 2\n")

    loaded = load_config(path)

    assert loaded == Settings()


def test_load_config_falls_back_on_wrong_type_percent(tmp_path):
    path = tmp_path / "config.yaml"
    path.write_text(yaml.safe_dump({"volume_max_percent": [1, 2, 3]}))

    loaded = load_config(path)

    assert loaded.volume_max_percent == Settings().volume_max_percent


def test_load_config_falls_back_on_wrong_type_duration(tmp_path):
    path = tmp_path / "config.yaml"
    path.write_text(yaml.safe_dump({"dwell_click_seconds": None}))

    loaded = load_config(path)

    assert loaded.dwell_click_seconds == Settings().dwell_click_seconds


def test_load_config_leaves_sane_values_untouched(tmp_path):
    path = tmp_path / "config.yaml"
    save_config(Settings(pinch_threshold=0.75, volume_max_percent=18, dwell_click_seconds=1.5), path)

    loaded = load_config(path)

    assert loaded.pinch_threshold == 0.75
    assert loaded.volume_max_percent == 18
    assert loaded.dwell_click_seconds == 1.5


def test_load_config_with_no_file_skips_sanitizing_defaults(tmp_path):
    # defaults are already sane; this just confirms _sanitize_settings runs
    # over the Settings() fallback path too without altering it
    loaded = load_config(tmp_path / "missing.yaml")
    assert loaded == Settings()


def test_multi_monitor_defaults_to_true():
    assert Settings().multi_monitor is True


def test_load_config_drops_unknown_radial_actions(tmp_path):
    path = tmp_path / "config.yaml"
    save_config(Settings(radial_actions=["undo", "typo_action", "copy"]), path)

    loaded = load_config(path)

    assert loaded.radial_actions == ["undo", "copy"]


def test_load_config_falls_back_to_defaults_when_all_radial_actions_invalid(tmp_path):
    path = tmp_path / "config.yaml"
    save_config(Settings(radial_actions=["nonsense", "still_nonsense"]), path)

    loaded = load_config(path)

    assert loaded.radial_actions == list(Settings().radial_actions)
    assert loaded.radial_actions  # never empty


def test_load_config_falls_back_when_radial_actions_is_empty(tmp_path):
    path = tmp_path / "config.yaml"
    save_config(Settings(radial_actions=[]), path)

    loaded = load_config(path)

    assert loaded.radial_actions == list(Settings().radial_actions)


def test_load_config_leaves_valid_radial_actions_untouched(tmp_path):
    path = tmp_path / "config.yaml"
    custom = ["copy", "paste", "close"]
    save_config(Settings(radial_actions=custom), path)

    loaded = load_config(path)

    assert loaded.radial_actions == custom


def test_load_config_falls_back_when_radial_actions_is_not_a_list(tmp_path):
    # a hand-edited config.yaml could hold `radial_actions: 5` or a mapping
    # instead of a list -- `a in _VALID_RADIAL_ACTIONS` used to raise
    # TypeError (unhashable dict, or a plain non-iterable), crashing
    # load_config outright instead of falling back like every other bad
    # value in this file.
    path = tmp_path / "config.yaml"
    path.write_text(yaml.safe_dump({"radial_actions": 5}))

    loaded = load_config(path)

    assert loaded.radial_actions == list(Settings().radial_actions)


def test_load_config_falls_back_when_radial_actions_has_unhashable_entries(tmp_path):
    path = tmp_path / "config.yaml"
    path.write_text(yaml.safe_dump({"radial_actions": [["nested", "list"], {"a": 1}]}))

    loaded = load_config(path)

    assert loaded.radial_actions == list(Settings().radial_actions)


def test_load_config_falls_back_to_default_pinch_action_when_invalid(tmp_path):
    path = tmp_path / "config.yaml"
    save_config(Settings(pinch_action="typo_action"), path)

    loaded = load_config(path)

    assert loaded.pinch_action == "click"


def test_load_config_falls_back_to_default_grab_action_when_invalid(tmp_path):
    path = tmp_path / "config.yaml"
    save_config(Settings(grab_action="not_a_real_action"), path)

    loaded = load_config(path)

    assert loaded.grab_action == "scroll"


def test_load_config_falls_back_when_gesture_action_is_unhashable(tmp_path):
    # `pinch_action: [not, a, string]` used to raise TypeError out of
    # `value in _VALID_GESTURE_ACTIONS` (unhashable list), same class of bug
    # as the radial_actions one above.
    path = tmp_path / "config.yaml"
    path.write_text(yaml.safe_dump({"pinch_action": ["not", "a", "string"]}))

    loaded = load_config(path)

    assert loaded.pinch_action == "click"


def test_load_config_leaves_valid_gesture_actions_untouched(tmp_path):
    path = tmp_path / "config.yaml"
    save_config(Settings(pinch_action="disabled", grab_action="click"), path)

    loaded = load_config(path)

    assert loaded.pinch_action == "disabled"
    assert loaded.grab_action == "click"


def test_save_then_load_round_trips_multi_monitor(tmp_path):
    path = tmp_path / "config.yaml"
    save_config(Settings(multi_monitor=False), path)
    loaded = load_config(path)
    assert loaded.multi_monitor is False


def test_missing_multi_monitor_key_falls_back_to_default(tmp_path):
    # simulates an existing config.yaml written before this field existed
    path = tmp_path / "config.yaml"
    path.write_text("cursor_mode: tilt\n")
    loaded = load_config(path)
    assert loaded.multi_monitor is True
    assert loaded.cursor_mode == "tilt"


def test_cursor_ring_enabled_defaults_to_false():
    assert Settings().cursor_ring_enabled is False


def test_save_then_load_round_trips_cursor_ring_enabled(tmp_path):
    path = tmp_path / "config.yaml"
    save_config(Settings(cursor_ring_enabled=True), path)
    loaded = load_config(path)
    assert loaded.cursor_ring_enabled is True


def test_missing_cursor_ring_enabled_key_falls_back_to_default(tmp_path):
    # simulates an existing config.yaml written before this field existed
    path = tmp_path / "config.yaml"
    path.write_text("cursor_mode: tilt\n")
    loaded = load_config(path)
    assert loaded.cursor_ring_enabled is False


def test_volume_scaling_fields_have_sane_defaults():
    settings = Settings()
    assert settings.volume_step_percent == 6
    assert settings.volume_max_percent == 18
    assert settings.volume_rate_slow_deg_s == 30.0
    assert settings.volume_rate_fast_deg_s == 200.0


def test_save_then_load_round_trips_volume_max_percent(tmp_path):
    path = tmp_path / "config.yaml"
    save_config(Settings(volume_max_percent=25), path)
    loaded = load_config(path)
    assert loaded.volume_max_percent == 25


def test_missing_volume_max_percent_key_falls_back_to_default(tmp_path):
    # simulates an existing config.yaml written before this field existed
    path = tmp_path / "config.yaml"
    path.write_text("cursor_mode: tilt\n")
    loaded = load_config(path)
    assert loaded.volume_max_percent == 18


def test_load_config_with_no_file_returns_defaults(tmp_path):
    path = tmp_path / "does_not_exist.yaml"
    loaded = load_config(path)
    assert loaded == Settings()


def test_thumbs_up_action_defaults_to_confirm():
    assert Settings().thumbs_up_action == "confirm"


def test_save_then_load_round_trips_thumbs_up_action(tmp_path):
    path = tmp_path / "config.yaml"
    save_config(Settings(thumbs_up_action="undo"), path)
    loaded = load_config(path)
    assert loaded.thumbs_up_action == "undo"


def test_load_config_falls_back_to_confirm_when_thumbs_up_action_is_unhashable(tmp_path):
    path = tmp_path / "config.yaml"
    path.write_text(yaml.safe_dump({"thumbs_up_action": ["undo"]}))

    loaded = load_config(path)

    assert loaded.thumbs_up_action == "confirm"


def test_load_config_falls_back_to_confirm_when_thumbs_up_action_invalid(tmp_path):
    path = tmp_path / "config.yaml"
    save_config(Settings(thumbs_up_action="typo_action"), path)

    loaded = load_config(path)

    assert loaded.thumbs_up_action == "confirm"


def test_load_config_falls_back_to_right_when_preferred_hand_invalid(tmp_path):
    path = tmp_path / "config.yaml"
    save_config(Settings(preferred_hand="lft"), path)

    loaded = load_config(path)

    assert loaded.preferred_hand == "right"


def test_load_config_falls_back_when_preferred_hand_is_unhashable(tmp_path):
    path = tmp_path / "config.yaml"
    path.write_text(yaml.safe_dump({"preferred_hand": ["left"]}))

    loaded = load_config(path)

    assert loaded.preferred_hand == "right"


def test_load_config_keeps_valid_preferred_hand(tmp_path):
    path = tmp_path / "config.yaml"
    save_config(Settings(preferred_hand="first"), path)

    loaded = load_config(path)

    assert loaded.preferred_hand == "first"


def test_load_config_falls_back_to_absolute_when_cursor_mode_invalid(tmp_path):
    path = tmp_path / "config.yaml"
    save_config(Settings(cursor_mode="relatve"), path)

    loaded = load_config(path)

    assert loaded.cursor_mode == "absolute"


def test_load_config_falls_back_when_cursor_mode_is_unhashable(tmp_path):
    path = tmp_path / "config.yaml"
    path.write_text(yaml.safe_dump({"cursor_mode": ["absolute"]}))

    loaded = load_config(path)

    assert loaded.cursor_mode == "absolute"


def test_load_config_keeps_valid_cursor_mode(tmp_path):
    path = tmp_path / "config.yaml"
    save_config(Settings(cursor_mode="tilt"), path)

    loaded = load_config(path)

    assert loaded.cursor_mode == "tilt"


def test_list_profiles_empty_when_dir_missing(tmp_path):
    assert list_profiles(tmp_path / "profiles") == []


def test_save_profile_then_list_and_load(tmp_path):
    profiles_dir = tmp_path / "profiles"
    save_profile("demo", Settings(cursor_mode="tilt"), profiles_dir)
    save_profile("precision", Settings(cursor_mode="absolute"), profiles_dir)

    assert list_profiles(profiles_dir) == ["demo", "precision"]
    assert load_profile("demo", profiles_dir).cursor_mode == "tilt"
    assert load_profile("precision", profiles_dir).cursor_mode == "absolute"


def test_load_profile_missing_raises(tmp_path):
    with pytest.raises(FileNotFoundError):
        load_profile("nope", tmp_path / "profiles")


def test_delete_profile(tmp_path):
    profiles_dir = tmp_path / "profiles"
    save_profile("temp", Settings(), profiles_dir)
    assert list_profiles(profiles_dir) == ["temp"]

    delete_profile("temp", profiles_dir)
    assert list_profiles(profiles_dir) == []


def test_delete_profile_missing_raises(tmp_path):
    with pytest.raises(FileNotFoundError):
        delete_profile("nope", tmp_path / "profiles")


@pytest.mark.parametrize("bad_name", ["", ".", "..", "a/b", "../escape", "a b"])
def test_invalid_profile_name_rejected(tmp_path, bad_name):
    with pytest.raises(ValueError):
        save_profile(bad_name, Settings(), tmp_path / "profiles")
