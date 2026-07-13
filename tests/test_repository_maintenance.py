from __future__ import annotations

import shutil
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SAFETY_SCRIPT = ROOT / "scripts" / "check_repository_safety.sh"
DAILY_SCRIPT = ROOT / "scripts" / "review_daily_update.sh"


def run(command: list[str], cwd: Path, *, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(command, cwd=cwd, check=check, capture_output=True, text=True)


def initialize_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    run(["git", "init", "-q"], repo)
    run(["git", "config", "user.email", "maintenance-tests@example.invalid"], repo)
    run(["git", "config", "user.name", "Maintenance Tests"], repo)
    (repo / "scripts").mkdir()
    return repo


def install_script(source: Path, repo: Path) -> Path:
    target = repo / "scripts" / source.name
    shutil.copy2(source, target)
    target.chmod(0o755)
    return target


def test_safety_script_rejects_tracked_dotenv(tmp_path: Path):
    repo = initialize_repo(tmp_path)
    script = install_script(SAFETY_SCRIPT, repo)
    (repo / ".env").write_text("SAFE_PLACEHOLDER=\n", encoding="utf-8")
    run(["git", "add", "-f", ".env"], repo)

    result = run([str(script)], repo, check=False)

    assert result.returncode != 0
    assert ".env" in result.stdout
    assert "prohibited path" in result.stdout.lower()


def test_safety_script_rejects_staged_private_key(tmp_path: Path):
    repo = initialize_repo(tmp_path)
    script = install_script(SAFETY_SCRIPT, repo)
    secret = "-----BEGIN " + "PRIVATE KEY-----\nnot-a-real-key\n-----END PRIVATE KEY-----\n"
    (repo / "credential.txt").write_text(secret, encoding="utf-8")
    run(["git", "add", "credential.txt"], repo)

    result = run([str(script), "--staged"], repo, check=False)

    assert result.returncode != 0
    assert "credential.txt" in result.stdout
    assert "private key" in result.stdout.lower()
    assert "not-a-real-key" not in result.stdout


def test_safety_script_accepts_safe_env_example(tmp_path: Path):
    repo = initialize_repo(tmp_path)
    script = install_script(SAFETY_SCRIPT, repo)
    (repo / ".env.example").write_text(
        "OPENAI_API_KEY=\nSPORTTERY_ENABLE_LIVE=0\n",
        encoding="utf-8",
    )
    run(["git", "add", ".env.example"], repo)

    result = run([str(script), "--staged"], repo, check=False)

    assert result.returncode == 0, result.stdout + result.stderr


def test_safety_script_warns_for_file_over_50_mb(tmp_path: Path):
    repo = initialize_repo(tmp_path)
    script = install_script(SAFETY_SCRIPT, repo)
    large_file = repo / "large-public-data.bin"
    with large_file.open("wb") as handle:
        handle.truncate(51 * 1024 * 1024)

    result = run([str(script)], repo, check=False)

    assert result.returncode == 0, result.stdout + result.stderr
    assert "large-public-data.bin" in result.stdout
    assert "large file warning" in result.stdout.lower()


def test_daily_review_script_never_invokes_git_mutation_commands():
    content = DAILY_SCRIPT.read_text(encoding="utf-8")

    assert "git add" not in content
    assert "git commit" not in content
    assert "git push" not in content


def test_daily_review_script_reports_code_and_data_changes_separately():
    content = DAILY_SCRIPT.read_text(encoding="utf-8")

    assert "Code and configuration changes" in content
    assert "Data changes" in content


def test_daily_review_script_runs_safety_check_before_tests():
    content = DAILY_SCRIPT.read_text(encoding="utf-8")

    assert content.index("check_repository_safety.sh") < content.index("pytest")
