import unittest

from wallpaper_parallax.motion import Motion, MotionSettings


class MotionTest(unittest.TestCase):
    def test_spring_retains_velocity_and_position_on_reversal(self):
        settings = MotionSettings("spring", 0.34, 55)
        motion = Motion(0, 0, settings)
        motion.retarget(1, 0, settings)
        mid, velocity = motion.sample(0.08)
        self.assertGreater(velocity, 0)
        motion.retarget(0, 0.08, settings)
        self.assertAlmostEqual(motion.sample(0.08)[0], mid)
        self.assertAlmostEqual(motion.sample(0.08)[1], velocity)
        self.assertGreater(motion.sample(0.09)[0], mid)
        self.assertEqual(motion.sample(3), (0, 0))

    def test_inertia_changes_overshoot(self):
        def peak(inertia):
            settings = MotionSettings("spring", 0.34, inertia)
            motion = Motion(0, 0, settings)
            motion.retarget(1, 0, settings)
            return max(motion.sample(i / 1000)[0] for i in range(1000))

        self.assertGreater(peak(100), 1.15)
        self.assertGreater(peak(55), peak(0) + 0.04)

    def test_reduced_motion_lands_immediately(self):
        moving = MotionSettings("spring", 0.34, 100)
        motion = Motion(0, 0, moving)
        motion.retarget(1, 0, moving)
        motion.retarget(1, 0.1, MotionSettings("instant", 0.34, 100))
        self.assertEqual(motion.sample(0.1), (1, 0))


if __name__ == "__main__":
    unittest.main()
