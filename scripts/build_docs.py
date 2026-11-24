#!/usr/bin/env python3
"""Build the documentation pages of the GPU Deck website from the repository's Markdown.

The Markdown files stay the single source (GitHub renders the same files). This script writes:

  website/docs/<slug>/index.html   one page per public doc, all from one template
  website/docs/index.html          the docs home (groups and cards, replaces docs/README.md)
  website/docs/search-index.json   the client-side search index
  website/docs/diagrams.css        the styles of the inlined diagrams
  website/docs/_media/             images a doc uses that the site does not already have
  website/sitemap.xml              the home page, the docs home and every doc page

website/docs/ is git-ignored: .github/workflows/pages.yml runs this script (after
website/sync-screenshots.sh) before it uploads website/. The header, footer, icon sprite, star
banner and search dialog are cut from website/index.html (between <!-- shared:NAME --> markers),
so the docs always match the home page.

    python3 -m venv .venv-docs && .venv-docs/bin/pip install -r scripts/requirements-docs.txt
    .venv-docs/bin/python scripts/build_docs.py                    # build into website/
    .venv-docs/bin/python scripts/build_docs.py --check            # build in a temp dir, only check (CI)
    .venv-docs/bin/python scripts/build_docs.py --render-diagrams  # after a ```mermaid block changes

Diagrams: every ```mermaid block is rendered ahead of time by --render-diagrams (Mermaid and
playwright-core at the versions pinned below, in headless Chromium, needs Node) into
website/assets/diagrams/<hash>.svg, which is committed. The build inlines that SVG and maps its
colours to CSS variables, so the light and dark themes both colour it and CI needs no browser.

The build fails (exit 1) when:
  - a doc links a file that does not exist, or a heading anchor that does not exist;
  - a doc links a git-ignored (internal) file, or a link leaves the repository;
  - any page of the built site (home page included) has a broken relative link, asset or #anchor;
  - a page links a rendered doc on GitHub instead of its page on the site;
  - a ```mermaid block has no up-to-date SVG (run --render-diagrams), or does not render;
  - website/index.html or website/404.html lost the theme script or its CSP hash;
  - (--check only) a public doc under docs/ or libs/contracts/ is missing from NAV below.
"""

from __future__ import annotations

import argparse
import base64
import datetime as dt
import hashlib
import html
import json
import os
import posixpath
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
import time
from dataclasses import dataclass, field
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import unquote, urlsplit

try:
    from markdown_it import MarkdownIt
    from markdown_it.common.utils import escapeHtml
    from mdit_py_plugins.footnote import footnote_plugin
    from mdit_py_plugins.tasklists import tasklists_plugin
    from pygments import highlight
    from pygments.formatters import HtmlFormatter
    from pygments.lexers import get_lexer_by_name
    from pygments.util import ClassNotFound
except ImportError:  # pragma: no cover
    sys.exit("build_docs.py needs its pinned packages: pip install -r scripts/requirements-docs.txt")

ROOT = Path(__file__).resolve().parent.parent
SITE = ROOT / "website"
DIAGRAM_DIR = SITE / "assets" / "diagrams"
REPO_SLUG = "sur950/gpu-deck"
REPO = f"https://github.com/{REPO_SLUG}"
SITE_URL = "https://sur950.github.io/gpu-deck/"
BASE_PATH = "/gpu-deck/"  # where GitHub Pages serves website/ (404.html uses absolute paths)

MERMAID_VERSION = "12.3.0"
PLAYWRIGHT_CORE_VERSION = "1.62.1"
DIAGRAM_REV = "3"  # bump when MERMAID_CONFIG or the SVG post-processing changes
MIN_DIAGRAM_SCALE = 0.68  # wide diagrams shrink to this much of their size, then scroll
DIAGRAM_ROOM = 734  # px a diagram gets in the article column (772 px minus the figure padding)
LR_FLOWCHART = re.compile(r"^\s*(?:flowchart|graph)\s+LR\b")

# The inline script every page runs first (theme and banner state before the first paint).
# Its SHA-256 is in each page's Content-Security-Policy, so it must stay byte-identical.
THEME_INIT = (
    '(function(){var d=document.documentElement;d.classList.add("js");try{'
    'var t=localStorage.getItem("gpudeck-theme");if(t==="light"||t==="dark")d.setAttribute("data-theme",t);'
    'if(localStorage.getItem("gpudeck-star-banner")==="dismissed")d.classList.add("banner-off")'
    "}catch(e){}})();"
)
THEME_HASH = "sha256-" + base64.b64encode(hashlib.sha256(THEME_INIT.encode()).digest()).decode()


@dataclass(frozen=True)
class Entry:
    source: str  # path from the repository root
    slug: str  # website/docs/<slug>/
    label: str  # sidebar, breadcrumbs, previous / next
    blurb: str  # one line on the docs home
    optional: bool = False  # skipped (not an error) while the file does not exist


# Sidebar order = previous / next order. Every public doc under docs/ and libs/contracts/ must
# be listed here (--check fails otherwise); docs/README.md is replaced by the docs home.
NAV: list[tuple[str, str, list[Entry]]] = [
    ("Getting started", "What GPU Deck is, how the pieces fit, and what it has run on.", [
        Entry("README.md", "introduction", "Introduction",
              "What GPU Deck is, how it runs, the quick start and the supported hardware."),
        Entry("docs/architecture.md", "architecture", "Architecture",
              "The components and the four flows that matter: join, serving, swap and billing."),
        Entry("docs/TESTING.md", "testing", "Testing and hardware",
              "What has been verified on real hardware and what is tested against fixtures.", optional=True),
    ]),
    ("Deployment", "Install the control plane once, then add GPU machines.", [
        Entry("docs/DEPLOYMENT.md", "deployment", "Deploy GPU Deck",
              "What runs where, ports, sizing, and the Compose, Kubernetes and offline installs."),
        Entry("docs/components/infra.md", "infrastructure", "Compose, Helm and offline",
              "The Compose files, the Helm chart, Grafana dashboards and the air-gapped bundle."),
        Entry("docs/components/node-agent.md", "node-agent", "Node agent",
              "The agent on every GPU machine: commands, configuration, packaging, behaviour."),
        Entry("docs/components/fastapi-cloud.md", "cloud-gpu-vms", "Cloud GPU VMs",
              "GPU VMs on AWS, Azure, GCP and DigitalOcean that join their cluster on boot."),
    ]),
    ("Using GPU Deck", "Serve models, automate the fleet and drive it from code.", [
        Entry("libs/contracts/openapi/serving.md", "serving-api", "Serving API",
              "OpenAI, Anthropic and Ollama endpoints, authentication, errors and routing."),
        Entry("libs/contracts/rules/rules.md", "rules", "Rules, layouts and swaps",
              "How the six rule types are evaluated, and how layouts and safe swaps run."),
        Entry("apps/cli/README.md", "cli", "CLI",
              "The gpudeck command line: install, profiles and every admin API command."),
        Entry("docs/components/terraform-provider.md", "terraform", "Terraform provider",
              "Clusters, models, deployments, layouts, rules and keys as code."),
        Entry("docs/components/desktop.md", "desktop-app", "Desktop app",
              "The native window around any control plane: build, signing and updates."),
    ]),
    ("Operations", "Keep an installation healthy, observable and up to date.", [
        Entry("docs/operations.md", "operations", "Operations",
              "Upgrades, backup and restore, rotation, high availability and troubleshooting."),
        Entry("libs/contracts/metrics.md", "metrics", "Metrics catalog",
              "Every metric, per engine and vendor, and how it reaches Prometheus and Grafana."),
    ]),
    ("Security", "How GPU Deck is locked down, and how to report a problem.", [
        Entry("docs/security-model.md", "security-model", "Security model",
              "Network rules and every control, mapped to where it is enforced."),
        Entry("docs/SECURITY.md", "security-policy", "Security policy",
              "Supported versions and how to report a vulnerability privately."),
    ]),
    ("Reference", "The specifications behind the settings and the protocols.", [
        Entry("libs/contracts/env.md", "environment-variables", "Environment variables",
              "Every setting GPU Deck reads, grouped the way the .env files are."),
        Entry("libs/contracts/agent-protocol/protocol.md", "agent-protocol", "Agent protocol",
              "The contract between the node agent, the agent hub and the API layer."),
        Entry("libs/contracts/agent-protocol/CHANGELOG.md", "agent-protocol-changelog",
              "Agent protocol changelog", "Additive changes within protocol major v1."),
        Entry("docs/components/contracts.md", "contracts", "Contracts",
              "The shared specs in libs/contracts and the clients generated from them."),
    ]),
    ("Project", "How GPU Deck is built, tested, released and run as a project.", [
        Entry("CHANGELOG.md", "changelog", "Changelog",
              "What changed in each release, and the known limitations."),
        Entry("CONTRIBUTING.md", "contributing", "Contributing",
              "Set up each component, the pull request workflow and the checklist."),
        Entry("docs/DEVELOPMENT.md", "development", "Development",
              "Layers, extension points, migrations, settings and testing rules."),
        Entry("docs/components/fastapi.md", "backend", "Backend",
              "The FastAPI codebase behind the admin API, serving API and jobs."),
        Entry("docs/components/fastapi-migrations.md", "migrations", "Database migrations",
              "SQL-first Alembic migrations: numbering, commands and rules."),
        Entry("docs/components/fastapi-e2e.md", "e2e-tests", "End-to-end tests",
              "Journeys that drive the APIs and real engines on an Apple silicon Mac."),
        Entry("docs/RELEASING.md", "releasing", "Releasing",
              "Version policy, compatibility, the checklist and what a release publishes."),
        Entry("docs/BRAND.md", "brand", "Brand",
              "Name, wording, marks and colours, and how the brand assets are built."),
        Entry("docs/SUPPORT.md", "support", "Getting help",
              "Where to ask questions, report bugs and share hardware reports."),
        Entry("CODE_OF_CONDUCT.md", "code-of-conduct", "Code of conduct", "How we behave."),
    ]),
]
DOCS_HOME_SOURCE = "docs/README.md"
SCAN_GLOBS = ["docs/**/*.md", "libs/contracts/**/*.md"]
START_HERE = ["deployment", "serving-api", "rules"]  # the three cards at the top of the docs home

