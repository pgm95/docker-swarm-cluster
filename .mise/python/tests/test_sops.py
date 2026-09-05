"""Tests for swarm._sops — SOPS decryption helpers."""

import pytest

from swarm import SopsError
from swarm._sops import sops_decrypt


class TestSopsDecrypt:
    def test_json_output_type_always(self, mock_subprocess):
        mock_subprocess.return_value.stdout = '{"DB_PASS": "secret123", "API_KEY": "abc"}'
        mock_subprocess.return_value.returncode = 0
        result = sops_decrypt("stack/secrets.sops.yaml")
        assert result == [("DB_PASS", "secret123"), ("API_KEY", "abc")]
        cmd = mock_subprocess.call_args[0][0]
        assert cmd[:4] == ["sops", "decrypt", "--output-type", "json"]
        assert cmd[-1] == "stack/secrets.sops.yaml"

    def test_preserves_document_order(self, mock_subprocess):
        mock_subprocess.return_value.stdout = '{"Z": "1", "A": "2", "M": "3"}'
        mock_subprocess.return_value.returncode = 0
        assert [k for k, _ in sops_decrypt("f.sops.yaml")] == ["Z", "A", "M"]

    def test_multiline_value_intact(self, mock_subprocess):
        pem = "-----BEGIN KEY-----\nabc\ndef\n-----END KEY-----\n"
        mock_subprocess.return_value.stdout = '{"PEM": "-----BEGIN KEY-----\\nabc\\ndef\\n-----END KEY-----\\n"}'
        mock_subprocess.return_value.returncode = 0
        assert sops_decrypt("f.sops.yaml") == [("PEM", pem)]

    def test_scalar_coercion(self, mock_subprocess):
        mock_subprocess.return_value.stdout = (
            '{"NUM": 42, "FLOAT": 1.5, "YES": true, "NO": false, "NONE": null}'
        )
        mock_subprocess.return_value.returncode = 0
        assert sops_decrypt("f.sops.yaml") == [
            ("NUM", "42"),
            ("FLOAT", "1.5"),
            ("YES", "true"),
            ("NO", "false"),
            ("NONE", ""),
        ]

    def test_value_with_equals_and_dollars(self, mock_subprocess):
        mock_subprocess.return_value.stdout = '{"DB_URL": "postgres://u:p@h/db?opt=1", "PW": "pa$$word"}'
        mock_subprocess.return_value.returncode = 0
        assert sops_decrypt("f.sops.yaml") == [
            ("DB_URL", "postgres://u:p@h/db?opt=1"),
            ("PW", "pa$$word"),
        ]

    def test_sops_metadata_key_skipped(self, mock_subprocess):
        mock_subprocess.return_value.stdout = '{"KEY": "v", "sops": {"version": "3.13.3"}}'
        mock_subprocess.return_value.returncode = 0
        assert sops_decrypt("f.sops.yaml") == [("KEY", "v")]

    def test_keeps_user_keys_starting_with_sops(self, mock_subprocess):
        mock_subprocess.return_value.stdout = '{"sops_recipient": "alice", "KEY": "val"}'
        mock_subprocess.return_value.returncode = 0
        assert sops_decrypt("f.sops.yaml") == [("sops_recipient", "alice"), ("KEY", "val")]

    def test_nested_value_raises(self, mock_subprocess):
        mock_subprocess.return_value.stdout = '{"KEY": {"nested": "x"}}'
        mock_subprocess.return_value.returncode = 0
        with pytest.raises(SopsError, match="KEY must be a scalar"):
            sops_decrypt("f.sops.yaml")

    def test_list_value_raises(self, mock_subprocess):
        mock_subprocess.return_value.stdout = '{"KEY": ["a", "b"]}'
        mock_subprocess.return_value.returncode = 0
        with pytest.raises(SopsError, match="got list"):
            sops_decrypt("f.sops.yaml")

    def test_non_mapping_document_raises(self, mock_subprocess):
        mock_subprocess.return_value.stdout = '["a", "b"]'
        mock_subprocess.return_value.returncode = 0
        with pytest.raises(SopsError, match="expected a mapping"):
            sops_decrypt("f.sops.yaml")

    def test_malformed_json_raises(self, mock_subprocess):
        mock_subprocess.return_value.stdout = "KEY=val\n"
        mock_subprocess.return_value.returncode = 0
        with pytest.raises(SopsError, match="Unexpected sops output"):
            sops_decrypt("f.sops.yaml")

    def test_decrypt_failure(self, mock_subprocess):
        mock_subprocess.return_value.returncode = 1
        mock_subprocess.return_value.stderr = "could not decrypt"
        with pytest.raises(SopsError, match="Failed to decrypt"):
            sops_decrypt("f.sops.yaml")

    def test_empty_output(self, mock_subprocess):
        mock_subprocess.return_value.stdout = ""
        mock_subprocess.return_value.returncode = 0
        assert sops_decrypt("f.sops.yaml") == []

    def test_empty_mapping(self, mock_subprocess):
        mock_subprocess.return_value.stdout = "{}"
        mock_subprocess.return_value.returncode = 0
        assert sops_decrypt("f.sops.yaml") == []
