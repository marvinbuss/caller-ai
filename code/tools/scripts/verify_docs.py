"""Validate local Markdown links and actually render every Mermaid diagram."""

import json
import re
import shutil
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET
from pathlib import Path
from urllib.parse import unquote, urlsplit

import psutil
from ci import ROOT, build_failure_category, environment
from markdown_it import MarkdownIt
from smoke import stop


def renderer_launcher() -> list[str]:
    windows = sys.platform == "win32"
    launcher = shutil.which("npx.cmd" if windows else "npx")
    if not launcher:
        raise RuntimeError("node_npx_required_for_mermaid_validation")
    if not windows:
        return [launcher]
    node = shutil.which("node.exe")
    if not node:
        raise RuntimeError("node_npx_required_for_mermaid_validation")
    script = Path(launcher).parent / "node_modules/npm/bin/npx-cli.js"
    if not script.is_file():
        raise RuntimeError("node_npx_script_unavailable")
    return [node, str(script)]


def renderer_browser(prepared: str, values: dict[str, str]) -> tuple[str, str]:
    if sys.platform not in ("linux", "win32") or values.get("GITHUB_ACTIONS") != "true":
        return prepared, "prepared"
    windows = sys.platform == "win32"
    browser = (
        r"C:\Program Files\Google\Chrome\Application\chrome.exe"
        if windows
        else "/opt/google/chrome/chrome"
    )
    if not Path(browser).is_file():
        raise RuntimeError("ci_sandboxed_browser_unavailable")
    command = [browser, "--version"]
    if windows:
        probe = (
            "import sys; from playwright.sync_api import sync_playwright; "
            "runtime=sync_playwright().start(); "
            "browser=runtime.chromium.launch(executable_path=sys.argv[1],chromium_sandbox=True); "
            "print(browser.version); browser.close(); runtime.stop()"
        )
        command = [
            "uv",
            "run",
            "--directory",
            "code/management",
            "--locked",
            "python",
            "-c",
            probe,
            browser,
        ]
    result = subprocess.run(
        command,
        cwd=ROOT,
        env=values,
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=30 if windows else 10,
    )
    pattern = r"(\d+\.\d+\.\d+\.\d+)" if windows else r"Google Chrome (\d+\.\d+\.\d+\.\d+)"
    match = re.fullmatch(pattern, result.stdout.strip())
    if match is None:
        raise RuntimeError("ci_sandboxed_browser_version_invalid")
    return browser, match[1]


def run_renderer(arguments: list[str], values: dict[str, str], log: Path) -> None:
    with log.open("w", encoding="utf-8") as output:
        process = psutil.Popen(
            arguments,
            cwd=ROOT,
            env=values,
            stdin=subprocess.DEVNULL,
            stdout=output,
            stderr=output,
        )
        try:
            try:
                result = process.wait(timeout=300)
            except (subprocess.TimeoutExpired, psutil.TimeoutExpired):
                output.flush()
                with log.open("rb") as diagnostic:
                    diagnostic.seek(max(0, log.stat().st_size - 16384))
                    tail = diagnostic.read(16384)
                phase = (
                    "mermaid_started"
                    if b"Generating single mermaid chart" in tail
                    else "launcher_or_package"
                )
                raise RuntimeError("diagram_renderer_timeout:" + phase) from None
            if result != 0:
                output.flush()
                raise RuntimeError("diagram_renderer_failed:" + build_failure_category(log))
        finally:
            stop([process])


def discover_documents(root: Path) -> list[Path]:
    old = root / "code/docs"
    if old.exists() and any(path.is_file() for path in old.rglob("*")):
        raise ValueError("duplicate_maintained_documentation")
    maintained = root / "docs"
    if not maintained.is_dir():
        raise ValueError("root_documentation_required")
    paths = set(maintained.rglob("*.md"))
    paths.update((root / "code/contracts").rglob("*.md"))
    paths.update((root / "specs").rglob("*.md"))
    if (root / "README.md").is_file():
        paths.add(root / "README.md")
    return sorted(paths)


def heading_fragments(path: Path) -> set[str]:
    tokens = MarkdownIt().parse(path.read_text(encoding="utf-8"))
    fragments: set[str] = set()
    for index, token in enumerate(tokens):
        if token.type != "heading_open":
            continue
        heading = tokens[index + 1]
        text = "".join(
            child.content
            for child in heading.children or []
            if child.type in {"text", "code_inline", "image"}
        )
        base = re.sub(r"[^\w\-\s]", "", text.lower()).replace(" ", "-")
        candidate, count = base, 0
        while candidate in fragments:
            count += 1
            candidate = base + "-" + str(count)
        fragments.add(candidate)
    return fragments


