"""Verify ownership across the new session used by actual ROS node launches."""
import importlib.util
import os
from pathlib import Path
import subprocess
import sys
import unittest

spec = importlib.util.spec_from_file_location('local_ros_smoke', Path(__file__).parents[1] / 'ros_smoke.py')
smoke = importlib.util.module_from_spec(spec)
spec.loader.exec_module(smoke)


class OwnershipTests(unittest.TestCase):
    @unittest.skipUnless(sys.platform.startswith('linux'), 'Linux /proc ownership check')
    def test_child_in_new_session_is_owned_but_parent_is_not(self):
        child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(10)'], start_new_session=True)
        try:
            self.assertTrue(smoke.is_descendant(child.pid, os.getpid()))
            self.assertFalse(smoke.is_descendant(os.getpid(), child.pid))
        finally:
            child.terminate()
            child.wait(timeout=3)
