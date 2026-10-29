from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from scripts.secret_scan import scan


def test_scanner_detects_credentials_and_private_documents(tmp_path: Path):
    token = "ghp_" + "A" * 36
    (tmp_path/"source.py").write_text(f'key="{token}"')
    (tmp_path/"resume.pdf").write_bytes(b"%PDF-private")
    findings=scan(tmp_path)
    assert len(findings) == 2
    assert all(token not in finding for finding in findings)
