"""Unit tests for the canvas drawing load test script."""

import unittest
from testing.e2e.load_canvas_drawing import parse_args, summarize_trials


class TestLoadCanvasDrawing(unittest.TestCase):

    def test_parse_args_defaults(self):
        args = parse_args([])
        self.assertEqual(args.viewers, 10)
        self.assertEqual(args.trials, 1)
        self.assertEqual(args.strokes_per_trial, 1)
        self.assertEqual(args.port, 0)
        self.assertEqual(args.timeout_seconds, 30.0)
        self.assertEqual(args.hold_seconds, 0.0)
        self.assertEqual(args.launch_interval_seconds, 0.0)
        self.assertTrue(args.headless)

    def test_parse_args_custom(self):
        args = parse_args([
            "--viewers", "50",
            "--trials", "3",
            "--strokes-per-trial", "4",
            "--port", "9000",
            "--timeout-seconds", "45",
            "--hold-seconds", "5",
            "--launch-interval-seconds", "0.25",
            "--no-headless",
        ])
        self.assertEqual(args.viewers, 50)
        self.assertEqual(args.trials, 3)
        self.assertEqual(args.strokes_per_trial, 4)
        self.assertEqual(args.port, 9000)
        self.assertEqual(args.timeout_seconds, 45.0)
        self.assertEqual(args.hold_seconds, 5.0)
        self.assertEqual(args.launch_interval_seconds, 0.25)
        self.assertFalse(args.headless)

    def test_parse_args_validation(self):
        invalid_cases = [
            ["--viewers", "0"],
            ["--trials", "0"],
            ["--strokes-per-trial", "0"],
            ["--port", "-1"],
            ["--port", "70000"],
            ["--timeout-seconds", "0"],
            ["--hold-seconds", "-1"],
            ["--launch-interval-seconds", "-0.5"],
        ]
        for case in invalid_cases:
            with self.subTest(case=case):
                with self.assertRaises(SystemExit):
                    parse_args(case)

    def test_summarize_trials_success(self):
        trial1 = {
            "trial": 1,
            "strokes": [
                {
                    "stroke": 1,
                    "server_ack_latency_ms": 10.0,
                    "viewer_observations": [
                        {"username": "v1", "render_latency_ms": 15.0, "delivery_latency_ms": 12.0},
                        {"username": "v2", "render_latency_ms": 25.0, "delivery_latency_ms": 20.0},
                    ],
                }
            ],
            "mean_render_latency_ms": 20.0,
            "min_render_latency_ms": 15.0,
            "max_render_latency_ms": 25.0,
            "mean_delivery_latency_ms": 16.0,
            "mean_server_ack_latency_ms": 10.0,
        }
        trial2 = {
            "trial": 2,
            "strokes": [
                {
                    "stroke": 1,
                    "server_ack_latency_ms": 8.0,
                    "viewer_observations": [
                        {"username": "v1", "render_latency_ms": 12.0, "delivery_latency_ms": 10.0},
                        {"username": "v2", "render_latency_ms": 18.0, "delivery_latency_ms": 14.0},
                    ],
                }
            ],
            "mean_render_latency_ms": 15.0,
            "min_render_latency_ms": 12.0,
            "max_render_latency_ms": 18.0,
            "mean_delivery_latency_ms": 12.0,
            "mean_server_ack_latency_ms": 8.0,
        }
        summary = summarize_trials([trial1, trial2])
        self.assertEqual(summary["successful_trials"], 2)
        self.assertEqual(summary["total_viewer_observations"], 4)
        # All render latencies: 15, 25, 12, 18 -> mean = 17.5
        self.assertEqual(summary["mean_render_latency_ms"], 17.5)
        self.assertEqual(summary["min_render_latency_ms"], 12.0)
        self.assertEqual(summary["max_render_latency_ms"], 25.0)
        # Delivery: 12, 20, 10, 14 -> mean = 14.0
        self.assertEqual(summary["mean_delivery_latency_ms"], 14.0)
        # Ack: 10, 8 -> mean = 9.0
        self.assertEqual(summary["mean_server_ack_latency_ms"], 9.0)

    def test_summarize_trials_with_failures(self):
        trials = [
            {"trial": 1, "error": "Connection lost"},
            {
                "trial": 2,
                "strokes": [
                    {
                        "stroke": 1,
                        "server_ack_latency_ms": 5.0,
                        "viewer_observations": [
                            {"username": "v1", "render_latency_ms": 8.0, "delivery_latency_ms": 6.0},
                        ],
                    }
                ],
                "mean_render_latency_ms": 8.0,
            },
        ]
        summary = summarize_trials(trials)
        self.assertEqual(summary["successful_trials"], 1)
        self.assertEqual(summary["total_viewer_observations"], 1)
        self.assertEqual(summary["mean_render_latency_ms"], 8.0)
        self.assertEqual(summary["min_render_latency_ms"], 8.0)
        self.assertEqual(summary["max_render_latency_ms"], 8.0)
        self.assertEqual(summary["render_latency_stdev_ms"], 0.0)


if __name__ == "__main__":
    unittest.main()
