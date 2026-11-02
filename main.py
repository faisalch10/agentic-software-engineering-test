import sys
import json
import re
import urllib.request
import urllib.error
from packaging.specifiers import SpecifierSet
from packaging.version import Version, InvalidVersion

PYPI_BASE = "https://pypi.org/pypi"
TARGET_PYTHON = "3.11"
USER_AGENT = "py311-requirements-checker/1.0"


def fetch_json(url):
    """GET a URL and return parsed JSON, or None on 404, raise on other errors."""
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return None
        raise
    except urllib.error.URLError as e:
        print(f"  ! network error fetching {url}: {e}", file=sys.stderr)
        return None


def parse_requirement_line(line):
    """
    Parse a simple 'name==version' requirement line.
    Returns (name, version) or None if the line can't be parsed as a
    pinned requirement (comments, blanks, unpinned specs, extras, etc.
    are still counted but flagged as not-pinned/parsable).
    """
    line = line.strip()
    if not line or line.startswith("#"):
        return None, None
    # Strip inline comments
    line = line.split("#", 1)[0].strip()
    if not line:
        return None, None

    m = re.match(
        r"^([A-Za-z0-9][A-Za-z0-9._-]*)\s*==\s*([A-Za-z0-9][A-Za-z0-9._+!-]*)$",
        line,
    )
    if not m:
        return line, None  # keep raw text as "name" so we can report it
    name, version = m.group(1), m.group(2)
    return name, version


def min_python_version(requires_python_str):
    """
    Given a requires_python specifier string like '>=3.8,<3.13', return the
    effective minimum Python version as a Version object (the MAX of all
    lower bounds, since all of them must hold simultaneously), or None if
    no lower bound is present (e.g. empty / only upper bounds).
    """
    if not requires_python_str:
        return None
    try:
        spec_set = SpecifierSet(requires_python_str)
    except Exception:
        return None

    lower_bounds = []
    for spec in spec_set:
        if spec.operator in (">=", ">", "==", "~="):
            try:
                lower_bounds.append(Version(spec.version))
            except InvalidVersion:
                continue
    if not lower_bounds:
        return None
    return max(lower_bounds)


def is_compatible_with_target(requires_python_str, target=TARGET_PYTHON):
    """
    A release with no requires_python counts as compatible.
    Otherwise target must satisfy the specifier set (checks upper bounds too).
    """
    if not requires_python_str or not requires_python_str.strip():
        return True
    try:
        spec_set = SpecifierSet(requires_python_str)
    except Exception:
        # Unparseable specifier -> don't block on it, treat as compatible
        return True
    return spec_set.contains(target, prereleases=True)


def main():
    if len(sys.argv) != 2:
        print(
            f"Usage: python3 {sys.argv[0]} requirements.txt", file=sys.stderr)
        sys.exit(1)

    req_file = sys.argv[1]
    try:
        with open(req_file, "r", encoding="utf-8") as f:
            raw_lines = f.readlines()
    except OSError as e:
        print(f"Could not read {req_file}: {e}", file=sys.stderr)
        sys.exit(1)

    # Only count real, non-blank, non-comment lines as "dependencies checked"
    lines = []
    for raw in raw_lines:
        stripped = raw.strip()
        if not stripped or stripped.startswith("#"):
            continue
        lines.append(stripped)

    total_checked = len(lines)
    not_found = []
    incompatible = []
    highest_req = None  # (name, version, min_version)

    for line in lines:
        name, version = parse_requirement_line(line)
        if name is None:
            continue
        if version is None:
            print(f"  ! could not parse pinned 'name==version' from: {line!r} "
                  f"-- skipping version/compat checks, counting as not found",
                  file=sys.stderr)
            not_found.append(line)
            continue

        print(f"Checking {name}=={version} ...")

        # Step 1: does the package exist at all?
        pkg_data = fetch_json(f"{PYPI_BASE}/{name}/json")
        if pkg_data is None:
            print(f"  -> package '{name}' not found on PyPI")
            not_found.append(f"{name}=={version}")
            continue

        # Step 2: does the specific pinned version exist?
        release_data = fetch_json(f"{PYPI_BASE}/{name}/{version}/json")
        if release_data is None:
            print(f"  -> version '{version}' of '{name}' not found on PyPI")
            not_found.append(f"{name}=={version}")
            continue

        requires_python = release_data.get("info", {}).get("requires_python")

        # Step 3: Python 3.11 compatibility (checks upper bounds too)
        if not is_compatible_with_target(requires_python):
            print(f"  -> incompatible with Python {TARGET_PYTHON} "
                  f"(requires_python={requires_python!r})")
            incompatible.append(
                f"{name}=={version} (requires_python={requires_python})")
        else:
            print(f"  -> OK (requires_python={requires_python!r})")

        # Step 4: track highest minimum-Python requirement
        # "among the releases that exist and have a requires_python"
        if requires_python:
            min_ver = min_python_version(requires_python)
            if min_ver is not None:
                if highest_req is None or min_ver > highest_req[2]:
                    highest_req = (name, version, min_ver)

    # ----- Final report -----
    print()
    print("===== RESULTS =====")
    print(f"Dependencies checked: {total_checked}")
    print(f"Not found on PyPI: {len(not_found)}")
    if not_found:
        for item in not_found:
            print(f"    - {item}")
    print(f"Incompatible with Python {TARGET_PYTHON}: {len(incompatible)}")
    if incompatible:
        for item in incompatible:
            print(f"    - {item}")

    if highest_req:
        name, version, min_ver = highest_req
        print(f"Highest requirement: {name}=={version} (>={min_ver})")
    else:
        print("Highest requirement: n/a (no releases declared requires_python)")


if __name__ == "__main__":
    main()
