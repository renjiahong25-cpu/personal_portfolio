"""How the performance is played, decided once and reused.

Why this is separate from measuring
-----------------------------------
:smod:`services.audio_analysis.dynamics` answers *how loud was this note in the
recording*. This module answers *how should it be played*, and the user was explicit
that the two must not be confused: "弹奏方式的话就要一开始就定下来，因为目前是乐谱都不对".
The rules here are therefore **policy**, not measurement. They do not get re-tuned every
time the score is iterated; changing one is a deliberate act with a reason.

That separation is also what makes the work reusable. A calibration is a small set of
numbers with a version and the measurements that produced it, so a new piece or a new
instrument loads the same profile instead of rediscovering the same mistakes.

The rules, and the measurements that set them
--------------------------------------------
All from 星辰大海, rendered with ``MuseScore_General.sf2``.

**PR-3, the right hand sits slightly above the left.** Measured before: right hand
velocity median **57**, left **77** - the melody was being played 20 units *quieter* than
the accompaniment, which is exactly the "左手太大声导致旋律不明显" complaint. The brief
says "右手比左手大声一点点", so this is a deliberate small offset, not a rebalance: the
melody has to sit on top without the accompaniment disappearing.

**PR-4, no sudden increase in loudness.** Measured before: **36.3 %** of consecutive
notes jumped by 40 or more velocity units, 17.7 % by 60 or more, the largest by 123. A
40-unit velocity step is roughly 15 dB, and hearing that between two neighbouring notes
is what "有时候突然大声有时候又小声" is. Enforced as a slew limit rather than a smoothing
filter, because smoothing would also flatten the accents that are real.

**PR-5, a pianist's usable range, not the whole keyboard.** Measured before: velocity
p1 = 3 and p99 = 127 - essentially the entire range, with 10 % of notes above 110. The
piano's velocity range is wide but a pianist does not use all of it in one piece, and the
extreme ends of a measured distribution are estimation noise rather than intent.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from dataclasses import dataclass, replace

from services.audio_analysis.dynamics import VELOCITY_CEIL, VELOCITY_FLOOR, to_velocity

logger = logging.getLogger(__name__)

#: PR-3. The melody above the accompaniment, by "一点点".
#:
#: Measured as right - left, so a positive value lifts the melody. The pre-change
#: measurement was -20, i.e. the melody was underneath.
RIGHT_HAND_OFFSET = 8

#: PR-4. The largest velocity step permitted between consecutive notes.
#:
#: Measured before: p99 of |dv| was 105 and the maximum 123. 24 is a step of roughly
#: 9 dB, which a listener reads as one note being struck rather than two notes being
#: contrasted, and it is above the natural spread of a real accent (~15 velocity units
#: on the same note repeated).
MAX_VELOCITY_STEP = 24

#: PR-5. The range a performance actually occupies, before any per-note measurement.
#:
#: The defaults in :mod:`dynamics` are the measured ones; these are the same numbers
#: named here so the playback policy can be stated in one place and changed in one place.
DEFAULT_FLOOR = VELOCITY_FLOOR
DEFAULT_CEIL = VELOCITY_CEIL

#: Bumped whenever a rule changes value, so a stored calibration can tell whether it
#: was produced under the same rules it would now be read with.
PROFILE_VERSION = 1


@dataclass(frozen=True)
class ExpressionProfile:
    """The playback rules, as numbers that can be stored and reused."""

    version: int = PROFILE_VERSION
    right_hand_offset: int = RIGHT_HAND_OFFSET
    max_velocity_step: int = MAX_VELOCITY_STEP
    velocity_floor: int = DEFAULT_FLOOR
    velocity_ceiling: int = DEFAULT_CEIL
    #: Whether PR-3/PR-4/PR-5 are applied at all. Kept so a caller can play a score
    #: literally - useful for the round-trip tests, which compare like with like.
    apply_rules: bool = True

    def to_dict(self) -> dict[str, object]:
        return {
            "version": self.version,
            "rightHandOffset": self.right_hand_offset,
            "maxVelocityStep": self.max_velocity_step,
            "velocityFloor": self.velocity_floor,
            "velocityCeiling": self.velocity_ceiling,
            "applyRules": self.apply_rules,
        }

    @classmethod
    def from_dict(cls, payload: dict[str, object]) -> ExpressionProfile:
        base = cls()

        def as_int(key: str, fallback: int) -> int:
            value = payload.get(key, fallback)
            try:
                return int(value)  # type: ignore[call-overload]
            except (TypeError, ValueError):
                return fallback

        return cls(
            version=as_int("version", base.version),
            right_hand_offset=as_int("rightHandOffset", base.right_hand_offset),
            max_velocity_step=as_int("maxVelocityStep", base.max_velocity_step),
            velocity_floor=as_int("velocityFloor", base.velocity_floor),
            velocity_ceiling=as_int("velocityCeiling", base.velocity_ceiling),
            apply_rules=bool(payload.get("applyRules", base.apply_rules)),
        )

    def with_rules_disabled(self) -> ExpressionProfile:
        return replace(self, apply_rules=False)


def limit_velocity_steps(velocities: Sequence[int], max_step: int) -> list[int]:
    """Cap how far the velocity may move between consecutive notes.

    A one-sided limit on the way *up* and a gentler one on the way down, because the
    user's complaint was "不要有突然响度增大的情况" - a sudden increase. A sudden drop is
    audible too but it is what a real diminuendo and a released chord both look like, so
    clamping it as hard would flatten phrasing that is in the performance.

    The limit is applied forwards from the first note, so the very first velocity is
    kept: a piece should start at the loudness the player gave it.
    """
    if not velocities:
        return []
    out = [int(velocities[0])]
    for raw in velocities[1:]:
        previous = out[-1]
        value = int(raw)
        if value - previous > max_step:
            value = previous + max_step
        elif previous - value > max_step * 2:
            value = previous - max_step * 2
        out.append(value)
    return out


def velocities(
    loudness: Sequence[float],
    hands: Sequence[str],
    *,
    profile: ExpressionProfile | None = None,
    times: Sequence[float] | None = None,
) -> list[int]:
    """The MIDI velocity for every note, under the fixed playback rules.

    ``loudness`` is the measured 0..1 per note, ``hands`` is ``"right"`` or ``"left"``,
    and ``times`` orders them for the slew limit (notes are per-hand grouped, so the
    order is not the input order by construction).

    PR-3 is applied before PR-4 on purpose: the hand offset is a deliberate, fixed part
    of the performance, and the slew limit exists to stop measurement noise, so letting
    the limiter also clamp the offset would quietly undo the rule it is supposed to
    protect. The offset is therefore re-applied afterwards as a floor on the melody.
    """
    rules = profile or ExpressionProfile()
    raw = [
        to_velocity(value, floor=rules.velocity_floor, ceiling=rules.velocity_ceiling)
        for value in loudness
    ]
    if not rules.apply_rules:
        return raw

    if rules.right_hand_offset:
        raw = [
            value + rules.right_hand_offset if hand == "right" else value
            for value, hand in zip(raw, hands, strict=True)
        ]
        raw = [min(value, rules.velocity_ceiling) for value in raw]

    order = sorted(range(len(raw)), key=lambda i: times[i] if times else i)
    limited = limit_velocity_steps([raw[i] for i in order], rules.max_velocity_step)
    result = list(raw)
    for slot, index in enumerate(order):
        result[index] = limited[slot]
    return result


__all__ = [
    "MAX_VELOCITY_STEP",
    "PROFILE_VERSION",
    "RIGHT_HAND_OFFSET",
    "ExpressionProfile",
    "limit_velocity_steps",
    "velocities",
]
