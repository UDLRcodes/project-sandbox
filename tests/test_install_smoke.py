import subprocess
import sys


def test_module_compiles():
    result = subprocess.run([sys.executable, "-m", "py_compile", "project_sandbox.py"])
    assert result.returncode == 0
