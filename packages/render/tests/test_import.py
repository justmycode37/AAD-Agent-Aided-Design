import subprocess
import sys


def test_import_stays_light():
    code = (
        "import sys, aad_render;"
        "heavy = {'OCP', 'vtk', 'build123d', 'ocp_tessellate'} & set(sys.modules);"
        "sys.exit(f'heavy imports: {heavy}' if heavy else 0)"
    )
    subprocess.run([sys.executable, "-c", code], check=True)
