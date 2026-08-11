import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def run_display(*arguments: str) -> subprocess.CompletedProcess[str]:
    environment = os.environ.copy()
    environment["CLANG_MODULE_CACHE_PATH"] = "/tmp/codex-macropad-clang-cache"
    environment["SWIFTPM_MODULECACHE_OVERRIDE"] = "/tmp/codex-macropad-swift-cache"
    return subprocess.run(
        [
            "swift",
            "run",
            "--disable-sandbox",
            "--quiet",
            "MacropadDisplay",
            *arguments,
        ],
        cwd=PROJECT_ROOT,
        text=True,
        capture_output=True,
        env=environment,
        timeout=120,
        check=False,
    )


def encode_packet(data_type: str, text: str) -> subprocess.CompletedProcess[str]:
    return run_display("--encode-only", data_type, text)


class MacropadDisplayCLITests(unittest.TestCase):
    def test_package_exposes_only_direct_hid_products(self) -> None:
        environment = os.environ.copy()
        environment["CLANG_MODULE_CACHE_PATH"] = "/tmp/codex-macropad-clang-cache"
        environment["SWIFTPM_MODULECACHE_OVERRIDE"] = "/tmp/codex-macropad-swift-cache"
        result = subprocess.run(
            ["swift", "package", "--disable-sandbox", "dump-package"],
            cwd=PROJECT_ROOT,
            text=True,
            capture_output=True,
            env=environment,
            timeout=120,
            check=False,
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        manifest = json.loads(result.stdout)
        self.assertEqual(
            [product["name"] for product in manifest["products"]],
            ["CodexMacropadCore", "MacropadDisplay"],
        )

    def test_build_script_produces_executable_helper(self) -> None:
        result = subprocess.run(
            ["/bin/zsh", str(PROJECT_ROOT / "scripts" / "build-helper.sh")],
            cwd=PROJECT_ROOT,
            text=True,
            capture_output=True,
            timeout=120,
            check=False,
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        helper = PROJECT_ROOT / "build" / "MacropadDisplay"
        self.assertTrue(helper.is_file())
        self.assertNotEqual(helper.stat().st_mode & 0o111, 0)

    def test_encodes_artist_as_32_byte_raw_hid_packet(self) -> None:
        result = encode_packet("0xAD", "CODEX")

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), "ad05434f444558" + "00" * 25)

    def test_truncates_title_without_splitting_utf8(self) -> None:
        result = encode_packet("0xAE", "я" * 16)

        self.assertEqual(result.returncode, 0, result.stderr)
        packet = bytes.fromhex(result.stdout.strip())
        self.assertEqual(len(packet), 32)
        self.assertEqual(packet[0], 0xAE)
        self.assertEqual(packet[1], 30)
        self.assertEqual(packet[2:32].decode("utf-8"), "я" * 15)

    def test_encodes_active_state_as_artist_and_title_packets(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            state_path = Path(directory) / "state.json"
            state_path.write_text(
                json.dumps(
                    {
                        "version": 3,
                        "sessions": {
                            "s1": {
                                "workspace": "macropad",
                                "status": "done",
                                "turn_id": "turn-1",
                                "turn_started_at": 4_102_444_546,
                                "updated_at": 4_102_444_700,
                                "expires_at": 4_102_444_800,
                                "active_tools": {},
                                "recent_phase": None,
                                "recent_phase_until": None,
                            }
                        },
                    }
                ),
                encoding="utf-8",
            )

            result = run_display(
                "--state-file",
                str(state_path),
                "--encode-state-once",
            )

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(
            result.stdout.splitlines(),
            [
                "ad1130323a333420c2b7203120414354495645" + "00" * 13,
                "ae0e444f4e453a206d6163726f706164" + "00" * 16,
            ],
        )

    def test_encodes_rgb_profile_without_opening_device(self) -> None:
        result = run_display(
            "--encode-rgb-profile",
            "approval",
            "200",
            "2,6",
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        packet = bytes.fromhex(result.stdout.strip())
        self.assertEqual(len(packet), 32)
        self.assertEqual(packet[:8], bytes([0x07, 0x41, 0x06, 0x00, 128, 28, 255, 140]))
        self.assertNotEqual(packet[0], 0x09)


if __name__ == "__main__":
    unittest.main()
