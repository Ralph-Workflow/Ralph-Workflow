import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from agents.marketing import send_curator_email


class SendCuratorEmailTests(unittest.TestCase):
    def test_from_header_handles_display_names_with_at_symbol(self):
        with tempfile.TemporaryDirectory() as tmp:
            body = Path(tmp) / "body.txt"
            body.write_text("hello", encoding="utf-8")
            log_dir = Path(tmp) / "logs"
            log_dir.mkdir()

            with patch.object(send_curator_email, "LOG_DIR", log_dir), \
                 patch("sys.argv", [
                     "send_curator_email.py",
                     "--to", "info@example.com",
                     "--subject", "Test",
                     "--body-file", str(body),
                     "--from-name", "Ken @ Ralph Workflow",
                     "--from-email", "ken@example.com",
                     "--dry-run",
                 ]):
                rc = send_curator_email.main()

            self.assertEqual(rc, 0)
            logs = list(log_dir.glob("marketing_*_curator_email.json"))
            self.assertEqual(len(logs), 1)


if __name__ == "__main__":
    unittest.main()
