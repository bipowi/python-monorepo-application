#!/usr/bin/env python3
"""
Python Turbo Prune --Docker

uv 모노레포에서 특정 앱과 그 의존성만 추출하여
Docker 빌드에 최적화된 구조를 생성합니다.

Usage:
    python scripts/prune.py <app-name> [--out-dir <dir>]

Examples:
    python scripts/prune.py order-service
    python scripts/prune.py user-dashboard --out-dir ./out
"""

from __future__ import annotations

import argparse
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

    # apps 탐색
    apps_dir = root / "apps"
    if apps_dir.exists():
        for app_dir in apps_dir.iterdir():
            pyproject_path = app_dir / "pyproject.toml"
            if pyproject_path.exists():
                pyproject = parse_pyproject(pyproject_path)
                name = get_package_name(pyproject)
                if name:
                    packages[name] = app_dir

    # packages 탐색
    packages_dir = root / "packages"
    if packages_dir.exists():
        for pkg_dir in packages_dir.iterdir():
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
    """
    타겟 패키지의 모든 의존성을 재귀적으로 수집합니다.
    """
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
        # glob 패턴 처리
        if exclude.endswith(app_name) or exclude == f"apps/{app_name}":
            return True

    return False


def copy_package(src: Path, dest: Path) -> None:
    """패키지 디렉토리를 복사합니다."""
    if dest.exists():
        shutil.rmtree(dest)

    shutil.copytree(
        src,
        dest,
        ignore=shutil.ignore_patterns(
            "__pycache__",
            "*.pyc",
            ".pytest_cache",
            ".ruff_cache",
            ".venv",
            "*.egg-info",
            ".git",
        )
    )


def generate_root_pyproject(
    root: Path,
    target_app: str,
    required_packages: set[str],
    all_packages: dict[str, Path],
    is_excluded: bool
) -> str:
    """
    타겟 앱을 위한 루트 pyproject.toml을 생성합니다.
    """
    original = parse_pyproject(root / "pyproject.toml")

    # 앱의 상대 경로 결정
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

    # workspace members 생성
    members = [str(app_relative)] + sorted(package_paths)

    # override-dependencies 유지
    override_deps = original.get("tool", {}).get("uv", {}).get("override-dependencies", [])
    override_section = ""
    if override_deps:
        deps_str = ", ".join(f'"{dep}"' for dep in override_deps)
        override_section = f"override-dependencies = [{deps_str}]"

    # workspace 제외된 앱의 경우 다른 처리
    if is_excluded:
        # workspace에서 제외된 앱은 members에 앱을 포함하지 않고
        # package들만 workspace member로 설정
        members_without_app = sorted(package_paths)
        if members_without_app:
            members_str = ", ".join(f'"{m}"' for m in members_without_app)
            workspace_section = f"""[tool.uv.workspace]
members = [{members_str}]"""
        else:
            workspace_section = """[tool.uv.workspace]
members = []"""
    else:
        members_str = ", ".join(f'"{m}"' for m in members)
        workspace_section = f"""[tool.uv.workspace]
members = [{members_str}]"""

    # 프로젝트 정보
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


def update_app_pyproject_for_pruned(
    app_path: Path,
    required_packages: set[str],
    all_packages: dict[str, Path],
    is_excluded: bool,
    out_dir: Path
) -> None:
    """
    앱의 pyproject.toml을 pruned 구조에 맞게 업데이트합니다.
    workspace 제외된 앱의 경우 path 참조를 수정합니다.
    """
    pyproject_path = app_path / "pyproject.toml"
    original = parse_pyproject(pyproject_path)

    if not is_excluded:
        # workspace member인 경우 수정 불필요
        return

    # workspace 제외된 앱의 경우 path 참조 수정
    uv_sources = get_uv_sources(original)

    # 앱의 out 디렉토리 내 위치
    app_relative_in_out = app_path.name  # e.g., "user-dashboard"

    new_sources = {}
    for dep_name, source in uv_sources.items():
        if source.get("path"):
            # 원래 경로에서 패키지 이름 추출
            pkg_name = dep_name
            if pkg_name in required_packages:
                # out 디렉토리 구조에 맞게 경로 수정
                # apps/user-dashboard -> packages/xxx 는 ../../packages/xxx
                new_path = f"../../packages/{pkg_name}"
                new_sources[dep_name] = {"path": new_path, "editable": True}

    if new_sources:
        # pyproject.toml 내용 읽기
        with open(pyproject_path) as f:
            content = f.read()

        # [tool.uv.sources] 섹션 찾아서 교체
        import re

        # 새 sources 섹션 생성
        sources_lines = ["[tool.uv.sources]"]
        for name, source in new_sources.items():
            path = source["path"]
            editable = str(source.get("editable", True)).lower()
            sources_lines.append(f'{name} = {{ path = "{path}", editable = {editable} }}')
        new_sources_section = "\n".join(sources_lines)

        # 기존 [tool.uv.sources] 섹션 교체
        pattern = r'\[tool\.uv\.sources\].*?(?=\n\[|\Z)'
        content = re.sub(pattern, new_sources_section, content, flags=re.DOTALL)

        with open(pyproject_path, 'w') as f:
            f.write(content)


