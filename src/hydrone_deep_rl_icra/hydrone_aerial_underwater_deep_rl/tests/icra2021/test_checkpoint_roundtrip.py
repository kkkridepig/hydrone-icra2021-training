import sys
import tempfile
import unittest
from pathlib import Path

import torch

SRC = Path(__file__).resolve().parents[2] / "src"
sys.path.insert(0, str(SRC))

from hydrone_icra2021.networks import PaperActor  # noqa: E402


class CheckpointRoundtripTests(unittest.TestCase):
    def test_state_dict_roundtrip_max_diff_zero(self):
        torch.manual_seed(11)
        model = PaperActor()
        state = torch.randn(2, 26)
        expected = model(state).detach()

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "actor_state.pt"
            torch.save(model.state_dict(), path)
            restored = PaperActor()
            restored.load_state_dict(
                torch.load(path, map_location="cpu", weights_only=True)
            )
            actual = restored(state).detach()

        max_diff = torch.max(torch.abs(expected - actual)).item()
        self.assertEqual(max_diff, 0.0)


if __name__ == "__main__":
    unittest.main()
