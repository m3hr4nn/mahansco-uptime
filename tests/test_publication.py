"""Exercise publication races using disposable local repositories, never GitHub."""
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

import monitor


class PublicationTests(unittest.TestCase):
    def command(self, *args, cwd=None, check=True):
        return subprocess.run(args, cwd=cwd or self.local, text=True, capture_output=True, check=check)

    def git(self, *args, cwd=None):
        return self.command("git", *args, cwd=cwd).stdout.strip()

    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        root = Path(self.folder.name)
        self.local = root / "local"
        self.remote = root / "remote.git"
        self.peer = root / "peer"
        self.command("git", "init", "--bare", "--initial-branch=main", str(self.remote), cwd=root)
        self.command("git", "clone", str(self.remote), str(self.local), cwd=root)
        self.git("config", "user.name", "Local Test")
        self.git("config", "user.email", "test@example.invalid")
        (self.local / "docs").mkdir()
        for name in ("state.json", "history.json", "uptime_daily.json"):
            (self.local / "docs" / name).write_text('{"original":true}\n')
        self.git("add", ".")
        self.git("commit", "-m", "baseline")
        self.git("push", "origin", "main")
        self.command("git", "clone", str(self.remote), str(self.peer), cwd=root)
        self.git("config", "user.name", "Peer Test", cwd=self.peer)
        self.git("config", "user.email", "test@example.invalid", cwd=self.peer)

    def publish(self):
        env = dict(os.environ, GITHUB_REF_NAME="main")
        return subprocess.run(["bash", str(Path(monitor.ROOT) / "scripts/persist-state.sh")], cwd=self.local,
                              env=env, text=True, capture_output=True)

    def peer_update(self, path, text):
        (self.peer / path).write_text(text)
        self.git("add", path, cwd=self.peer)
        self.git("commit", "-m", "concurrent update", cwd=self.peer)
        self.git("push", "origin", "main", cwd=self.peer)

    def test_clean_publication_and_noop(self):
        (self.local / "docs/state.json").write_text('{"probe":true}\n')
        result = self.publish()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.git("rev-parse", "HEAD"), self.git("rev-parse", "main", cwd=self.remote))
        before = self.git("rev-parse", "HEAD")
        self.assertEqual(self.publish().returncode, 0)
        self.assertEqual(self.git("rev-parse", "HEAD"), before)

    def test_concurrent_source_update_rebases_without_losing_data(self):
        self.peer_update("README.md", "concurrent source update\n")
        (self.local / "docs/state.json").write_text('{"probe":true}\n')
        result = self.publish()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual((self.local / "README.md").read_text(), "concurrent source update\n")
        self.assertEqual(self.git("show", "main:docs/state.json", cwd=self.remote), '{"probe":true}')

    def test_conflicting_data_fails_and_preserves_both_versions(self):
        self.peer_update("docs/state.json", '{"peer":true}\n')
        remote_head = self.git("rev-parse", "main", cwd=self.remote)
        (self.local / "docs/state.json").write_text('{"probe":true}\n')
        result = self.publish()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("No remote data overwritten", result.stdout)
        self.assertEqual(self.git("rev-parse", "main", cwd=self.remote), remote_head)
        self.assertEqual((self.local / "docs/state.json").read_text(), '{"probe":true}\n')
        self.assertFalse((self.local / ".git/rebase-merge").exists())
