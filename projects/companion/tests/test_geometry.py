"""A-level checks for the pure multi-monitor / DPI math used by the VRM pet (docs/03 V4/V9).

Everything here runs without a display or Win32: geometry.py is pure data. This is the
first-day V9 defence the C03 card demands - the risky physical/logical arithmetic is
covered long before anyone drags a window across real screens.
"""
import pytest
from app.pet3d import geometry as geo


def rect(l, t, r, b):
    return geo.Rect(l, t, r, b)


def single_monitor(scale=1.0):
    return geo.Monitor("\\\\.\\LCD", rect(0, 0, 1920, 1080), scale)


def mixed_layout():
    """100% left screen + 150% right screen, the exact V9 shape."""
    left = geo.Monitor("\\\\.\\LCD-A", rect(0, 0, 1920, 1080), 1.0)
    right = geo.Monitor("\\\\.\\LCD-B", rect(1920, 0, 4320, 1440), 1.5)
    return geo.Layout((left, right))


def test_rect_basics():
    r = rect(10, 20, 110, 120)
    assert (r.width, r.height) == (100, 100)
    assert r.center == (60, 70)
    assert r.contains(10, 20) and not r.contains(110, 120)
    assert rect(5, 5, 5, 9).empty  # zero width is empty by definition
    assert not rect(5, 5, 6, 9).empty


def test_physical_logical_roundtrip_at_150_percent():
    monitor = geo.Monitor("m", rect(0, 0, 2880, 1620), 1.5)
    logical = geo.Rect(40, 50, 40 + 320, 50 + 380)  # the configured 320x380 pet
    physical = monitor.to_physical(logical)
    assert physical == rect(60, 75, 540, 645)  # scaled by 1.5
    back = monitor.to_logical(physical)
    assert back == logical


def test_negative_virtual_desktop_coordinates_survive():
    second = geo.Monitor("\\\\.\\SECOND", rect(-1920, 0, 0, 1080), 1.0)
    layout = geo.Layout((single_monitor(), second))
    assert layout.virtual.left == -1920
    assert layout.monitor_at(-100, 100) is second
    assert layout.monitor_at(100, 100) == single_monitor()
    assert layout.monitor_at(5000, 5000) is None


def test_clamp_keeps_size_and_moves_into_monitor():
    monitor = single_monitor()
    hung = rect(1800, 1000, 2120, 1380)  # pet half off the bottom-right corner
    fixed = monitor.clamp(hung)
    assert (fixed.width, fixed.height) == (hung.width, hung.height)
    assert monitor.contains_rect(fixed)
    assert fixed.right == 1920 and fixed.bottom == 1080


def test_clamp_of_larger_than_screen_window_shrinks_to_fit():
    monitor = geo.Monitor("tiny", rect(0, 0, 200, 100), 1.0)
    fixed = monitor.clamp(rect(0, 0, 500, 500))
    assert fixed == rect(0, 0, 200, 100)


def test_drag_into_gap_between_screens_returns_to_nearest_monitor():
    layout = mixed_layout()
    # pointer in the dead zone past both screens (would happen with a screen gap).
    target = geo.clamp_during_drag((5000, 900), (160, 190), (320, 380), layout)
    assert layout.monitors[-1].contains_rect(target)
    assert target.right == 4320


def test_reanchor_preserves_logical_size_per_screen_scale():
    layout = mixed_layout()
    was = rect(3000, 500, 3000 + 480, 500 + 570)  # 320x380 at 150%
    seated = geo.reanchor(was, layout, (320, 380))
    assert (seated.width, seated.height) == (480, 570)  # apparent size identical on the 150% screen
    assert layout.monitors[1].contains_rect(seated)


def test_reanchor_after_unplugging_monitor_lands_on_existing_screen():
    layout = mixed_layout()
    gone = rect(30000, 30000, 30480, 30570)  # coordinates of a monitor that no longer exists
    seated = geo.reanchor(gone, layout, (320, 380))
    assert layout.monitors[1].contains_rect(seated)


def test_monitor_nearest_prefers_containing_then_distance():
    layout = mixed_layout()
    inside = rect(100, 100, 420, 480)
    assert layout.monitor_nearest(inside) is layout.monitors[0]
    outside = rect(5000, 100, 5320, 480)
    assert layout.monitor_nearest(outside) is layout.monitors[1]
    assert geo.Layout(()).monitor_nearest(inside) is None


def test_cursor_norm_is_bounded_and_centered():
    monitor = single_monitor(scale=1.5)
    # physical centre of a 1920x1080 physical monitor is (960, 540).
    nx, ny = geo.cursor_norm((960, 540), monitor)
    assert abs(nx) < 0.01 and abs(ny) < 0.01
    assert geo.cursor_norm((-99999, 99999), monitor) == (-1.0, 1.0)


def test_monitor_rejects_nonpositive_scale():
    with pytest.raises(ValueError):
        geo.Monitor("bad", rect(0, 0, 100, 100), 0.0)