def validate_documents(paths: list[Path], root: Path) -> list[tuple[Path, str]]:
    diagrams = []
    fragments: dict[Path, set[str]] = {}
    for path in paths:
        tokens = MarkdownIt().parse(path.read_text(encoding="utf-8"))
        if sum(token.type == "heading_open" and token.tag == "h1" for token in tokens) != 1:
            raise ValueError(f"document_requires_one_title: {path}")
        for token in tokens:
            if token.type == "fence" and token.info.strip() == "mermaid":
                if not token.content.strip():
                    raise ValueError(f"empty_mermaid_diagram: {path}")
                diagrams.append((path, token.content))
            for child in token.children or []:
                if child.type not in {"link_open", "image"}:
                    continue
                reference = child.attrGet("src" if child.type == "image" else "href") or ""
                if not isinstance(reference, str):
                    raise ValueError(f"invalid_local_document_link: {path}")
                target = urlsplit(reference)
                if not target.scheme and not target.netloc and (target.path or target.fragment):
                    resolved = (
                        (path.parent / unquote(target.path)).resolve()
                        if target.path
                        else path.resolve()
                    )
                    if not resolved.is_relative_to(root.resolve()) or not resolved.is_file():
                        raise ValueError(f"invalid_local_document_link: {path}")
                    if target.fragment and resolved.suffix.lower() == ".md":
                        if resolved not in fragments:
                            fragments[resolved] = heading_fragments(resolved)
                        if unquote(target.fragment) not in fragments[resolved]:
                            raise ValueError(f"invalid_local_document_fragment: {path}")
    return diagrams


def main() -> None:
    paths = discover_documents(ROOT)
    diagrams = validate_documents(paths, ROOT)
    if not diagrams:
        raise RuntimeError("no_architecture_diagrams_found")
    launcher = renderer_launcher()
    values = environment()
    values["PUPPETEER_SKIP_DOWNLOAD"] = "true"
    values["NPM_CONFIG_FETCH_TIMEOUT"] = "30000"
    values["NPM_CONFIG_FETCH_RETRIES"] = "0"
    browser = subprocess.run(
        [
            "uv",
            "run",
            "--directory",
            "code/management",
            "--locked",
            "python",
            "-c",
            "from playwright.sync_api import sync_playwright; "
            "context=sync_playwright().start(); "
            "print(context.chromium.executable_path); context.stop()",
        ],
        cwd=ROOT,
        env=values,
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=30,
    ).stdout.strip()
    if not Path(browser).is_file():
        raise RuntimeError("prepared_chromium_required_for_mermaid_validation")
    browser, browser_version = renderer_browser(browser, values)
    output = ROOT / "code/tools/test-results/diagrams"
    output.mkdir(parents=True, exist_ok=True)
    renderer = [
        "--yes",
        "--registry",
        values["NPM_CONFIG_REGISTRY"],
        "--package",
        "@mermaid-js/mermaid-cli@12.0.0",
        "--",
        "mmdc",
    ]
    preparation_log = output / "renderer-prepare.log"
    print(json.dumps({"event": "diagram_renderer_prepare_started"}), flush=True)
    run_renderer([*launcher, *renderer, "--version"], values, preparation_log)
    with preparation_log.open("rb") as diagnostic:
        prepared_version = diagnostic.read(65536).decode("utf-8", errors="replace")
    if not re.search(r"(?m)^12\.0\.0\r?$", prepared_version):
        raise RuntimeError("mermaid_cli_version_invalid")
    print(json.dumps({"event": "diagram_renderer_prepared", "version": "12.0.0"}), flush=True)
    with tempfile.TemporaryDirectory(prefix="caller-diagrams-") as temporary:
        directory = Path(temporary)
        configuration = directory / "browser.json"
        configuration.write_text(json.dumps({"executablePath": browser}), encoding="utf-8")
        for index, (path, source) in enumerate(diagrams):
            diagram = directory / f"diagram-{index}.mmd"
            rendered = output / f"{path.stem}-{index}.svg"
            diagram.write_text(source, encoding="utf-8")
            print(
                json.dumps({"event": "diagram_render_started", "diagram_index": index}), flush=True
            )
            run_renderer(
                [
                    *launcher,
                    "--offline",
                    *renderer,
                    "-i",
                    str(diagram),
                    "-o",
                    str(rendered),
                    "-p",
                    str(configuration),
                ],
                values,
                output / f"renderer-{index}.log",
            )
            svg = ET.parse(rendered).getroot()
            view_box = svg.attrib.get("viewBox", "").split()
            if (
                not svg.tag.endswith("svg")
                or len(view_box) != 4
                or float(view_box[2]) <= 0
                or float(view_box[3]) <= 0
                or len(list(svg.iter())) < 5
            ):
                raise RuntimeError("rendered_diagram_empty_or_invalid")
            print(
                json.dumps({"event": "diagram_render_passed", "diagram_index": index}), flush=True
            )
    print(
        json.dumps(
            {
                "result": "documentation_verified",
                "documents": len(paths),
                "rendered_mermaid_diagrams": len(diagrams),
                "browser_version": browser_version,
            }
        )
    )


if __name__ == "__main__":
    main()
