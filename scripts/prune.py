#!/usr/bin/env python3
"""
Python Turbo Prune --Docker

uv 모노레포에서 특정 앱과 그 의존성만 추출하여
Docker 빌드에 최적화된 구조를 생성합니다.

출력 구조 (turbo prune --docker와 동일):
  out/
  ├── json/                    # pyproject.toml + uv.lock만 (의존성 캐시용)
  │   ├── pyproject.toml
  │   ├── uv.lock
  │   ├── apps/<app>/pyproject.toml
  │   └── packages/<pkg>/pyproject.toml
  └── full/                    # 전체 소스 코드
      ├── pyproject.toml
      ├── uv.lock
      ├── apps/<app>/
      └── packages/<pkg>/

Usage:
    python scripts/prune.py <app-name> [--out-dir <dir>]

Examples:
    python scripts/prune.py order-service
    python scripts/prune.py user-dashboard --out-dir ./out
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path

if sys.version_info >= (3, 11):
    import tomllib
else:
    import tomli as tomllib


def parse_pyproject(path: Path) -> dict:
    """pyproject.toml 파일을 파싱합니다."""
    with open(path, "rb") as f:
        return tomllib.load(f)


def get_package_name(pyproject: dict) -> str:
    """pyproject.toml에서 패키지 이름을 추출합니다."""
    return pyproject.get("project", {}).get("name", "")


def get_dependencies(pyproject: dict) -> list[str]:
    """pyproject.toml에서 의존성 목록을 추출합니다."""
    return pyproject.get("project", {}).get("dependencies", [])


def get_uv_sources(pyproject: dict) -> dict:
    """pyproject.toml에서 uv sources를 추출합니다."""
    return pyproject.get("tool", {}).get("uv", {}).get("sources", {})


def discover_packages(root: Path) -> dict[str, Path]:
    """모노레포 내의 모든 패키지를 탐색합니다."""
    packages = {}

    for base_dir in ["apps", "packages"]:
        base_path = root / base_dir
        if base_path.exists():
            for pkg_dir in base_path.iterdir():
                if not pkg_dir.is_dir():
                    continue
                pyproject_path = pkg_dir / "pyproject.toml"
                if pyproject_path.exists():
                    pyproject = parse_pyproject(pyproject_path)
                    name = get_package_name(pyproject)
                    if name:
                        packages[name] = pkg_dir

    return packages


def get_internal_dependencies(
    pyproject: dict,
    all_packages: dict[str, Path]
) -> set[str]:
    """
    pyproject.toml에서 내부 패키지 의존성만 추출합니다.
    workspace 참조와 path 참조 모두 처리합니다.
    """
    internal_deps = set()
    dependencies = get_dependencies(pyproject)
    uv_sources = get_uv_sources(pyproject)

    for dep in dependencies:
        # 의존성 이름 추출 (버전 제약 제거)
        dep_name = dep.split(">=")[0].split("<=")[0].split("==")[0].split("[")[0].strip()

        # uv sources에서 workspace 또는 path 참조 확인
        if dep_name in uv_sources:
            source = uv_sources[dep_name]
            if source.get("workspace") or source.get("path"):
                internal_deps.add(dep_name)
        # uv sources에 없어도 내부 패키지면 추가
        elif dep_name in all_packages:
            internal_deps.add(dep_name)

    return internal_deps


def collect_all_dependencies(
    target: str,
    all_packages: dict[str, Path],
    collected: set[str] | None = None
) -> set[str]:
    """타겟 패키지의 모든 의존성을 재귀적으로 수집합니다."""
    if collected is None:
        collected = set()

    if target in collected:
        return collected

    collected.add(target)

    pkg_path = all_packages.get(target)
    if not pkg_path:
        return collected

    pyproject_path = pkg_path / "pyproject.toml"
    if not pyproject_path.exists():
        return collected

    pyproject = parse_pyproject(pyproject_path)
    internal_deps = get_internal_dependencies(pyproject, all_packages)

    for dep in internal_deps:
        collect_all_dependencies(dep, all_packages, collected)

    return collected


def is_workspace_excluded(root: Path, app_name: str) -> bool:
    """앱이 workspace에서 제외되었는지 확인합니다."""
    root_pyproject = parse_pyproject(root / "pyproject.toml")
    excludes = root_pyproject.get("tool", {}).get("uv", {}).get("workspace", {}).get("exclude", [])

    for exclude in excludes:
        if exclude.endswith(app_name) or exclude == f"apps/{app_name}":
            return True

    return False


IGNORE_PATTERNS = shutil.ignore_patterns(
    "__pycache__",
    "*.pyc",
    ".pytest_cache",
    ".ruff_cache",
    ".venv",
    "*.egg-info",
    ".git",
)


def copy_pyproject_only(src: Path, dest: Path) -> None:
    """pyproject.toml과 uv.lock(있으면)만 복사합니다 (json/ 디렉토리용)."""
    dest.mkdir(parents=True, exist_ok=True)
    pyproject_src = src / "pyproject.toml"
    if pyproject_src.exists():
        shutil.copy2(pyproject_src, dest / "pyproject.toml")
    # workspace 제외된 앱은 자체 uv.lock을 가질 수 있음
    uv_lock_src = src / "uv.lock"
    if uv_lock_src.exists():
        shutil.copy2(uv_lock_src, dest / "uv.lock")


def copy_full_package(src: Path, dest: Path) -> None:
    """패키지 전체를 복사합니다 (full/ 디렉토리용)."""
    if dest.exists():
        shutil.rmtree(dest)
    shutil.copytree(src, dest, ignore=IGNORE_PATTERNS)


def generate_root_pyproject(
    root: Path,
    target_app: str,
    required_packages: set[str],
    all_packages: dict[str, Path],
    is_excluded: bool
) -> str:
    """타겟 앱을 위한 루트 pyproject.toml을 생성합니다."""
    original = parse_pyproject(root / "pyproject.toml")

    # 앱의 상대 경로
    app_path = all_packages[target_app]
    app_relative = app_path.relative_to(root)

    # 필요한 패키지들의 경로
    package_paths = []
    for pkg_name in required_packages:
        if pkg_name == target_app:
            continue
        pkg_path = all_packages[pkg_name]
        pkg_relative = pkg_path.relative_to(root)
        package_paths.append(str(pkg_relative))

    # override-dependencies 유지
    override_deps = original.get("tool", {}).get("uv", {}).get("override-dependencies", [])
    override_section = ""
    if override_deps:
        deps_str = ", ".join(f'"{dep}"' for dep in override_deps)
        override_section = f"override-dependencies = [{deps_str}]"

    # workspace members 및 exclude 설정
    if is_excluded:
        # workspace 제외된 앱: packages만 members에, 앱은 exclude에
        members = sorted(package_paths)
        exclude = [str(app_relative)]
    else:
        # workspace member: 앱과 packages 모두 members에
        members = [str(app_relative)] + sorted(package_paths)
        exclude = []

    members_str = ", ".join(f'"{m}"' for m in members)
    exclude_str = ", ".join(f'"{e}"' for e in exclude)

    workspace_section = f'[tool.uv.workspace]\nmembers = [{members_str}]'
    if exclude:
        workspace_section += f'\nexclude = [{exclude_str}]'

    project_info = original.get("project", {})

    content = f'''[project]
name = "{project_info.get('name', 'python-monorepo-application')}"
version = "{project_info.get('version', '0.1.0')}"
description = "{project_info.get('description', 'Python Monorepo Application')}"
readme = "README.md"
license = {{ text = "MIT" }}
authors = [{{ name = "stendhal.son", email = "stendhal.son@gmail.com" }}]
requires-python = "{project_info.get('requires-python', '>=3.11,<3.12')}"
dependencies = []

[tool.uv]
package = false
{override_section}

{workspace_section}
'''

    return content


def generate_lockfile_json(root: Path, target_app: str, required_packages: set[str]) -> dict:
    """prune 메타데이터를 JSON으로 생성합니다."""
    return {
        "target": target_app,
        "packages": sorted(required_packages),
        "generated_by": "python-turbo-prune",
    }


def prune(target_app: str, out_dir: Path, root: Path) -> None:
    """
    타겟 앱과 그 의존성만 포함하는 pruned 구조를 생성합니다.
    turbo prune --docker와 동일한 json/, full/ 구조를 사용합니다.
    """
    print(f"Pruning for: {target_app}")
    print(f"Output directory: {out_dir}")

    # 1. 모든 패키지 탐색
    all_packages = discover_packages(root)
    print(f"Discovered packages: {list(all_packages.keys())}")

    if target_app not in all_packages:
        print(f"Error: App '{target_app}' not found in monorepo")
        apps = [name for name, path in all_packages.items() if "apps" in str(path)]
        print(f"Available apps: {apps}")
        sys.exit(1)

    # 2. workspace 제외 여부 확인
    is_excluded = is_workspace_excluded(root, target_app)
    print(f"Workspace excluded: {is_excluded}")

    # 3. 모든 의존성 수집
    required_packages = collect_all_dependencies(target_app, all_packages)
    print(f"Required packages: {required_packages}")

    # 4. 출력 디렉토리 생성
    if out_dir.exists():
        shutil.rmtree(out_dir)

    json_dir = out_dir / "json"
    full_dir = out_dir / "full"
    json_dir.mkdir(parents=True)
    full_dir.mkdir(parents=True)

    # 5. 루트 pyproject.toml 생성
    root_pyproject_content = generate_root_pyproject(
        root, target_app, required_packages, all_packages, is_excluded
    )

    (json_dir / "pyproject.toml").write_text(root_pyproject_content)
    (full_dir / "pyproject.toml").write_text(root_pyproject_content)
    print("Generated: pyproject.toml")

    # 6. 앱과 패키지 복사
    for pkg_name in required_packages:
        pkg_path = all_packages[pkg_name]
        relative_path = pkg_path.relative_to(root)

        # json/ - pyproject.toml만
        json_pkg_dir = json_dir / relative_path
        copy_pyproject_only(pkg_path, json_pkg_dir)
        print(f"[json] Copied: {relative_path}/pyproject.toml")

        # full/ - 전체 복사
        full_pkg_dir = full_dir / relative_path
        full_pkg_dir.parent.mkdir(parents=True, exist_ok=True)
        copy_full_package(pkg_path, full_pkg_dir)
        print(f"[full] Copied: {relative_path}/")

    # 7. uv.lock 복사
    uv_lock = root / "uv.lock"
    if uv_lock.exists():
        shutil.copy2(uv_lock, json_dir / "uv.lock")
        shutil.copy2(uv_lock, full_dir / "uv.lock")
        print("Copied: uv.lock")

    # 8. .python-version 복사
    python_version = root / ".python-version"
    if python_version.exists():
        shutil.copy2(python_version, json_dir / ".python-version")
        shutil.copy2(python_version, full_dir / ".python-version")
        print("Copied: .python-version")

    # 9. ruff.toml 복사 (full/만)
    ruff_toml = root / "ruff.toml"
    if ruff_toml.exists():
        shutil.copy2(ruff_toml, full_dir / "ruff.toml")
        print("Copied: ruff.toml (full/ only)")

    # 10. prune.json 메타데이터 생성
    prune_meta = generate_lockfile_json(root, target_app, required_packages)
    (out_dir / "prune.json").write_text(json.dumps(prune_meta, indent=2))
    print("Generated: prune.json")

    print(f"\n{'='*50}")
    print(f"Prune completed for: {target_app}")
    print(f"Output: {out_dir}")
    print(f"  - json/  : Lock files for dependency caching")
    print(f"  - full/  : Full source code")
    print(f"\nDockerfile usage:")
    print(f"  COPY out/json /app")
    print(f"  RUN uv sync --frozen")
    print(f"  COPY out/full /app")


def main():
    parser = argparse.ArgumentParser(
        description="Python Turbo Prune --Docker: Extract app with dependencies for Docker build"
    )
    parser.add_argument(
        "app",
        help="Target app name to prune (e.g., order-service, user-dashboard)"
    )
    parser.add_argument(
        "--out-dir",
        "-o",
        default=None,
        help="Output directory (default: out/<app>)"
    )
    parser.add_argument(
        "--root",
        "-r",
        default=".",
        help="Monorepo root directory (default: current directory)"
    )

    args = parser.parse_args()

    root = Path(args.root).resolve()

    # 기본 출력 디렉토리: out/
    if args.out_dir:
        out_dir = Path(args.out_dir).resolve()
    else:
        out_dir = root / "out"

    prune(args.app, out_dir, root)


if __name__ == "__main__":
    main()