LANG_ALIASES = {
    "sh": "bash", "shell": "bash", "zsh": "bash", "env": "bash", "dotenv": "bash",
    "yml": "yaml", "tf": "terraform", "hcl": "terraform", "jsonc": "json", "js": "javascript",
    "ts": "typescript", "py": "python", "rs": "rust", "text": "", "txt": "", "plain": "",
    "plaintext": "", "promql": "", "caddyfile": "", "conf": "ini",
}
ALERTS = {
    "NOTE": ("Note", "i-info"),
    "TIP": ("Tip", "i-bulb"),
    "IMPORTANT": ("Important", "i-flag"),
    "WARNING": ("Warning", "i-alert"),
    "CAUTION": ("Caution", "i-octagon"),
}
FORMATTER = HtmlFormatter(nowrap=True, classprefix="tok-")


# ------------------------------------------------------------------------------------ helpers
class BuildError(Exception):
    pass


def git(*args: str) -> str:
    return subprocess.run(["git", *args], cwd=ROOT, capture_output=True, text=True, check=True).stdout


_ignored: dict[str, bool] = {}


def is_ignored(path: str) -> bool:
    """True when the repository path is git-ignored (internal docs). Works for missing paths too."""
    path = path.rstrip("/")
    if path not in _ignored:
        try:
            r = subprocess.run(["git", "check-ignore", "-q", "--no-index", path], cwd=ROOT)
            _ignored[path] = r.returncode == 0
        except OSError:  # no git: at least keep docs/internal out
            _ignored[path] = path == "docs/internal" or path.startswith("docs/internal/")
    return _ignored[path]


def git_date(path: str) -> dt.date | None:
    try:
        out = git("log", "-1", "--format=%cs", "--", path).strip()
        return dt.date.fromisoformat(out) if out else None
    except (OSError, subprocess.CalledProcessError, ValueError):
        return None


def gh_slug(text: str) -> str:
    """GitHub's heading anchor: lower case, punctuation dropped, each space a hyphen."""
    return re.sub(r"[^\w\- ]", "", text.strip().lower()).replace(" ", "-")


def unique_slug(slug: str, seen: dict[str, int]) -> str:
    if slug not in seen:
        seen[slug] = 0
        return slug
    while True:
        seen[slug] += 1
        candidate = f"{slug}-{seen[slug]}"
        if candidate not in seen:
            seen[candidate] = 0
            return candidate


def strip_tags(fragment: str) -> str:
    fragment = re.sub(r"<(script|style|svg|template)\b.*?</\1>", " ", fragment, flags=re.S | re.I)
    text = re.sub(r"<[^>]+>", " ", fragment)
    return re.sub(r"\s+", " ", html.unescape(text)).strip()


