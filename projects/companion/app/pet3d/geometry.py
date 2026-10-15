"""Pure geometry for the desktop pet. No Qt, no Win32, no I/O - so it can be unit tested.

All rectangles here are *physical device pixels* unless the name says `logical`.
Qt reports per-screen geometry in logical pixels and a per-screen devicePixelRatio;
Win32 reports everything in physical pixels. Every conversion between the two worlds
lives in this module so the risky arithmetic is testable without a display.
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class Rect:
    """Axis aligned rectangle. `left/top` may be negative on virtual desktops."""

    left: int
    top: int
    right: int
    bottom: int

    @property
    def width(self) -> int:
        return self.right - self.left

    @property
    def height(self) -> int:
        return self.bottom - self.top

    @property
    def empty(self) -> bool:
        return self.width <= 0 or self.height <= 0

    @property
    def center(self) -> tuple[int, int]:
        return (self.left + self.right) // 2, (self.top + self.bottom) // 2

    def contains(self, x: int, y: int) -> bool:
        return self.left <= x < self.right and self.top <= y < self.bottom

    def shifted(self, dx: int, dy: int) -> "Rect":
        return Rect(self.left + dx, self.top + dy, self.right + dx, self.bottom + dy)

    def as_tuple(self) -> tuple[int, int, int, int]:
        return self.left, self.top, self.width, self.height


@dataclass(frozen=True)
class Monitor:
    """One physical display.

    `physical` is the Win32 monitor rectangle in virtual-desktop coordinates.
    `scale` is the Qt devicePixelRatio of that screen (1.0 == 100%, 1.5 == 150%).
    """

    name: str
    physical: Rect
    scale: float = 1.0

    def __post_init__(self) -> None:
        if self.scale <= 0:
            raise ValueError(f"monitor {self.name}: scale must be positive, got {self.scale}")

    @property
    def physical_size(self) -> tuple[int, int]:
        return self.physical.width, self.physical.height

    def contains_point(self, x: int, y: int) -> bool:
        return self.physical.contains(x, y)

    def contains_rect(self, rect: Rect) -> bool:
        return (
            rect.left >= self.physical.left
            and rect.top >= self.physical.top
            and rect.right <= self.physical.right
            and rect.bottom <= self.physical.bottom
        )

    def to_logical(self, rect: Rect) -> Rect:
        """Win32 physical rect -> Qt logical rect local to this monitor's origin."""
        return Rect(
            round((rect.left - self.physical.left) / self.scale),
            round((rect.top - self.physical.top) / self.scale),
            round((rect.right - self.physical.left) / self.scale),
            round((rect.bottom - self.physical.top) / self.scale),
        )

    def to_physical(self, rect: Rect) -> Rect:
        """Qt logical rect local to this monitor's origin -> Win32 physical rect."""
        return Rect(
            self.physical.left + round(rect.left * self.scale),
            self.physical.top + round(rect.top * self.scale),
            self.physical.left + round(rect.right * self.scale),
            self.physical.top + round(rect.bottom * self.scale),
        )

    def clamp(self, rect: Rect) -> Rect:
        """Keep `rect` inside the monitor, preserving its size whenever it fits."""
        if self.physical.empty:
            return rect
        width = min(rect.width, self.physical.width)
        height = min(rect.height, self.physical.height)
        left = min(max(rect.left, self.physical.left), self.physical.right - width)
        top = min(max(rect.top, self.physical.top), self.physical.bottom - height)
        return Rect(left, top, left + width, top + height)

    def scale_point(self, x: int, y: int) -> tuple[float, float]:
        """Physical point -> logical point local to this monitor."""
        return (
            (x - self.physical.left) / self.scale,
            (y - self.physical.top) / self.scale,
        )


@dataclass(frozen=True)
class Layout:
    """An immutable snapshot of every attached display."""

    monitors: tuple[Monitor, ...] = field(default_factory=tuple)

    @property
    def virtual(self) -> Rect:
        """Bounding box of the whole virtual desktop, in physical pixels."""
        if not self.monitors:
            return Rect(0, 0, 0, 0)
        return Rect(
            min(m.physical.left for m in self.monitors),
            min(m.physical.top for m in self.monitors),
            max(m.physical.right for m in self.monitors),
            max(m.physical.bottom for m in self.monitors),
        )

    def monitor_at(self, x: int, y: int) -> Monitor | None:
        """Monitor containing a physical point, or None when the point is off-desktop."""
        for monitor in self.monitors:
            if monitor.contains_point(x, y):
                return monitor
        return None

    def monitor_nearest(self, rect: Rect) -> Monitor | None:
        """Monitor that best holds `rect`: the one it sits on, else the nearest by distance.

        Used after a monitor is unplugged or a resolution shrinks: the pet must land on a
        screen that still exists instead of staying at now-invalid virtual coordinates.
        """
        if not self.monitors:
            return None
        for monitor in self.monitors:
            if monitor.contains_rect(rect):
                return monitor
        return min(self.monitors, key=lambda m: _rect_distance(m.physical, rect))


def _rect_distance(a: Rect, b: Rect) -> int:
    dx = max(a.left - b.right, b.left - a.right, 0)
    dy = max(a.top - b.bottom, b.top - a.bottom, 0)
    return dx * dx + dy * dy


def clamp_during_drag(
    pointer: tuple[int, int],
    offset: tuple[int, int],
    size: tuple[int, int],
    layout: Layout,
) -> Rect:
    """Where the window lands while dragging.

    The pointer may leave every monitor (gap between screens, edge of the virtual desktop).
    We keep the grab offset, then clamp into the nearest monitor so the pet can never be
    dropped where it could not be clicked again.
    """
    width, height = size
    left = pointer[0] - offset[0]
    top = pointer[1] - offset[1]
    rect = Rect(left, top, left + width, top + height)
    monitor = layout.monitor_nearest(rect)
    return monitor.clamp(rect) if monitor is not None else rect


def reanchor(physical: Rect, layout: Layout, logical_size: tuple[int, int]) -> Rect:
    """Re-seat the window after a monitor / resolution / DPI change.

    `logical_size` is the size the user configured, in Qt logical pixels. The pet keeps
    that *logical* size so a 150% screen shows the pet at the same apparent size as 100%,
    and it is clamped into whatever monitor is nearest to where it used to be.
    """
    if not layout.monitors:
        return physical
    monitor = layout.monitor_nearest(physical)
    assert monitor is not None
    target_w, target_h = (
        round(logical_size[0] * monitor.scale),
        round(logical_size[1] * monitor.scale),
    )
    sized = Rect(physical.left, physical.top, physical.left + target_w, physical.top + target_h)
    return monitor.clamp(sized)


def cursor_norm(position: tuple[float, float], monitor: Monitor) -> tuple[float, float]:
    """Normalised cursor position over a monitor, in [-1, 1], for look-at targets."""
    x, y = monitor.scale_point(int(position[0]), int(position[1]))
    width, height = monitor.physical_size
    nx = (x / max(1.0, width / monitor.scale)) * 2.0 - 1.0
    ny = (y / max(1.0, height / monitor.scale)) * 2.0 - 1.0
    return max(-1.0, min(1.0, nx)), max(-1.0, min(1.0, ny))