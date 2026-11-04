"""The quality catalog/control surface must not prewarm a CAD kernel."""
import subprocess
import sys


def test_package_import_is_kernel_free():
    result=subprocess.run([sys.executable,'-c',
        "import sys; import aiad_quality.spec, aiad_quality.process, aiad.engine.types; assert not any(n == 'build123d' or n == 'OCP' or n.startswith('OCP.') for n in sys.modules)"],
        text=True,capture_output=True,timeout=10)
    assert result.returncode==0,result.stderr
