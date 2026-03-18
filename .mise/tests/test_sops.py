"""Tests for swarm._sops — SOPS decryption helpers."""

import base64

import pytest

from swarm import SopsError
from swarm._sops import sops_decrypt


class TestSopsDecrypt:
    def test_dotenv_format(self, mock_subprocess):
        mock_subprocess.return_value.stdout = "DB_PASS=secret123\nAPI_KEY=abc\n"
        mock_subprocess.return_value.returncode = 0
        result = sops_decrypt("stack/secrets.env")
        assert result == [("DB_PASS", "secret123"), ("API_KEY", "abc")]
        # Should NOT have --output-type dotenv for .env files
        cmd = mock_subprocess.call_args[0][0]
        assert "--output-type" not in cmd

    def test_yaml_format(self, mock_subprocess):
        mock_subprocess.return_value.stdout = "DOMAIN=example.com\nOIDC_URL=https://auth\n"
        mock_subprocess.return_value.returncode = 0
        result = sops_decrypt("secrets/dev.yaml")
        assert result == [("DOMAIN", "example.com"), ("OIDC_URL", "https://auth")]
        cmd = mock_subprocess.call_args[0][0]
        assert "--output-type" in cmd
        assert "dotenv" in cmd

    def test_yml_extension(self, mock_subprocess):
        mock_subprocess.return_value.stdout = "KEY=val\n"
        mock_subprocess.return_value.returncode = 0
        sops_decrypt("secrets/shared.yml")
        cmd = mock_subprocess.call_args[0][0]
        assert "--output-type" in cmd

    def test_b64_suffix_decoded(self, mock_subprocess):
        raw_value = "multi-line\nencoded\nvalue"
        encoded = base64.b64encode(raw_value.encode()).decode()
        mock_subprocess.return_value.stdout = f"OIDC_KEY_B64={encoded}\n"
        mock_subprocess.return_value.returncode = 0
        result = sops_decrypt("stack/secrets.env")
        assert len(result) == 1
        key, value = result[0]
        assert key == "OIDC_KEY"  # _B64 stripped
        assert value == raw_value

    def test_filters_comments(self, mock_subprocess):
        mock_subprocess.return_value.stdout = "# comment\nKEY=val\n\n"
        mock_subprocess.return_value.returncode = 0
        result = sops_decrypt("stack/secrets.env")
        assert result == [("KEY", "val")]

    def test_filters_sops_metadata(self, mock_subprocess):
        mock_subprocess.return_value.stdout = "sops_version=3.7\nKEY=val\nsops_age=xxx\n"
        mock_subprocess.return_value.returncode = 0
        result = sops_decrypt("stack/secrets.env")
        assert result == [("KEY", "val")]

    def test_decrypt_failure(self, mock_subprocess):
        mock_subprocess.return_value.returncode = 1
        mock_subprocess.return_value.stderr = "could not decrypt"
        with pytest.raises(SopsError, match="Failed to decrypt"):
            sops_decrypt("stack/secrets.env")

    def test_bad_b64_raises(self, mock_subprocess):
        mock_subprocess.return_value.stdout = "KEY_B64=not-valid-base64!!!\n"
        mock_subprocess.return_value.returncode = 0
        with pytest.raises(SopsError, match="base64 decode failed"):
            sops_decrypt("stack/secrets.env")

    def test_empty_output(self, mock_subprocess):
        mock_subprocess.return_value.stdout = ""
        mock_subprocess.return_value.returncode = 0
        assert sops_decrypt("stack/secrets.env") == []

    def test_value_with_equals(self, mock_subprocess):
        mock_subprocess.return_value.stdout = "DB_URL=postgres://user:pass@host/db?opt=1\n"
        mock_subprocess.return_value.returncode = 0
        result = sops_decrypt("stack/secrets.env")
        assert result == [("DB_URL", "postgres://user:pass@host/db?opt=1")]
