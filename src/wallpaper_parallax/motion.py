"""Workspace motion with continuous velocity when a spring is retargeted."""

from dataclasses import dataclass
from math import cos, exp, sin, sqrt

MODES = ("smooth", "spring", "instant")
SPRING_MARGIN = 0.25


@dataclass(frozen=True)
class MotionSettings:
    mode: str
    duration: float
    inertia: int

    @property
    def spring(self):
        return self.mode == "spring" and self.duration > 0


class Motion:
    def __init__(self, position, now, settings):
        self.start = self.target = position
        self.velocity = 0.0
        self.since = now
        self.settings = settings

    def sample(self, now):
        if self.start == self.target and self.velocity == 0:
            return self.target, 0.0
        duration = self.settings.duration
        if duration == 0 or self.settings.mode == "instant":
            return self.target, 0.0
        elapsed = max(0, now - self.since)
        if not self.settings.spring:
            if elapsed >= duration:
                return self.target, 0.0
            t = elapsed / duration
            distance = self.target - self.start
            return self.start + distance * t * t * (3 - 2 * t), distance * 6 * t * (1 - t) / duration

        # Closed-form underdamped spring. The baseline stiffness is the
        # Material slow spatial spring, scaled by the configured duration.
        frequency = sqrt(300) * 0.34 / duration
        damping = 0.9 - 0.004 * self.settings.inertia
        decay = frequency * damping
        oscillation = frequency * sqrt(1 - damping * damping)
        if decay * elapsed > 9:
            return self.target, 0.0

        offset = self.start - self.target
        coefficient = (self.velocity + decay * offset) / oscillation
        cosine, sine = cos(oscillation * elapsed), sin(oscillation * elapsed)
        envelope = exp(-decay * elapsed)
        wave = offset * cosine + coefficient * sine
        position = envelope * wave
        velocity = envelope * (oscillation * (coefficient * cosine - offset * sine) - decay * wave)
        if abs(position) < 0.00025 and abs(velocity) < 0.004:
            return self.target, 0.0
        return self.target + position, velocity

    def retarget(self, target, now, settings):
        if self.target != target or self.settings != settings:
            self.start, velocity = self.sample(now)
            self.velocity = velocity if settings.spring else 0.0
            self.target = target
            self.since = now
            self.settings = settings
