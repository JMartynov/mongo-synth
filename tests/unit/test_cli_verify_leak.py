import json
import pytest
import sys
from unittest.mock import MagicMock
from mongo_synth.cli import run_verify_leak

def test_verify_leak_secure(tmp_path):
    # 1. Create a dummy verifier file
    verifier_file = tmp_path / "verifiers.json"
    verifiers = [
        {"type": "email", "value": "dev_run_leak@example.com"},
        {"type": "api_key", "value": "key_live_dev_run_somehex"}
    ]
    verifier_file.write_text(json.dumps(verifiers))

    # 2. Create a clean target file (no leaks)
    target_file = tmp_path / "clean_log.log"
    target_file.write_text("This is a clean file without any leaks or credentials.\nSome ordinary lines.")

    # Mock the CLI arguments
    args = MagicMock()
    args.verifier_file = str(verifier_file)
    args.target = str(target_file)

    # run_verify_leak exits on success with code 0. We expect SystemExit(0).
    with pytest.raises(SystemExit) as excinfo:
        run_verify_leak(args, parser=None)
    assert excinfo.value.code == 0

def test_verify_leak_with_leaks(tmp_path):
    # 1. Create a dummy verifier file
    verifier_file = tmp_path / "verifiers.json"
    verifiers = [
        {"type": "email", "value": "dev_run_leak@example.com"},
        {"type": "api_key", "value": "key_live_dev_run_somehex"}
    ]
    verifier_file.write_text(json.dumps(verifiers))

    # 2. Create a dirty target file (contains a leak)
    target_file = tmp_path / "dirty_log.log"
    target_file.write_text("Log started...\n[INFO] User logged in: dev_run_leak@example.com\nEnd of log.")

    # Mock the CLI arguments
    args = MagicMock()
    args.verifier_file = str(verifier_file)
    args.target = str(target_file)

    # run_verify_leak exits on failure with code 1. We expect SystemExit(1).
    with pytest.raises(SystemExit) as excinfo:
        run_verify_leak(args, parser=None)
    assert excinfo.value.code == 1

def test_verify_leak_invalid_verifier_file(tmp_path):
    # 1. Create a malformed verifier file
    verifier_file = tmp_path / "verifiers.json"
    verifier_file.write_text("{invalid json")

    args = MagicMock()
    args.verifier_file = str(verifier_file)
    args.target = str(tmp_path)

    with pytest.raises(SystemExit) as excinfo:
        run_verify_leak(args, parser=None)
    assert excinfo.value.code == 1

def test_verify_leak_empty_verifiers(tmp_path):
    verifier_file = tmp_path / "verifiers.json"
    verifier_file.write_text(json.dumps([]))

    args = MagicMock()
    args.verifier_file = str(verifier_file)
    args.target = str(tmp_path)

    with pytest.raises(SystemExit) as excinfo:
        run_verify_leak(args, parser=None)
    assert excinfo.value.code == 0

def test_verify_leak_invalid_target(tmp_path):
    verifier_file = tmp_path / "verifiers.json"
    verifier_file.write_text(json.dumps([{"type": "email", "value": "test@example.com"}]))

    args = MagicMock()
    args.verifier_file = str(verifier_file)
    args.target = str(tmp_path / "non_existent_path")

    with pytest.raises(SystemExit) as excinfo:
        run_verify_leak(args, parser=None)
    assert excinfo.value.code == 1
