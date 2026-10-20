import os
import sys
import re
import ast
import traceback
import subprocess
import importlib
from pathlib import Path

ROOT = Path(__file__).resolve().parent
os.chdir(ROOT)

print("\n" + "=" * 70)
print(" WEBSITE 500 ERROR DIAGNOSTIC")
print("=" * 70)

errors = []
warnings = []


def ok(msg):
    print(f"[OK] {msg}")


def fail(msg):
    print(f"[ERROR] {msg}")
    errors.append(msg)


def warn(msg):
    print(f"[WARNING] {msg}")
    warnings.append(msg)


def section(title):
    print("\n" + "-" * 70)
    print(title)
    print("-" * 70)


section("1. PYTHON ENVIRONMENT")

print(f"Python: {sys.version}")
print(f"Executable: {sys.executable}")
print(f"Working directory: {ROOT}")

try:
    result = subprocess.run(
        [sys.executable, "-m", "pip", "--version"],
        capture_output=True,
        text=True
    )
    if result.returncode == 0:
        ok(result.stdout.strip())
    else:
        fail("pip is not working")
except Exception as e:
    fail(f"Could not check pip: {e}")


section("2. PROJECT FILES")

important_files = [
    "app.py",
    "requirements.txt",
    ".env"
]

for filename in important_files:
    path = ROOT / filename

    if path.exists():
        ok(f"{filename} exists")
    else:
        warn(f"{filename} not found")


section("3. REQUIREMENTS CHECK")

requirements_file = ROOT / "requirements.txt"

if requirements_file.exists():
    try:
        requirements = []

        for line in requirements_file.read_text(
            encoding="utf8",
            errors="ignore"
        ).splitlines():
            line = line.strip()

            if not line or line.startswith("#"):
                continue

            package = re.split(r"[<>=!~\[]", line)[0].strip()

            if package:
                requirements.append(package)

        try:
            from importlib.metadata import distributions

            installed = {
                dist.metadata["Name"].lower()
                for dist in distributions()
                if dist.metadata["Name"]
            }

            for package in requirements:
                normalized = package.lower().replace("_", "-")

                if normalized in installed:
                    ok(f"Installed: {package}")
                else:
                    fail(f"Missing package: {package}")

        except Exception as e:
            warn(f"Could not fully inspect installed packages: {e}")

    except Exception as e:
        fail(f"Could not read requirements.txt: {e}")

else:
    warn("Skipping requirements check because requirements.txt does not exist")


section("4. ENVIRONMENT VARIABLES")

env_file = ROOT / ".env"

if env_file.exists():
    try:
        env_keys = []

        for line in env_file.read_text(
            encoding="utf8",
            errors="ignore"
        ).splitlines():
            line = line.strip()

            if not line or line.startswith("#") or "=" not in line:
                continue

            key = line.split("=", 1)[0].strip()
            env_keys.append(key)

        if env_keys:
            for key in env_keys:
                value = os.getenv(key)

                if value:
                    ok(f"Environment variable available: {key}")
                else:
                    warn(f"Environment variable not loaded yet: {key}")

            print("\n.env contains:")
            for key in env_keys:
                print(f"  {key}=***")

        else:
            warn(".env exists but no variables were found")

    except Exception as e:
        fail(f"Could not read .env: {e}")

else:
    warn(".env file not found")


section("5. PYTHON FILE SYNTAX CHECK")

python_files = list(ROOT.rglob("*.py"))

if not python_files:
    fail("No Python files found")

for file in python_files:
    try:
        source = file.read_text(
            encoding="utf8",
            errors="ignore"
        )

        ast.parse(source, filename=str(file))
        ok(f"Syntax valid: {file.relative_to(ROOT)}")

    except SyntaxError as e:
        fail(
            f"Syntax error in {file.relative_to(ROOT)} "
            f"line {e.lineno}: {e.msg}"
        )

    except Exception as e:
        fail(f"Could not inspect {file.relative_to(ROOT)}: {e}")


section("6. IMPORT CHECK")

stdlib_modules = {
    "os",
    "sys",
    "re",
    "ast",
    "json",
    "time",
    "math",
    "datetime",
    "pathlib",
    "traceback",
    "subprocess",
    "threading",
    "typing",
    "functools",
    "collections",
    "itertools",
    "hashlib",
    "secrets",
    "uuid",
    "random",
    "logging",
    "urllib",
    "http",
    "email",
    "sqlite3"
}

imports = set()

for file in python_files:
    try:
        tree = ast.parse(
            file.read_text(encoding="utf8", errors="ignore"),
            filename=str(file)
        )

        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for name in node.names:
                    imports.add(name.name.split(".")[0])

            elif isinstance(node, ast.ImportFrom):
                if node.module:
                    imports.add(node.module.split(".")[0])

    except Exception:
        pass


for module in sorted(imports):
    if module in stdlib_modules:
        continue

    local_module = (
        (ROOT / f"{module}.py").exists()
        or (ROOT / module).is_dir()
    )

    if local_module:
        ok(f"Local import found: {module}")
        continue

    try:
        importlib.import_module(module)
        ok(f"Import works: {module}")

    except Exception as e:
        fail(f"Import failed: {module} -> {type(e).__name__}: {e}")


section("7. FLASK APP LOAD TEST")

app_file = ROOT / "app.py"

if app_file.exists():
    try:
        ok("Trying to import app.py...")

        spec = importlib.util.spec_from_file_location(
            "website_app",
            app_file
        )

        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)

        if hasattr(module, "app"):
            ok("Flask app object found: app")

            flask_app = module.app

            try:
                with flask_app.app_context():
                    ok("Flask application context works")
            except Exception as e:
                fail(
                    f"Flask app context failed: "
                    f"{type(e).__name__}: {e}"
                )
                traceback.print_exc()

        else:
            warn(
                "app.py imported successfully but no variable named 'app' was found"
            )

    except Exception as e:
        fail(
            f"app.py IMPORT FAILED: "
            f"{type(e).__name__}: {e}"
        )

        print("\nFULL TRACEBACK:")
        traceback.print_exc()

else:
    fail("app.py not found, cannot test Flask app")


section("8. FLASK TEST REQUEST")

try:
    if "flask_app" in locals():

        flask_app.testing = True

        with flask_app.test_client() as client:
            response = client.get("/")

            print(f"Homepage status: {response.status_code}")

            if response.status_code >= 500:
                fail(
                    f"Homepage returned HTTP {response.status_code}"
                )

                print("\nRESPONSE PREVIEW:")
                print(
                    response.get_data(as_text=True)[:2000]
                )

            else:
                ok(
                    f"Homepage responded successfully with HTTP "
                    f"{response.status_code}"
                )

except Exception as e:
    fail(
        f"Homepage test caused exception: "
        f"{type(e).__name__}: {e}"
    )

    print("\nFULL TRACEBACK:")
    traceback.print_exc()


section("FINAL RESULT")

if errors:
    print(f"\nFOUND {len(errors)} ERROR(S):\n")

    for i, error in enumerate(errors, 1):
        print(f"{i}. {error}")

else:
    print("\nNO CRITICAL ERRORS FOUND")

if warnings:
    print(f"\nWARNINGS: {len(warnings)}")

    for i, warning in enumerate(warnings, 1):
        print(f"{i}. {warning}")

print("\n" + "=" * 70)
print("DIAGNOSTIC COMPLETE")
print("=" * 70)