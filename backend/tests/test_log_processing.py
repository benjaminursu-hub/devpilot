"""Unit tests for log extraction, truncation and secret redaction (pure functions)."""

import io
import zipfile

import pytest

from app.errors import LogArchiveError
from app.log_processing import combine_log_files, extract_log_files, truncate_keep_tail
from app.redaction import REDACTED, redact_secrets


def _zip(files: dict[str, str]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as zf:
        for path, content in files.items():
            zf.writestr(path, content)
    return buffer.getvalue()


def test_prefers_top_level_job_files_in_numeric_order():
    archive = _zip(
        {
            "10_deploy.txt": "deploy\n",
            "2_test.txt": "test\n",
            "0_build.txt": "build\n",
            "build/1_Checkout.txt": "duplicate\n",
        }
    )
    files, cut = extract_log_files(archive, keep_bytes_per_file=10_000)
    assert [f.name for f in files] == ["build", "test", "deploy"]
    assert cut is False


def test_falls_back_to_step_files():
    archive = _zip({"build/2_Compile.txt": "b\n", "build/1_Checkout.txt": "a\n"})
    files, _ = extract_log_files(archive, keep_bytes_per_file=10_000)
    assert combine_log_files(files) == (
        "===== build / Checkout =====\na\n\n===== build / Compile =====\nb\n"
    )


def test_keeps_only_tail_of_huge_file():
    archive = _zip({"0_build.txt": "x" * 50_000 + "END"})
    files, cut = extract_log_files(archive, keep_bytes_per_file=1_000)
    assert cut is True
    assert len(files[0].text) == 1_000 and files[0].text.endswith("END")


def test_invalid_zip():
    with pytest.raises(LogArchiveError):
        extract_log_files(b"nope", keep_bytes_per_file=1_000)


def test_truncate_keep_tail():
    text = "\n".join(f"line {i}" for i in range(1_000))
    short, cut = truncate_keep_tail(text, 2_000)
    assert cut is True
    assert short.endswith("line 999")
    assert short.split("\n")[1].startswith("line ")  # starts on a whole line
    assert truncate_keep_tail("small", 2_000) == ("small", False)


@pytest.mark.parametrize(
    "secret_line",
    [
        "token ghp_" + "A" * 36,
        "github_pat_" + "B" * 40,
        "AKIAABCDEFGHIJKLMNOP",
        "xoxb-1234567890-abcdefghij",
        "key sk-ant-api03-" + "c" * 30,
        "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTYifQ.abcdefghijklmnop",
        "-----BEGIN RSA PRIVATE KEY-----\nMIIabc\n-----END RSA PRIVATE KEY-----",
    ],
)
def test_redacts_known_formats(secret_line):
    redacted = redact_secrets(secret_line)
    assert REDACTED in redacted
    assert secret_line not in redacted


def test_redacts_credentials_in_urls_and_assignments():
    text = (
        "git clone https://bot:s3cretpass@github.com/o/r.git\n"
        "Authorization: Bearer abc.def.ghi\n"
        'DB_PASSWORD="correcthorsebattery"\n'
    )
    redacted = redact_secrets(text)
    assert "s3cretpass" not in redacted
    assert "abc.def.ghi" not in redacted
    assert "correcthorsebattery" not in redacted
    assert "github.com/o/r.git" in redacted  # the useful part survives


def test_leaves_normal_log_lines_alone():
    line = "Compilation failed: package example does not exist"
    assert redact_secrets(line) == line