def clip(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    return text[: limit - 1].rsplit(" ", 1)[0].rstrip(",;:") + "…"


def attr(value: str) -> str:
    return html.escape(value, quote=True)


def human_date(day: dt.date) -> str:
    return f"{day.day} {day.strftime('%B %Y')}"


# ------------------------------------------------------------------------------------ diagrams
# Mermaid renders with sentinel colours; the post-processing turns each sentinel into a CSS
# variable (--dg-<name>) so docs.css colours the diagram for the active theme.
SENTINELS = {
    "#0b1c01": "bg", "#0b1c02": "node", "#0b1c03": "text", "#0b1c04": "border", "#0b1c05": "line",
    "#0b1c06": "node-2", "#0b1c07": "cluster", "#0b1c08": "cluster-border", "#0b1c09": "lifeline",
    "#0b1c0a": "note", "#0b1c0b": "note-border",
}
STRAY_COLOURS = {  # Mermaid defaults left on a few elements; CSS classes override them anyway
    "#000000": "line", "#000": "line", "#666": "border", "#666666": "border", "#999": "lifeline",
    "#999999": "lifeline", "#eaeaea": "node", "#edf2ae": "note", "#ffffff": "bg", "#fff": "bg",
    "white": "bg", "black": "line",
}
_S = {v: k for k, v in SENTINELS.items()}
MERMAID_CONFIG = {
    "startOnLoad": False,
    "securityLevel": "strict",
    "theme": "base",
    "look": "classic",
    "htmlLabels": False,
    "fontFamily": "Figtree, ui-sans-serif, system-ui, sans-serif",
    "themeVariables": {
        "darkMode": False, "fontFamily": "Figtree, ui-sans-serif, system-ui, sans-serif",
        "fontSize": "14px", "background": _S["bg"], "primaryColor": _S["node"],
        "primaryTextColor": _S["text"], "primaryBorderColor": _S["border"], "lineColor": _S["line"],
        "secondaryColor": _S["node-2"], "tertiaryColor": _S["cluster"], "clusterBkg": _S["cluster"],
        "clusterBorder": _S["cluster-border"], "edgeLabelBackground": _S["bg"],
        "titleColor": _S["text"], "textColor": _S["text"], "nodeTextColor": _S["text"],
        "actorBkg": _S["node"], "actorBorder": _S["border"], "actorTextColor": _S["text"],
        "actorLineColor": _S["lifeline"], "signalColor": _S["line"], "signalTextColor": _S["text"],
        "labelBoxBkgColor": _S["node-2"], "labelBoxBorderColor": _S["border"],
        "labelTextColor": _S["text"], "loopTextColor": _S["text"], "noteBkgColor": _S["note"],
        "noteBorderColor": _S["note-border"], "noteTextColor": _S["text"],
        "activationBkgColor": _S["node-2"], "activationBorderColor": _S["border"],
        "sequenceNumberColor": _S["bg"],
    },
    "flowchart": {"htmlLabels": False, "curve": "basis", "padding": 14, "nodeSpacing": 44,
                  "rankSpacing": 54, "diagramPadding": 8, "useMaxWidth": True},
    "sequence": {"mirrorActors": False, "messageFontSize": 13, "actorFontSize": 13,
                 "noteFontSize": 13, "actorMargin": 40, "width": 150, "height": 50, "boxMargin": 10,
                 "boxTextMargin": 5, "noteMargin": 10, "messageMargin": 36, "useMaxWidth": True,
                 "wrap": True},
}
PRESENTATION = {"fill", "stroke", "stroke-width", "stroke-dasharray", "stroke-dashoffset",
                "stroke-linecap", "stroke-linejoin", "opacity", "fill-opacity", "stroke-opacity",
                "font-size", "font-weight", "font-family", "font-style", "text-anchor",
                "dominant-baseline", "alignment-baseline"}

RENDER_JS = r"""
// Written by scripts/build_docs.py --render-diagrams. args: jobs.json out.json mermaid.min.js font.woff2 playwright-core-dir
import fs from "node:fs";
import { createRequire } from "node:module";
const [jobsPath, outPath, mermaidPath, fontPath, pwDir] = process.argv.slice(2);
const { chromium } = createRequire(pwDir + "/")("playwright-core");
const jobs = JSON.parse(fs.readFileSync(jobsPath, "utf8"));
const font = fs.readFileSync(fontPath).toString("base64");
const browser = await chromium.launch();
try {
  const page = await browser.newPage();
  await page.setContent(`<!doctype html><html><head><style>@font-face{font-family:"Figtree";font-weight:300 900;src:url(data:font/woff2;base64,${font}) format("woff2")}body{font-family:Figtree;margin:0}</style></head><body></body></html>`);
  await page.addScriptTag({ path: mermaidPath });
  await page.evaluate(() => Promise.all([document.fonts.load('400 14px "Figtree"'), document.fonts.load('600 14px "Figtree"')]));
  const results = await page.evaluate(async (jobs) => {
    const out = [];
    for (const j of jobs) {
      mermaid.initialize(j.config);
      try {
        const { svg } = await mermaid.render(j.id, j.code);
        out.push({ key: j.key, svg });
      } catch (e) {
        out.push({ key: j.key, error: String((e && e.message) || e) });
        document.querySelectorAll('[id^="d' + j.id + '"],#' + j.id).forEach((n) => n.remove());
      }
    }
    return out;
  }, jobs);
  fs.writeFileSync(outPath, JSON.stringify(results));
} finally {
  await browser.close();
}
"""


def diagram_key(code: str) -> str:
    seed = f"{MERMAID_VERSION}\n{DIAGRAM_REV}\n{code.strip()}\n"
    return hashlib.sha256(seed.encode()).hexdigest()[:16]


def _colour_name(value: str) -> str | None:
    v = value.strip().lower()
    if v in SENTINELS:
        return SENTINELS[v]
    m = re.fullmatch(r"rgba?\(\s*11\s*,\s*28\s*,\s*(\d+)\s*(?:,\s*[\d.]+\s*)?\)", v)
    if m:
        return SENTINELS.get(f"#0b1c{int(m.group(1)):02x}")
    if v in STRAY_COLOURS:
        return STRAY_COLOURS[v]
    if v.startswith("hsl("):
        return "line"
    return None


def _css_colours(css: str) -> str:
    def rgba(m: re.Match) -> str:
        name = SENTINELS.get(f"#0b1c{int(m.group(1)):02x}", "line")
        if m.group(2) is None:
            return f"var(--dg-{name})"
        return f"color-mix(in srgb,var(--dg-{name}) {round(float(m.group(2)) * 100)}%,transparent)"

    css = re.sub(r"rgba?\(\s*11\s*,\s*28\s*,\s*(\d+)\s*(?:,\s*([\d.]+)\s*)?\)", rgba, css)
    css = re.sub(r"#0b1c0[0-9a-b]\b", lambda m: f"var(--dg-{SENTINELS[m.group(0).lower()]})", css, flags=re.I)
    css = re.sub(r"filter:\s*drop-shadow\([^;{}]*?\)\s*\)?;?", "", css)
    return css


def postprocess_svg(svg: str, key: str) -> str:
    """Mermaid's SVG -> theme-able SVG: colours become classes / CSS variables, no style attributes."""
    style = re.search(r"<style>(.*?)</style>", svg, re.S)
    css = _css_colours(style.group(1)) if style else ""
    svg = re.sub(r"<style>.*?</style>", "", svg, flags=re.S)

    def fix_tag(m: re.Match) -> str:
        name, attrs, close = m.group(1), m.group(2), m.group(3)
        classes: list[str] = []
        props: dict[str, str] = {}
        sm = re.search(r'\sstyle="([^"]*)"', attrs)
        if sm:
            for decl in sm.group(1).split(";"):
                if ":" in decl:
                    k, v = decl.split(":", 1)
                    k, v = k.strip().lower(), v.strip()
                    if k in PRESENTATION and v:
                        props[k] = v
            attrs = attrs.replace(sm.group(0), "")
        for k in ("fill", "stroke"):
            am = re.search(rf'\s{k}="([^"]*)"', attrs)
            if am:
                props.setdefault(k, am.group(1))
                attrs = attrs.replace(am.group(0), "")
        out_attrs = []
        for k, v in props.items():
            if k in ("fill", "stroke"):
                cname = _colour_name(v)
                if cname:
                    classes.append(f"dg-{k[0]}-{cname}")
                    continue
            out_attrs.append(f'{k}="{attr(v)}"')
        if classes:
            cm = re.search(r'\sclass="([^"]*)"', attrs)
            if cm:
                attrs = attrs.replace(cm.group(0), f' class="{cm.group(1)} {" ".join(classes)}"')
            else:
                out_attrs.append(f'class="{" ".join(classes)}"')
        extra = (" " + " ".join(out_attrs)) if out_attrs else ""
        return f"<{name}{attrs}{extra}{close}>"

    svg = re.sub(r"<([a-zA-Z][\w:-]*)((?:\s[^<>]*?)?)(\s*/?)>", fix_tag, svg)
    # The root element: no fixed size, an id from the hash, labelled as one image.
    width = re.search(r'viewBox="[-\d.]+ [-\d.]+ ([\d.]+) ([\d.]+)"', svg)
    root = re.match(r"<svg\b[^>]*>", svg)
    if not root or not width:
        raise BuildError("unexpected SVG from Mermaid")
    keep = []
    for name in ("viewBox", "xmlns", "xmlns:xlink", "aria-roledescription", "class"):
        am = re.search(rf'\s{re.escape(name)}="([^"]*)"', root.group(0))
        if am:
            value = am.group(1)
            if name == "class":
                value = f"dg-svg {value}"
            keep.append(f'{name}="{value}"')
    if not any(k.startswith("class=") for k in keep):
        keep.append('class="dg-svg"')
    new_root = f'<svg id="dg-{key}" {" ".join(keep)} role="img" aria-label="Diagram" data-width="{float(width.group(1)):.0f}" data-height="{float(width.group(2)):.0f}">'
    svg = new_root + svg[root.end():]
    old_id = re.search(r'\sid="([^"]+)"', root.group(0)).group(1)
    svg = svg.replace(f"#{old_id}", f"#dg-{key}").replace(f'"{old_id}-', f'"dg-{key}-').replace(f"({old_id}-", f"(dg-{key}-")
    css = css.replace(f"#{old_id}", f"#dg-{key}")
    return f"<style>{css}</style>\n{svg}" if css else svg


def cache_dir() -> Path:
    base = os.environ.get("GPUDECK_DOCS_CACHE") or os.environ.get("XDG_CACHE_HOME") or str(Path.home() / ".cache")
    path = Path(base) / "gpu-deck-docs" if "GPUDECK_DOCS_CACHE" not in os.environ else Path(base)
    path.mkdir(parents=True, exist_ok=True)
    return path


def render_toolchain() -> tuple[Path, Path]:
    """mermaid.min.js and a playwright-core install at the pinned versions, cached on disk."""
    if not shutil.which("node") or not shutil.which("npm"):
        raise BuildError("--render-diagrams needs Node.js and npm on PATH")
    cache = cache_dir()
    mermaid_js = cache / f"mermaid-{MERMAID_VERSION}.min.js"
    if not mermaid_js.exists():
        with tempfile.TemporaryDirectory() as tmp:
            subprocess.run(["npm", "pack", f"mermaid@{MERMAID_VERSION}", "--silent", "--pack-destination", tmp],
                           check=True, capture_output=True)
            tgz = next(Path(tmp).glob("mermaid-*.tgz"))
            with tarfile.open(tgz) as tar:
                member = tar.getmember("package/dist/mermaid.min.js")
                src = tar.extractfile(member)
                assert src is not None
                mermaid_js.write_bytes(src.read())
    pw = cache / f"playwright-core-{PLAYWRIGHT_CORE_VERSION}"
    if not (pw / "node_modules" / "playwright-core").exists():
        subprocess.run(["npm", "install", "--prefix", str(pw), "--no-save", "--no-audit", "--no-fund",
                        "--ignore-scripts", f"playwright-core@{PLAYWRIGHT_CORE_VERSION}"], check=True, capture_output=True)
    probe = subprocess.run(["node", "-e", "const {chromium}=require('playwright-core');process.stdout.write(chromium.executablePath())"],
                           cwd=pw, capture_output=True, text=True)
    if probe.returncode != 0 or not Path(probe.stdout).exists():
        print(f"installing Chromium for playwright-core {PLAYWRIGHT_CORE_VERSION} (once)")
        subprocess.run(["node", str(pw / "node_modules" / "playwright-core" / "cli.js"), "install", "chromium"], check=True)
    return mermaid_js, pw


def render_diagrams(blocks: dict[str, tuple[str, str]], force: bool) -> None:
    """blocks: key -> (code, "source:line"). Writes website/assets/diagrams/<key>.svg."""
    DIAGRAM_DIR.mkdir(parents=True, exist_ok=True)
    todo = {k: v for k, v in blocks.items() if force or not (DIAGRAM_DIR / f"{k}.svg").exists()}
    for stale in DIAGRAM_DIR.glob("*.svg"):
        if stale.stem not in blocks:
            stale.unlink()
            print(f"removed unused {stale.relative_to(ROOT)}")
    if not todo:
        print(f"all {len(blocks)} diagrams are up to date")
        return
    mermaid_js, pw = render_toolchain()
    font = SITE / "assets" / "fonts" / "figtree-latin-wght.woff2"
    jobs = [{"key": k, "id": f"m{k.replace('-', '_')}", "code": code, "config": MERMAID_CONFIG} for k, (code, _) in todo.items()]
    with tempfile.TemporaryDirectory() as tmp:
        script, jobs_path, out_path = Path(tmp, "render.mjs"), Path(tmp, "jobs.json"), Path(tmp, "out.json")
        script.write_text(RENDER_JS, encoding="utf-8")
        jobs_path.write_text(json.dumps(jobs), encoding="utf-8")
        subprocess.run(["node", str(script), str(jobs_path), str(out_path), str(mermaid_js), str(font),
                        str(pw / "node_modules")], check=True)
        results = json.loads(out_path.read_text(encoding="utf-8"))
    failed = []
    for r in results:
        where = todo[r["key"]][1]
        if "error" in r:
            failed.append(f"{where}: Mermaid cannot render this diagram (GitHub cannot either):\n    "
                          + r["error"].strip().replace("\n", "\n    "))
            continue
        svg = postprocess_svg(r["svg"], r["key"])
        header = (f"<!-- Generated by scripts/build_docs.py --render-diagrams (Mermaid {MERMAID_VERSION}) "
                  f"from the mermaid block at {where}. Do not edit. -->\n")
        (DIAGRAM_DIR / f"{r['key']}.svg").write_text(header + svg + "\n", encoding="utf-8")
        print(f"rendered {where} -> website/assets/diagrams/{r['key']}.svg")
    if failed:
        raise BuildError("\n".join(failed))


# ------------------------------------------------------------------------------------ markdown
@dataclass
class Page:
    entry: Entry
    group: str
    title: str = ""
    title_html: str = ""
    body: str = ""
    toc: list[tuple[int, str, str]] = field(default_factory=list)  # (level, id, text)
    ids: set[str] = field(default_factory=set)
    description: str = ""
    words: int = 0
    diagrams: list[str] = field(default_factory=list)
    img_hosts: set[str] = field(default_factory=set)
    updated: dt.date | None = None
    h1_id: str = ""

    @property
    def url(self) -> str:
        return f"docs/{self.entry.slug}/"


class Builder:
    def __init__(self, out: Path, check: bool) -> None:
        self.out = out  # the site root being written (website/ or a temp copy)
        self.check = check
        self.errors: list[str] = []
        self.warnings: list[str] = []
        self.pages: list[Page] = []
        self.by_source: dict[str, Page] = {}
        self.anchor_checks: list[tuple[str, str, str]] = []  # (from source, to source, anchor)
        self.diagram_css: dict[str, str] = {}
        self.media: dict[str, str] = {}
        self.line_offset: dict[str, int] = {}  # README: lines trimmed by source_text()
        self.md = self._markdown()

    # ---------------------------------------------------------------- discovery
    def discover(self) -> None:
        listed = set()
        for group, _, entries in NAV:
            for e in entries:
                listed.add(e.source)
                path = ROOT / e.source
                if not path.exists():
                    if not e.optional:
                        self.errors.append(f"NAV lists {e.source}, which does not exist")
                    continue
                if is_ignored(e.source):
                    self.errors.append(f"NAV lists {e.source}, which is git-ignored")
                    continue
                page = Page(e, group)
                self.pages.append(page)
                self.by_source[e.source] = page
        slugs = [p.entry.slug for p in self.pages]
        for s in {s for s in slugs if slugs.count(s) > 1}:
            self.errors.append(f"NAV uses the slug {s!r} twice")
        extra: list[Entry] = []
        for pattern in SCAN_GLOBS:
            for path in sorted(ROOT.glob(pattern)):
                rel = path.relative_to(ROOT).as_posix()
                if rel in listed or rel == DOCS_HOME_SOURCE or is_ignored(rel):
                    continue
                slug = re.sub(r"[^a-z0-9]+", "-", Path(rel).with_suffix("").as_posix().lower()).strip("-")
                extra.append(Entry(rel, slug, Path(rel).stem.replace("-", " ").replace("_", " ").capitalize(), ""))
        if extra:
            msg = "public docs missing from NAV in scripts/build_docs.py: " + ", ".join(e.source for e in extra)
            (self.errors if self.check else self.warnings).append(msg)
            for e in extra:
                page = Page(e, "More")
                self.pages.append(page)
                self.by_source[e.source] = page

    # ---------------------------------------------------------------- markdown-it
    def _markdown(self) -> MarkdownIt:
        md = MarkdownIt("gfm-like", {"html": True, "linkify": True, "typographer": False})
        md.use(footnote_plugin).use(tasklists_plugin)
        md.core.ruler.push("gpudeck_headings", self._core_headings)
        md.core.ruler.push("gpudeck_alerts", self._core_alerts)
        md.add_render_rule("fence", lambda r, *a, f=self._r_fence: f(r, *a))
        md.add_render_rule("code_block", lambda r, *a, f=self._r_fence: f(r, *a))
        md.add_render_rule("link_open", lambda r, *a, f=self._r_link: f(r, *a))
        md.add_render_rule("image", lambda r, *a, f=self._r_image: f(r, *a))
        md.add_render_rule("html_block", lambda r, *a, f=self._r_html: f(r, *a))
        md.add_render_rule("html_inline", lambda r, *a, f=self._r_html: f(r, *a))
        md.add_render_rule("heading_close", lambda r, *a, f=self._r_heading_close: f(r, *a))
        md.add_render_rule("table_open", lambda s, t, i, o, e: '<div class="table-wrap" tabindex="0" role="group" aria-label="Table, scrolls sideways">\n<table>\n')
        md.add_render_rule("table_close", lambda s, t, i, o, e: "</table>\n</div>\n")
        md.add_render_rule("blockquote_open", lambda r, *a, f=self._r_quote_open: f(r, *a))
        md.add_render_rule("blockquote_close", lambda r, *a, f=self._r_quote_close: f(r, *a))
        return md

    @staticmethod
    def _inline_text(tok) -> str:
        out = []
        for c in tok.children or []:
            if c.type in ("text", "code_inline"):
                out.append(c.content)
            elif c.type in ("softbreak", "hardbreak"):
                out.append(" ")
        return "".join(out)

    def _core_headings(self, state) -> None:
        seen: dict[str, int] = {}
        page: Page = state.env["page"]
        toks = state.tokens
        for i, tok in enumerate(toks):
            if tok.type != "heading_open":
                continue
            text = self._inline_text(toks[i + 1])
            hid = unique_slug(gh_slug(text) or "section", seen)
            tok.attrSet("id", hid)
            tok.meta["id"] = hid
            toks[i + 2].meta["id"] = hid
            page.ids.add(hid)
            level = int(tok.tag[1])
            if level in (2, 3):
                page.toc.append((level, hid, text.strip()))

    def _core_alerts(self, state) -> None:
        toks = state.tokens
        for i, tok in enumerate(toks):
            if tok.type != "blockquote_open" or i + 2 >= len(toks):
                continue
            if toks[i + 1].type != "paragraph_open" or toks[i + 2].type != "inline":
                continue
            inline = toks[i + 2]
            m = re.match(r"^\[!(NOTE|TIP|IMPORTANT|WARNING|CAUTION)\]\s*", inline.content)
            if not m:
                continue
            tok.meta["alert"] = m.group(1)
            kids = inline.children or []
            if kids and kids[0].type == "text" and kids[0].content.startswith("[!"):
                kids[0].content = kids[0].content[len(m.group(0).rstrip()):].lstrip()
                if not kids[0].content:
                    kids.pop(0)
                    if kids and kids[0].type in ("softbreak", "hardbreak"):
                        kids.pop(0)
            inline.content = inline.content[m.end():]
            if not kids:  # the marker had its own paragraph
                toks[i + 1].hidden = True
                toks[i + 3].hidden = True

    def _r_quote_open(self, r, tokens, idx, options, env) -> str:
        kind = tokens[idx].meta.get("alert")
        env.setdefault("quote_stack", []).append(kind)
        if not kind:
            return "<blockquote>\n"
        label, icon = ALERTS[kind]
        return (f'<div class="admonition admonition-{kind.lower()}" role="note">'
                f'<p class="admonition-title"><svg class="icon" aria-hidden="true"><use href="#{icon}"/></svg>{label}</p>\n')

    def _r_quote_close(self, r, tokens, idx, options, env) -> str:
        stack = env.get("quote_stack") or []
        kind = stack.pop() if stack else None
        return "</div>\n" if kind else "</blockquote>\n"

    def _r_heading_close(self, r, tokens, idx, options, env) -> str:
        hid = tokens[idx].meta.get("id")
        tag = tokens[idx].tag
        if not hid or tag == "h1":
            return f"</{tag}>\n"
        return (f'<a class="h-anchor" href="#{hid}" aria-label="Link to this section">'
                f'<svg class="icon" aria-hidden="true"><use href="#i-link"/></svg></a></{tag}>\n')

    def _r_fence(self, r, tokens, idx, options, env) -> str:
        tok = tokens[idx]
        lang = (tok.info or "").strip().split(maxsplit=1)[0].lower() if tok.info else ""
        if lang == "mermaid":
            line = (tok.map[0] + 1 if tok.map else 0) + self.line_offset.get(env["page"].entry.source, 0)
            return self.diagram(tok.content, env, line)
        return self.code_html(tok.content, lang)

    def code_html(self, code: str, lang: str) -> str:
        name = LANG_ALIASES.get(lang, lang)
        lexer = None
        if name:
            try:
                lexer = get_lexer_by_name(name, stripnl=False)
            except ClassNotFound:
                lexer = None
        body = highlight(code, lexer, FORMATTER) if lexer else escapeHtml(code)
        body = body.rstrip("\n")
        label = f'<span class="code-lang" aria-hidden="true">{escapeHtml(lang)}</span>' if lang else ""
        return (f'<div class="code-block">{label}'
                '<button type="button" class="code-copy" data-copy-code aria-label="Copy code">'
                '<svg class="icon i-copy" aria-hidden="true"><use href="#i-copy"/></svg>'
                '<svg class="icon i-done" aria-hidden="true"><use href="#i-check"/></svg></button>'
                f'<pre tabindex="0"><code>{body}\n</code></pre></div>\n')

    def _diagram_svg(self, key: str) -> tuple[str, int] | None:
        path = DIAGRAM_DIR / f"{key}.svg"
        if not path.exists():
            return None
        svg = re.sub(r"<!--.*?-->\s*", "", path.read_text(encoding="utf-8"), flags=re.S).strip()
        style = re.search(r"<style>(.*?)</style>\s*", svg, re.S)
        if style:
            svg = svg.replace(style.group(0), "")
        w = int(re.search(r'data-width="(\d+)"', svg).group(1))
        # Shrink to MIN_DIAGRAM_SCALE at most, then scroll; a near miss (down to 0.6) still fits.
        min_w = round(w * MIN_DIAGRAM_SCALE)
        if w * 0.6 <= DIAGRAM_ROOM < min_w:
            min_w = DIAGRAM_ROOM
        self.diagram_css[key] = (html.unescape(style.group(1)) if style else "") + (
            f"#dg-{key}{{width:100%;max-width:{w}px;min-width:{min_w}px}}")
        return svg, w

    def diagram(self, code: str, env: dict, line: int) -> str:
        page: Page = env["page"]
        key = diagram_key(code)
        where = f"{page.entry.source}:{line}"
        keys = [key, f"{key}-tb"] if LR_FLOWCHART.match(code) else [key]
        missing = [k for k in keys if not (DIAGRAM_DIR / f"{k}.svg").exists()]
        if missing:
            msg = (f"{where}: the mermaid block has no rendered SVG (website/assets/diagrams/{missing[0]}.svg); "
                   "run python scripts/build_docs.py --render-diagrams")
            self.errors.append(msg) if self.check else self.warnings.append(msg)
            return self.code_html(code, "mermaid")
        svg, w = self._diagram_svg(key)
        full = ""
        if len(keys) > 1 and w * MIN_DIAGRAM_SCALE > DIAGRAM_ROOM:
            # Too wide for the column even scaled down: show it top to bottom, and the author's
            # left-to-right layout in the full-size view.
            svg, _ = self._diagram_svg(keys[1])
            full = f' data-full="{env["root"]}assets/diagrams/{key}.svg"'
        label = env.get("section") or page.title or page.entry.label
        kind = "Sequence diagram" if 'aria-roledescription="sequence"' in svg[:600] else "Diagram"
        svg = svg.replace('aria-label="Diagram"', f'aria-label="{attr(kind + ": " + label)}"', 1)
        page.diagrams.append(key)
        return (f'<figure class="diagram" data-diagram{full}>\n<div class="diagram-scroll" tabindex="0" role="group" '
                f'aria-label="{attr(kind)}, scrolls sideways">{svg}</div>\n'
                '<button type="button" class="diagram-expand" data-diagram-expand aria-label="Open the diagram full size">'
                '<svg class="icon" aria-hidden="true"><use href="#i-expand"/></svg></button>\n</figure>\n')

    def _r_link(self, r, tokens, idx, options, env) -> str:
        tok = tokens[idx]
        href = tok.attrGet("href") or ""
        new = self.href(href, env)
        tok.attrSet("href", new)
        if re.match(r"^https?://", new):
            tok.attrSet("rel", "noopener")
        return r.renderToken(tokens, idx, options, env)

    def _r_image(self, r, tokens, idx, options, env) -> str:
        tok = tokens[idx]
        alt = r.renderInlineAsText(tok.children or [], options, env)
        src, srcset = self.img_src(tok.attrGet("src") or "", env)
        parts = [f'src="{attr(src)}"', f'alt="{attr(alt)}"']
        if srcset:
            parts.append(f'srcset="{attr(srcset)}" sizes="(min-width: 1100px) 780px, 92vw"')
        title = tok.attrGet("title")
        if title:
            parts.append(f'title="{attr(title)}"')
        parts.append('loading="lazy" decoding="async"')
        return f"<img {' '.join(parts)}>"

    def _r_html(self, r, tokens, idx, options, env) -> str:
        content = tokens[idx].content

        def fix_url(m: re.Match) -> str:
            name, quote, value = m.group(1), m.group(2), html.unescape(m.group(3))
            if name == "href":
                return f"{m.group(0)[0]}{name}={quote}{attr(self.href(value, env))}{quote}"
            if name == "src":
                return f"{m.group(0)[0]}{name}={quote}{attr(self.img_src(value, env)[0])}{quote}"
            out = []
            for part in value.split(","):
                bits = part.strip().split()
                if bits:
                    bits[0] = self.img_src(bits[0], env)[0]
                    out.append(" ".join(bits))
            return f"{m.group(0)[0]}{name}={quote}{attr(', '.join(out))}{quote}"

        return re.sub(r"""\s(href|src|srcset)=(["'])(.*?)\2""", fix_url, content)

    # ---------------------------------------------------------------- links
    def resolve(self, path: str, page: Page) -> str:
        if path.startswith("/"):
            resolved = posixpath.normpath(path.lstrip("/"))
        else:
            resolved = posixpath.normpath(posixpath.join(posixpath.dirname(page.entry.source), path))
        if resolved == ".." or resolved.startswith("../"):
            raise BuildError(f"{page.entry.source}: link {path!r} leaves the repository")
        return resolved

    def page_url(self, target: Page, anchor: str, root: str) -> str:
        return f"{root}docs/{target.entry.slug}/" + (f"#{anchor}" if anchor else "")

    def href(self, href: str, env: dict) -> str:
        page: Page = env["page"]
        root: str = env["root"]
        raw = href.strip()
        if not raw:
            self.errors.append(f"{page.entry.source}: empty link")
            return raw
        if raw.startswith("#"):
            self.anchor_checks.append((page.entry.source, page.entry.source, unquote(raw[1:])))
            return raw
        if raw.startswith(SITE_URL):
            return root + raw[len(SITE_URL):]
        gh = re.match(rf"^https?://github\.com/{re.escape(REPO_SLUG)}/(?:blob|tree|edit)/main/([^#?]+)(?:#(.*))?$", raw)
        if gh:
            path, anchor = gh.group(1).rstrip("/"), gh.group(2) or ""
            if is_ignored(path):
                self.errors.append(f"{page.entry.source}: links the git-ignored (internal) file {path}")
            elif path in self.by_source and "/edit/" not in raw:
                self.anchor_checks.append((page.entry.source, path, unquote(anchor)))
                return self.page_url(self.by_source[path], anchor, root)
            return raw
        parts = urlsplit(raw)
        if parts.scheme or raw.startswith("//"):
            return raw
        try:
            resolved = self.resolve(unquote(parts.path), page) if parts.path else page.entry.source
        except BuildError as err:
            self.errors.append(str(err))
            return raw
        anchor = unquote(parts.fragment)
        suffix = f"#{parts.fragment}" if parts.fragment else ""
        if is_ignored(resolved):
            self.errors.append(f"{page.entry.source}: links the git-ignored (internal) file {resolved}")
            return raw
        if resolved in self.by_source:
            self.anchor_checks.append((page.entry.source, resolved, anchor))
            return self.page_url(self.by_source[resolved], parts.fragment, root)
        if resolved == DOCS_HOME_SOURCE:
            if anchor:
                self.errors.append(f"{page.entry.source}: links {raw}; the docs home has no #{anchor}")
            return f"{root}docs/"
        full = ROOT / resolved
        if resolved == ".":
            return f"{REPO}/tree/main"
        if full.is_dir():
            return f"{REPO}/tree/main/{resolved}{suffix}"
        if full.is_file():
            return f"{REPO}/blob/main/{resolved}{suffix}"
        self.errors.append(f"{page.entry.source}: broken link {href!r} ({resolved} does not exist)")
        return raw

    def img_src(self, src: str, env: dict) -> tuple[str, str]:
        page: Page = env["page"]
        root: str = env["root"]
        raw = src.strip()
        parts = urlsplit(raw)
        if parts.scheme in ("http", "https"):
            page.img_hosts.add(f"{parts.scheme}://{parts.netloc}")
            return raw, ""
        if parts.scheme or not raw:
            return raw, ""
        try:
            resolved = self.resolve(unquote(parts.path), page)
        except BuildError as err:
            self.errors.append(str(err))
            return raw, ""
        if is_ignored(resolved):
            self.errors.append(f"{page.entry.source}: uses the git-ignored image {resolved}")
            return raw, ""
        if not (ROOT / resolved).is_file():
            self.errors.append(f"{page.entry.source}: image {src!r} not found ({resolved})")
            return raw, ""
        # README screenshots are the display SVGs (docs/screenshots/<name>.svg); the site already
        # serves the same files from assets/screenshots/ (website/sync-screenshots.sh), so point there
        # instead of copying them into docs/_media/.
        shot = re.fullmatch(r"docs/screenshots/([\w-]+\.svg)", resolved)
        if shot and (self.out / "assets" / "screenshots" / shot.group(1)).is_file():
            return f"{root}assets/screenshots/{shot.group(1)}", ""
        self.media[resolved] = resolved
        return f"{root}docs/_media/{resolved}", ""

    # ---------------------------------------------------------------- render one page
    def source_text(self, entry: Entry) -> str:
        text = (ROOT / entry.source).read_text(encoding="utf-8")
        if entry.source == "README.md":
            # The README's centred logo, badges and link row are GitHub chrome; the site has its own.
            first = re.search(r"^## ", text, re.M)
            if first:
                title = "# Introduction\n\nThe control plane for your own GPUs.\n\n"
                self.line_offset[entry.source] = text[:first.start()].count("\n") - title.count("\n")
                text = title + text[first.start():]
            text = re.sub(r"\n<p align=\"center\">\s*<br>.*\Z", "\n", text, flags=re.S)
        return text

    def render(self, page: Page) -> None:
        env = {"page": page, "root": "../../", "section": ""}
        tokens = self.md.parse(self.source_text(page.entry), env)
        section = ""  # diagrams are named after the heading of their section
        for i, tok in enumerate(tokens):
            if tok.type == "inline" and i > 0 and tokens[i - 1].type == "heading_open":
                section = self._inline_text(tok)
            elif tok.type == "fence" and (tok.info or "").strip().startswith("mermaid"):
                tok.meta["section"] = section
        body = self._render_with_sections(tokens, env)
        h1 = re.search(r"<h1\b([^>]*)>(.*?)</h1>\s*", body, re.S)
        if h1:
            page.title_html = h1.group(2).strip()
            page.title = strip_tags(page.title_html)
            hid = re.search(r'id="([^"]+)"', h1.group(1))
            page.h1_id = hid.group(1) if hid else ""
            body = body.replace(h1.group(0), "", 1)
        else:
            page.title = page.entry.label
            page.title_html = escapeHtml(page.title)
        body = re.sub(r"<code>([^<]{1,28})</code>", r'<code class="nw">\1</code>', body)
        page.body = body
        first_p = re.search(r"<p>(.*?)</p>", body, re.S)
        page.description = clip(strip_tags(first_p.group(1)) if first_p else page.entry.blurb, 158)
        page.words = len(strip_tags(body).split())
        page.updated = git_date(page.entry.source)

    def _render_with_sections(self, tokens, env) -> str:
        out = []
        r = self.md.renderer
        options = self.md.options
        # markdown-it renders a flat token list; walk it so diagrams know their section heading.
        start = 0
        for i, tok in enumerate(tokens):
            if tok.type == "fence" and "section" in tok.meta:
                out.append(r.render(tokens[start:i], options, env))
                env["section"] = tok.meta["section"]
                out.append(r.render(tokens[i:i + 1], options, env))
                start = i + 1
        out.append(r.render(tokens[start:], options, env))
        return "".join(out)

    # ---------------------------------------------------------------- shared chrome
    def shared(self) -> dict[str, str]:
        index = (SITE / "index.html").read_text(encoding="utf-8")
        parts = {}
        for name in ("sprite", "banner", "header", "footer", "search"):
            m = re.search(rf"<!-- shared:{name} -->\n?(.*?)\n?\s*<!-- /shared:{name} -->", index, re.S)
            if not m:
                raise BuildError(f"website/index.html has no <!-- shared:{name} --> block")
            parts[name] = m.group(1).strip()
        return parts

    @staticmethod
    def rebase(fragment: str, root: str) -> str:
        def fix(url: str) -> str:
            if not url or url.startswith(("#", "/")) or re.match(r"^[a-z][a-z0-9+.-]*:", url, re.I):
                return url
            if url == "./":
                return root or "./"
            return root + url

        fragment = re.sub(r'(\s(?:href|src)=")([^"]*)"', lambda m: m.group(1) + fix(m.group(2)) + '"', fragment)
        fragment = re.sub(r'(\ssrcset=")([^"]*)"', lambda m: m.group(1) + ", ".join(
            fix(p.strip()) for p in m.group(2).split(",")) + '"', fragment)
        fragment = re.sub(r'(<a\b[^>]*?\shref=")#([^"]+)"', lambda m: f'{m.group(1)}{root}#{m.group(2)}"', fragment)
        return fragment

    def chrome(self, root: str, in_docs: bool = True) -> dict[str, str]:
        parts = {k: self.rebase(v, root) for k, v in self._shared.items()}
        if in_docs:
            parts["header"] = re.sub(r'(<a\b[^>]*data-nav="docs"[^>]*?)>', r'\1 aria-current="page">', parts["header"], count=1)
        return parts

    # ---------------------------------------------------------------- templates
    def head(self, *, title: str, description: str, canonical: str, root: str, og_type: str,
             extra_css: list[str], img_hosts: set[str], jsonld: dict) -> str:
        img_src = " ".join(["'self'", "data:", *sorted(img_hosts)])
        csp = (f"default-src 'self'; script-src 'self' '{THEME_HASH}'; style-src 'self'; img-src {img_src}; "
               "font-src 'self'; connect-src 'self' https://api.github.com; manifest-src 'self'; "
               "object-src 'none'; base-uri 'self'; form-action 'none'")
        css = "\n".join(f'  <link rel="stylesheet" href="{root}{c}">' for c in ["assets/css/site.css", "assets/css/docs.css", *extra_css])
        return f"""<!DOCTYPE html>
<html lang="en" class="docs">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <meta http-equiv="Content-Security-Policy" content="{csp}">
  <meta name="referrer" content="strict-origin-when-cross-origin">
  <title>{escapeHtml(title)}</title>
  <meta name="description" content="{attr(description)}">
  <link rel="canonical" href="{canonical}">
  <meta name="color-scheme" content="dark light">
  <meta name="theme-color" content="#0E0F12" media="(prefers-color-scheme: dark)">
  <meta name="theme-color" content="#F4F3F0" media="(prefers-color-scheme: light)">
  <meta property="og:type" content="{og_type}">
  <meta property="og:site_name" content="GPU Deck">
  <meta property="og:title" content="{attr(title)}">
  <meta property="og:description" content="{attr(description)}">
  <meta property="og:url" content="{canonical}">
  <meta property="og:image" content="{SITE_URL}assets/brand/og-image.png">
  <meta property="og:image:width" content="1280">
  <meta property="og:image:height" content="640">
  <meta name="twitter:card" content="summary_large_image">
  <link rel="icon" href="{root}favicon.ico" sizes="48x48">
  <link rel="icon" href="{root}favicon.svg" type="image/svg+xml">
  <link rel="apple-touch-icon" href="{root}apple-touch-icon.png">
  <link rel="manifest" href="{root}site.webmanifest">
  <script>{THEME_INIT}</script>
  <link rel="preload" href="{root}assets/fonts/figtree-latin-wght.woff2" as="font" type="font/woff2" crossorigin>
{css}
  <script src="{root}assets/js/site.js" defer></script>
  <script src="{root}assets/js/docs.js" defer></script>
  <script type="application/ld+json">{json.dumps(jsonld, separators=(",", ":"))}</script>
</head>"""

    def sidebar(self, root: str, current: str | None) -> str:
        groups = []
        for group, _, _ in [*NAV, ("More", "", [])]:
            items = [p for p in self.pages if p.group == group]
            if not items:
                continue
            lis = "\n".join(
                f'          <li><a href="{root}docs/{p.entry.slug}/"'
                f'{" aria-current=\"page\"" if p.entry.slug == current else ""}>{escapeHtml(p.entry.label)}</a></li>'
                for p in items)
            groups.append(f'        <li class="side-group">\n          <p class="side-title">{escapeHtml(group)}</p>\n'
                          f'          <ul>\n{lis}\n          </ul>\n        </li>')
        home_cur = ' aria-current="page"' if current is None else ""
        return f"""    <aside class="docs-side" id="docs-nav" aria-label="Documentation">
      <div class="docs-side-inner">
        <a class="side-home" href="{root}docs/"{home_cur}><svg class="icon" aria-hidden="true"><use href="#i-book"/></svg>Documentation</a>
        <button type="button" class="side-search" data-search-open aria-haspopup="dialog"><svg class="icon" aria-hidden="true"><use href="#i-search"/></svg><span>Search</span><kbd data-kbd-mod>⌘K</kbd></button>
        <ul class="side-groups">
{chr(10).join(groups)}
        </ul>
      </div>
    </aside>"""

    def mobile_bar(self, crumb: str) -> str:
        return f"""  <div class="docs-bar">
    <div class="wrap docs-bar-inner">
      <button type="button" class="docs-bar-btn" aria-controls="docs-nav" aria-expanded="false"><svg class="icon" aria-hidden="true"><use href="#i-sidebar"/></svg>Menu</button>
      <span class="docs-bar-crumb">{crumb}</span>
    </div>
  </div>"""

    def toc_list(self, page: Page) -> str:
        items = []
        for level, hid, text in page.toc:
            cls = ' class="toc-sub"' if level == 3 else ""
            items.append(f'<li{cls}><a href="#{hid}">{escapeHtml(text)}</a></li>')
        return "\n".join(items)

    def doc_page(self, page: Page, index: int, order: list[Page]) -> str:
        root = "../../"
        e = page.entry
        chrome = self.chrome(root)
        prev_p = order[index - 1] if index > 0 else None
        next_p = order[index + 1] if index + 1 < len(order) else None
        canonical = f"{SITE_URL}{page.url}"
        jsonld = {
            "@context": "https://schema.org", "@type": "TechArticle", "headline": page.title,
            "description": page.description, "url": canonical, "isPartOf": {"@type": "WebSite", "name": "GPU Deck", "url": SITE_URL},
            "author": {"@type": "Person", "name": "Suresh Konakanchi"},
            **({"dateModified": page.updated.isoformat()} if page.updated else {}),
            "breadcrumb": {"@type": "BreadcrumbList", "itemListElement": [
                {"@type": "ListItem", "position": 1, "name": "Docs", "item": f"{SITE_URL}docs/"},
                {"@type": "ListItem", "position": 2, "name": page.entry.label, "item": canonical}]},
        }
        extra_css = ["docs/diagrams.css"] if page.diagrams else []
        head = self.head(title=f"{page.title} · GPU Deck docs", description=page.description,
                         canonical=canonical, root=root, og_type="article", extra_css=extra_css,
                         img_hosts=page.img_hosts, jsonld=jsonld)
        minutes = max(1, round(page.words / 230))
        toc = self.toc_list(page)
        toc_side = f"""
      <aside class="docs-toc" aria-labelledby="toc-title">
        <div class="docs-toc-inner">
          <p class="toc-title" id="toc-title">On this page</p>
          <ol class="toc-list" data-toc>
{toc}
          </ol>
          <div class="toc-foot">
            <a href="#main" data-to-top><svg class="icon" aria-hidden="true"><use href="#i-up"/></svg>Back to top</a>
            <a href="{REPO}/edit/main/{e.source}"><svg class="icon" aria-hidden="true"><use href="#i-edit"/></svg>Edit this page</a>
          </div>
        </div>
      </aside>""" if page.toc else ""
        toc_inline = f"""
        <details class="toc-inline">
          <summary>On this page<svg class="icon" aria-hidden="true"><use href="#i-chevron"/></svg></summary>
          <ol class="toc-list">
{toc}
          </ol>
        </details>""" if page.toc else ""

        def pager_link(p: Page | None, rel: str) -> str:
            if not p:
                return '<span class="pager-gap"></span>'
            k = "Previous" if rel == "prev" else "Next"
            icon = "i-left" if rel == "prev" else "i-right"
            return (f'<a class="pager-{rel}" href="{root}docs/{p.entry.slug}/" rel="{rel}">'
                    f'<span class="pager-k">{k}</span><span class="pager-t">'
                    f'<svg class="icon" aria-hidden="true"><use href="#{icon}"/></svg>{escapeHtml(p.entry.label)}</span></a>')

        updated = (f'<span>Updated <time datetime="{page.updated.isoformat()}">{human_date(page.updated)}</time></span>'
                   if page.updated else "")
        crumb = (f'<a href="{root}docs/">Docs</a><span aria-hidden="true">/</span>'
                 f'{escapeHtml(page.group)}<span aria-hidden="true">/</span><b>{escapeHtml(e.label)}</b>')
        return f"""{head}
<body class="doc-page" data-root="{root}">
  <a class="skip" href="#main">Skip to content</a>
  {chrome["sprite"]}
  {chrome["banner"]}
  {chrome["header"]}
{self.mobile_bar(crumb)}
  <div class="wrap docs-shell">
{self.sidebar(root, e.slug)}
    <main id="main" class="docs-main">
      <article class="docs-article">
        <nav class="crumbs" aria-label="Breadcrumb">
          <ol>
            <li><a href="{root}docs/">Docs</a></li>
            <li><span>{escapeHtml(page.group)}</span></li>
            <li><a href="{root}docs/{e.slug}/" aria-current="page">{escapeHtml(e.label)}</a></li>
          </ol>
        </nav>
        <header class="docs-head">
          <h1{f' id="{page.h1_id}"' if page.h1_id else ""}>{page.title_html}</h1>
          <p class="docs-meta"><span>{minutes} min read</span>{updated}<a href="{REPO}/edit/main/{e.source}"><svg class="icon" aria-hidden="true"><use href="#i-edit"/></svg>Edit on GitHub</a></p>
        </header>{toc_inline}
        <div class="prose">
{page.body}
        </div>
        <nav class="pager" aria-label="Previous and next page">
          {pager_link(prev_p, "prev")}
          {pager_link(next_p, "next")}
        </nav>
        <footer class="docs-foot">
          <a href="{REPO}/edit/main/{e.source}"><svg class="icon" aria-hidden="true"><use href="#i-edit"/></svg>Edit this page on GitHub</a>
          <a href="{REPO}/issues/new/choose"><svg class="icon" aria-hidden="true"><use href="#i-chat"/></svg>Report a problem with this page</a>
        </footer>
      </article>{toc_side}
    </main>
  </div>
  {chrome["footer"]}
  {chrome["search"]}
  <p class="sr-only" aria-live="polite" data-announce></p>
</body>
</html>
"""

    def home_page(self) -> str:
        root = "../"
        chrome = self.chrome(root)
        canonical = f"{SITE_URL}docs/"
        jsonld = {"@context": "https://schema.org", "@type": "CollectionPage", "name": "GPU Deck documentation",
                  "url": canonical, "isPartOf": {"@type": "WebSite", "name": "GPU Deck", "url": SITE_URL}}
        description = ("Deploy the GPU Deck control plane, add GPU machines, serve models through OpenAI, "
                       "Anthropic and Ollama APIs, automate the fleet with rules and layouts, and run it.")
        head = self.head(title="Documentation · GPU Deck", description=description, canonical=canonical,
                         root=root, og_type="website", extra_css=[], img_hosts=set(), jsonld=jsonld)
        by_slug = {p.entry.slug: p for p in self.pages}
        start_cards = []
        for n, slug in enumerate(START_HERE, 1):
            p = by_slug.get(slug)
            if not p:
                continue
            start_cards.append(
                f'<li><a class="start-card" href="{root}docs/{slug}/"><span class="start-n">0{n}</span>'
                f'<b>{escapeHtml(p.entry.label)}</b><span>{escapeHtml(p.entry.blurb)}</span>'
                f'<svg class="icon" aria-hidden="true"><use href="#i-arrow"/></svg></a></li>')
        groups = []
        for group, desc, _ in [*NAV, ("More", "Other documents.", [])]:
            items = [p for p in self.pages if p.group == group]
            if not items:
                continue
            gid = gh_slug(group)
            lis = "\n".join(
                f'<li><a href="{root}docs/{p.entry.slug}/"><b>{escapeHtml(p.entry.label)}</b>'
                f'<span>{escapeHtml(p.entry.blurb or p.description)}</span></a></li>' for p in items)
            groups.append(f"""        <section class="dh-group" aria-labelledby="g-{gid}">
          <h2 id="g-{gid}">{escapeHtml(group)}</h2>
          <p>{escapeHtml(desc)}</p>
          <ul>
{lis}
          </ul>
        </section>""")
        crumb = "<b>Documentation</b>"
        return f"""{head}
<body class="doc-page doc-home" data-root="{root}">
  <a class="skip" href="#main">Skip to content</a>
  {chrome["sprite"]}
  {chrome["banner"]}
  {chrome["header"]}
{self.mobile_bar(crumb)}
  <div class="wrap docs-shell">
{self.sidebar(root, None)}
    <main id="main" class="docs-main">
      <div class="docs-home">
        <header class="dh-hero">
          <p class="eyebrow">Documentation</p>
          <h1>Everything to run GPU Deck on your own GPUs.</h1>
          <p class="dh-lead">Deploy the control plane once, add GPU machines with one command, serve models through OpenAI, Anthropic and Ollama APIs, and let rules and layouts keep the fleet busy. The specifications behind every API are here too.</p>
          <button type="button" class="dh-search" data-search-open aria-haspopup="dialog"><svg class="icon" aria-hidden="true"><use href="#i-search"/></svg><span>Search the docs</span><kbd data-kbd-mod>⌘K</kbd></button>
        </header>
        <section class="dh-start" aria-labelledby="start-title">
          <h2 class="mini-label" id="start-title">Start here</h2>
          <ol class="start-grid">
            {"".join(start_cards)}
          </ol>
        </section>
        <div class="dh-groups">
{chr(10).join(groups)}
        </div>
      </div>
    </main>
  </div>
  {chrome["footer"]}
  {chrome["search"]}
  <p class="sr-only" aria-live="polite" data-announce></p>
</body>
</html>
"""

    # ---------------------------------------------------------------- outputs
    def search_index(self, order: list[Page]) -> str:
        docs = []
        for p in order:
            sections = []
            heads = list(re.finditer(r'<h([23]) id="([^"]+)"[^>]*>(.*?)</h\1>', p.body, re.S))
            bounds = [(None, p.title, 0)] + [(m.group(2), strip_tags(m.group(3)), m.end()) for m in heads]
            for n, (anchor, heading, start) in enumerate(bounds):
                end = heads[n].start() if n < len(heads) else len(p.body)
                text = strip_tags(re.sub(r'<figure class="diagram".*?</figure>', " ", p.body[start:end], flags=re.S))
                if not text and anchor is None:
                    continue
                sections.append([heading, anchor or "", clip(text, 900)])
            docs.append({"t": p.title if p.entry.source != "README.md" else "Introduction",
                         "l": p.entry.label, "g": p.group, "u": f"{p.entry.slug}/", "s": sections})
        return json.dumps({"v": 1, "docs": docs}, ensure_ascii=False, separators=(",", ":"))

    def sitemap(self, order: list[Page]) -> str:
        rows = [(SITE_URL, git_date("website/index.html"), "1.0"), (f"{SITE_URL}docs/", None, "0.8")]
        rows += [(f"{SITE_URL}{p.url}", p.updated, "0.6") for p in order]
        body = []
        for loc, day, prio in rows:
            lastmod = f"\n    <lastmod>{day.isoformat()}</lastmod>" if day else ""
            body.append(f"  <url>\n    <loc>{loc}</loc>{lastmod}\n    <priority>{prio}</priority>\n  </url>")
        return ('<?xml version="1.0" encoding="UTF-8"?>\n<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'
                + "\n".join(body) + "\n</urlset>\n")

    def not_found_page(self) -> str:
        root = BASE_PATH  # served for any missing path, so every URL is absolute
        chrome = self.chrome(root, in_docs=False)
        head = self.head(title="Page not found · GPU Deck", description="This page does not exist.",
                         canonical=SITE_URL, root=root, og_type="website", extra_css=[], img_hosts=set(),
                         jsonld={"@context": "https://schema.org", "@type": "WebPage", "name": "Page not found"})
        head = head.replace('<html lang="en" class="docs">', '<html lang="en">')
        head = head.replace(f'  <link rel="stylesheet" href="{root}assets/css/docs.css">\n', "")
        head = head.replace(f'  <script src="{root}assets/js/docs.js" defer></script>\n', "")
        head = head.replace('<meta name="referrer"', '<meta name="robots" content="noindex">\n  <meta name="referrer"')
        return f"""{head}
<body class="not-found-page" data-root="{root}">
  <a class="skip" href="#main">Skip to content</a>
  {chrome["sprite"]}
  {chrome["header"]}
  <main id="main" class="not-found">
    <div class="wrap">
      <p class="nf-code" aria-hidden="true">404</p>
      <h1>This page is not on the deck.</h1>
      <p>The link may be old, or the page has moved. Try the documentation, or search it.</p>
      <div class="cta-row">
        <a class="btn btn-primary" href="{root}">Go to the home page</a>
        <a class="btn btn-secondary" href="{root}docs/">Browse the docs</a>
        <button type="button" class="btn btn-ghost" data-search-open aria-haspopup="dialog"><svg class="icon" aria-hidden="true"><use href="#i-search"/></svg>Search</button>
      </div>
    </div>
  </main>
  {chrome["search"]}
  <p class="sr-only" aria-live="polite" data-announce></p>
</body>
</html>
"""

    def check_hand_written(self) -> None:
        for name in ("index.html",):
            text = (SITE / name).read_text(encoding="utf-8")
            if f"<script>{THEME_INIT}</script>" not in text:
                self.errors.append(f"website/{name}: the inline theme script differs from THEME_INIT in build_docs.py")
            if f"'{THEME_HASH}'" not in text:
                self.errors.append(f"website/{name}: the Content-Security-Policy needs '{THEME_HASH}'")

    def build(self) -> None:
        t0 = time.time()
        self.check_hand_written()
        self.discover()
        self._shared = self.shared()
        for page in self.pages:
            self.render(page)
        by_source = self.by_source
        for src, dst, anchor in self.anchor_checks:
            if anchor and dst in by_source and anchor not in by_source[dst].ids and not anchor.startswith("L"):
                self.errors.append(f"{src}: links {dst}#{anchor}, but {dst} has no such heading")
        order = [p for p in self.pages]
        docs_out = self.out / "docs"
        if docs_out.exists():
            shutil.rmtree(docs_out)
        docs_out.mkdir(parents=True)
        for n, page in enumerate(order):
            path = docs_out / page.entry.slug / "index.html"
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(self.doc_page(page, n, order), encoding="utf-8")
        (docs_out / "index.html").write_text(self.home_page(), encoding="utf-8")
        (docs_out / "search-index.json").write_text(self.search_index(order), encoding="utf-8")
        (docs_out / "diagrams.css").write_text(
            "/* Generated by scripts/build_docs.py from website/assets/diagrams/*.svg. */\n"
            + "\n".join(self.diagram_css[k] for k in sorted(self.diagram_css)) + "\n", encoding="utf-8")
        for rel in self.media:
            dest = docs_out / "_media" / rel
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(ROOT / rel, dest)
        (self.out / "sitemap.xml").write_text(self.sitemap(order), encoding="utf-8")
        (self.out / "404.html").write_text(self.not_found_page(), encoding="utf-8")
        self.errors += check_site(self.out, set(by_source))
        kb = (docs_out / "search-index.json").stat().st_size // 1024
        print(f"built {len(order)} doc pages + docs home into {docs_out.relative_to(self.out.parent) if self.out == SITE else docs_out}"
              f" (search index {kb} KB, {len(self.diagram_css)} diagrams) in {time.time() - t0:.1f}s")


# ------------------------------------------------------------------------------------ site check
class _Collect(HTMLParser):
    URL_ATTRS = {"href", "src", "srcset", "poster"}

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.ids: set[str] = set()
        self.urls: list[tuple[str, str, str]] = []  # (tag, attr, value)

    def handle_starttag(self, tag, attrs):
        for k, v in attrs:
            if v is None:
                continue
            if k == "id" or (tag == "a" and k == "name"):
                self.ids.add(v)
            elif k in self.URL_ATTRS:
                if k == "srcset":
                    for part in v.split(","):
                        bits = part.strip().split()
                        if bits:
                            self.urls.append((tag, k, bits[0]))
                else:
                    self.urls.append((tag, k, v))

    handle_startendtag = handle_starttag


def check_site(site: Path, doc_sources: set[str]) -> list[str]:
    """Every relative link, asset and #anchor of every built page resolves; GitHub links obey the policy."""
    errors: list[str] = []
    parsed: dict[Path, _Collect] = {}

    def load(path: Path) -> _Collect:
        if path not in parsed:
            c = _Collect()
            c.feed(path.read_text(encoding="utf-8"))
            parsed[path] = c
        return parsed[path]

    pages = sorted(p for p in site.rglob("*.html") if "node_modules" not in p.parts)
    for page in pages:
        rel_page = page.relative_to(site).as_posix()
        c = load(page)
        for tag, name, url in c.urls:
            if url.startswith(("mailto:", "data:", "tel:", "javascript:")):
                continue
            if re.match(r"^https?://", url):
                gh = re.match(rf"^https?://github\.com/{re.escape(REPO_SLUG)}/(blob|tree)/main/([^#?]+)", url)
                if gh:
                    path = gh.group(2).rstrip("/")
                    if is_ignored(path):
                        errors.append(f"{rel_page}: links the git-ignored (internal) file {path} on GitHub")
                    elif path in doc_sources or path == DOCS_HOME_SOURCE:
                        errors.append(f"{rel_page}: links {path} on GitHub; link its page on the site instead")
                continue
            parts = urlsplit(url)
            if parts.scheme or url.startswith("//"):
                continue
            path = unquote(parts.path)
            if not path:
                target = page
            elif path.startswith(BASE_PATH):
                target = site / path[len(BASE_PATH):]
            elif path.startswith("/"):
                errors.append(f"{rel_page}: {url!r} is outside {BASE_PATH}")
                continue
            else:
                target = site / posixpath.normpath(posixpath.join(posixpath.dirname(rel_page), path))
            if path.endswith("/") or target.is_dir():
                target = target / "index.html"
            if not target.exists():
                errors.append(f"{rel_page}: broken {tag} {name}={url!r}")
                continue
            if parts.fragment and target.suffix == ".html":
                if parts.fragment not in load(target).ids:
                    errors.append(f"{rel_page}: {url!r} points at a missing #{parts.fragment}")
    return errors


# ------------------------------------------------------------------------------------ main
def mermaid_blocks() -> dict[str, tuple[str, str]]:
    """Every ```mermaid block of the docs the site renders: key -> (code, "source:line")."""
    b = Builder(SITE, check=False)
    b.discover()
    md = MarkdownIt("commonmark")
    found: dict[str, tuple[str, str]] = {}
    for page in b.pages:
        for tok in md.parse(b.source_text(page.entry)):
            if tok.type == "fence" and (tok.info or "").strip().split(maxsplit=1)[:1] == ["mermaid"]:
                line = (tok.map[0] + 1 if tok.map else 0) + b.line_offset.get(page.entry.source, 0)
                key = diagram_key(tok.content)
                found.setdefault(key, (tok.content, f"{page.entry.source}:{line}"))
                if LR_FLOWCHART.match(tok.content):  # the narrow variant for the article column
                    tb = LR_FLOWCHART.sub(lambda m: m.group(0)[:-2] + "TB", tok.content, count=1)
                    found.setdefault(f"{key}-tb", (tb, f"{page.entry.source}:{line} (top to bottom)"))
    return found


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--check", action="store_true", help="build in a temporary directory and only report problems")
    ap.add_argument("--render-diagrams", action="store_true", help="render missing diagram SVGs (needs Node)")
    ap.add_argument("--force", action="store_true", help="with --render-diagrams: render every diagram again")
    args = ap.parse_args()
    try:
        if args.render_diagrams:
            render_diagrams(mermaid_blocks(), args.force)
        if args.check:
            with tempfile.TemporaryDirectory() as tmp:
                out = Path(tmp) / "website"
                shutil.copytree(SITE, out, ignore=shutil.ignore_patterns("docs"))
                builder = Builder(out, check=True)
                builder.build()
        else:
            builder = Builder(SITE, check=False)
            builder.build()
    except BuildError as err:
        print(f"error: {err}", file=sys.stderr)
        return 1
    for w in builder.warnings:
        print(f"warning: {w}", file=sys.stderr)
    if builder.errors:
        for e in builder.errors:
            print(f"error: {e}", file=sys.stderr)
        print(f"{len(builder.errors)} problem(s); the build failed", file=sys.stderr)
        return 1
    print("site check passed: links, anchors, assets and link policy" + (" (check mode, nothing written)" if args.check else ""))
    return 0


if __name__ == "__main__":
    sys.exit(main())
