import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from deepseek_runner import discover_deepseek, run_deepseek


class DeepSeekRunnerTest(unittest.TestCase):
    def test_explicit_launcher_and_headless_stdin(self):
        with tempfile.TemporaryDirectory() as directory:
            launcher = Path(directory) / "dsh.cmd"
            launcher.touch()
            with patch.dict("os.environ", {"DEEPSEEK_HARNESS_CLI": str(launcher)}):
                self.assertEqual(discover_deepseek(), str(launcher))
            completed = subprocess.CompletedProcess([], 0, "任务完成", "")
            with patch("deepseek_runner.subprocess.run", return_value=completed) as invoked:
                result = run_deepseek(str(launcher), "秘密任务", Path(directory))
            self.assertEqual(result, "任务完成")
            arguments, options = invoked.call_args
            self.assertEqual(arguments[0][-2:], ["never", "-"])
            self.assertEqual(options["input"], "秘密任务")
            self.assertNotIn("秘密任务", arguments[0])


if __name__ == "__main__":
    unittest.main()