def prune(target_app: str, out_dir: Path, root: Path) -> None:
    """
    타겟 앱과 그 의존성만 포함하는 pruned 구조를 생성합니다.
    """
    print(f"Pruning for: {target_app}")
    print(f"Output directory: {out_dir}")

    # 1. 모든 패키지 탐색
    all_packages = discover_packages(root)
    print(f"Discovered packages: {list(all_packages.keys())}")

    if target_app not in all_packages:
        print(f"Error: App '{target_app}' not found in monorepo")
        print(f"Available apps: {[name for name, path in all_packages.items() if 'apps' in str(path)]}")
        sys.exit(1)

    # 2. workspace 제외 여부 확인
    is_excluded = is_workspace_excluded(root, target_app)
    print(f"Workspace excluded: {is_excluded}")

    # 3. 모든 의존성 수집
    required_packages = collect_all_dependencies(target_app, all_packages)
    print(f"Required packages: {required_packages}")

    # 4. out 디렉토리 생성
    if out_dir.exists():
        shutil.rmtree(out_dir)
    out_dir.mkdir(parents=True)

    # 5. 앱과 패키지 복사
    for pkg_name in required_packages:
        pkg_path = all_packages[pkg_name]
        relative_path = pkg_path.relative_to(root)
        dest_path = out_dir / relative_path
        dest_path.parent.mkdir(parents=True, exist_ok=True)

        print(f"Copying: {relative_path}")
        copy_package(pkg_path, dest_path)

    # 6. 루트 pyproject.toml 생성
    root_pyproject_content = generate_root_pyproject(
        root, target_app, required_packages, all_packages, is_excluded
    )
    (out_dir / "pyproject.toml").write_text(root_pyproject_content)
    print("Generated: pyproject.toml")

    # 7. workspace 제외된 앱의 pyproject.toml 수정
    if is_excluded:
        app_out_path = out_dir / all_packages[target_app].relative_to(root)
        update_app_pyproject_for_pruned(
            app_out_path, required_packages, all_packages, is_excluded, out_dir
        )
        print(f"Updated: {app_out_path / 'pyproject.toml'}")

    # 8. uv.lock 복사
    uv_lock = root / "uv.lock"
    if uv_lock.exists():
        shutil.copy2(uv_lock, out_dir / "uv.lock")
        print("Copied: uv.lock")

    # 9. .python-version 복사
    python_version = root / ".python-version"
    if python_version.exists():
        shutil.copy2(python_version, out_dir / ".python-version")
        print("Copied: .python-version")

    # 10. ruff.toml 복사 (루트)
    ruff_toml = root / "ruff.toml"
    if ruff_toml.exists():
        shutil.copy2(ruff_toml, out_dir / "ruff.toml")
        print("Copied: ruff.toml")

    print(f"\nPrune completed! Output: {out_dir}")
    print(f"To build Docker image, copy '{out_dir}' contents to your Docker context.")


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
        default="out",
        help="Output directory (default: out)"
    )
    parser.add_argument(
        "--root",
        "-r",
        default=".",
        help="Monorepo root directory (default: current directory)"
    )

    args = parser.parse_args()

    root = Path(args.root).resolve()
    out_dir = Path(args.out_dir).resolve()

    prune(args.app, out_dir, root)


if __name__ == "__main__":
    main()
