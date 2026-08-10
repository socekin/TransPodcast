from __future__ import annotations

import unittest

from experiments.pipeline.assemble_baseline import parse_loudnorm_json


class AssembleBaselineTest(unittest.TestCase):
    def test_parses_loudnorm_measurement(self) -> None:
        stderr = """
        [Parsed_loudnorm_0 @ 0x123] {
            "input_i" : "-20.12",
            "input_tp" : "-2.34",
            "input_lra" : "4.50",
            "input_thresh" : "-30.45",
            "output_i" : "-16.02",
            "output_tp" : "-1.01",
            "output_lra" : "4.20",
            "output_thresh" : "-26.31",
            "normalization_type" : "dynamic",
            "target_offset" : "0.02"
        }
        """

        measurement = parse_loudnorm_json(stderr)

        self.assertEqual(measurement["input_i"], "-20.12")
        self.assertEqual(measurement["target_offset"], "0.02")

    def test_rejects_log_without_measurement(self) -> None:
        with self.assertRaisesRegex(
            ValueError,
            "FFmpeg loudness measurement",
        ):
            parse_loudnorm_json("no measurement")


if __name__ == "__main__":
    unittest.main()
