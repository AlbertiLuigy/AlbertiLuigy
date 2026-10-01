#!/usr/bin/env python3
"""
Atualiza automaticamente o README.md do perfil com:
- Ultimos repositorios (projetos) criados/atualizados
- Atividade recente (commits, PRs, issues)
- Grafico de atividade mensal (SVG inline)

Usa a API oficial do GitHub via `gh` ou `requests`.
Marcadores HTML comentados no README definem onde cada bloco vai.
"""

import os
import re
import sys
import json
import math
import datetime
import subprocess
from pathlib import Path
from urllib.request import Request, urlopen
from urllib.error import URLError, HTTPError

GITHUB_USERNAME = "AlbertiLuigy"
README_PATH = Path(__file__).resolve().parent.parent / "README.md"
MAX_REPOS = 4
MAX_ACTIVITY = 6
DAYS_BACK = 30

GH_TOKEN = os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN")

API_HEADERS = {
    "Accept": "application/vnd.github+json",
    "User-Agent": "profile-readme-updater",
    "X-GitHub-Api-Version": "2022-11-28",
}
if GH_TOKEN:
    API_HEADERS["Authorization"] = f"Bearer {GH_TOKEN}"


def gh_api(path):
    url = f"https://api.github.com{path}"
    req = Request(url, headers=API_HEADERS)
    try:
        with urlopen(req, timeout=30) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except (URLError, HTTPError) as e:
        print(f"[WARN] Erro na API {path}: {e}", file=sys.stderr)
        return None


def replace_chunk(content, marker, chunk):
    pattern = re.compile(
        r"<!--\s*{}:START\s*-->[\s\S]*?<!--\s*{}:END\s*-->".format(marker, marker),
        re.MULTILINE,
    )
    chunk = f"<!-- {marker}:START -->\n{chunk.rstrip()}\n<!-- {marker}:END -->"
    if not pattern.search(content):
        print(f"[WARN] Marcador {marker} nao encontrado no README", file=sys.stderr)
        return content
    return pattern.sub(chunk, content)


def fmt_dt_relative(iso_str):
    """Retorna string relativa tipo '3 dias atras' a partir de ISO-8601."""
    if not iso_str:
        return ""
    try:
        dt = datetime.datetime.fromisoformat(iso_str.replace("Z", "+00:00"))
    except ValueError:
        return iso_str
    now = datetime.datetime.now(datetime.timezone.utc)
    diff = now - dt
    secs = int(diff.total_seconds())
    if secs < 60:
        return "agora mesmo"
    mins = secs // 60
    if mins < 60:
        return f"{mins} min atrás"
    hrs = mins // 60
    if hrs < 24:
        return f"{hrs} h atrás"
    days = hrs // 24
    if days < 30:
        return f"{days} dias atrás"
    months = days // 30
    if months < 12:
        return f"{months} mês(es) atrás"
    return f"{months // 12} ano(s) atrás"


def lang_color(lang):
    colors = {
        "Java": "#b07219",
        "JavaScript": "#f1e05a",
        "TypeScript": "#3178c6",
        "Python": "#3572A5",
        "HTML": "#e34c26",
        "CSS": "#563d7c",
        "PHP": "#4F5D95",
        "C": "#555555",
        "Shell": "#89e051",
        "Dockerfile": "#384d54",
    }
    return colors.get(lang, "#cccccc")


def fetch_repos():
    """Busca repositorios publicos ordenados por ultimo push."""
    params = f"per_page={MAX_REPOS + 6}&sort=pushed&type=owner"
    data = gh_api(f"/users/{GITHUB_USERNAME}/repos?{params}")
    if not data:
        return []
    repos = [r for r in data if not r.get("fork") and r.get("name") != GITHUB_USERNAME]
    return repos[:MAX_REPOS]


def build_repos_chunk(repos):
    if not repos:
        return "> Sem projetos públicos recentes. Volte em breve!\n"
    lines = []
    for r in repos:
        name = r["name"]
        url = r["html_url"]
        desc = (r.get("description") or "Sem descrição").strip()
        lang = r.get("language")
        stars = r.get("stargazers_count", 0)
        forks = r.get("forks_count", 0)
        updated = fmt_dt_relative(r.get("pushed_at"))
        lang_badge = ""
        if lang:
            c = lang_color(lang)
            lang_badge = (
                f'<img src="https://img.shields.io/badge/'
                f'{lang}-{c[1:]}.svg?style=flat-square&logoColor=white" height="18"/> '
            )
        meta = []
        if stars:
            meta.append(f"⭐ {stars}")
        if forks:
            meta.append(f"🍴 {forks}")
        meta.append(f"🕒 {updated}")
        meta_str = " · ".join(meta)
        lines.append(
            f"- [{name}]({url}) — {desc}\n"
            f"  <br>&nbsp;&nbsp;{lang_badge} <sub>{meta_str}</sub>"
        )
    return "\n".join(lines) + "\n"


def fetch_recent_activity():
    """Retorna eventos publicos recentes."""
    data = gh_api(f"/users/{GITHUB_USERNAME}/events/public?per_page={MAX_ACTIVITY + 10}")
    if not data:
        return []
    events = []
    for ev in data:
        etype = ev.get("type", "")
        repo_name = ev.get("repo", {}).get("name", "")
        repo_url = f"https://github.com/{repo_name}"
        created = ev.get("created_at")
        payload = ev.get("payload", {})
        text = None
        if etype == "PushEvent":
            commits = payload.get("commits", [])
            n = payload.get("distinct_size") or payload.get("size") or len(commits) or 1
            msg = commits[0]["message"].splitlines()[0] if commits else (
                payload.get("head", "")[:70] if payload.get("head") else ""
            )
            if len(msg) > 70:
                msg = msg[:67] + "..."
            branch = (payload.get("ref") or "").replace("refs/heads/", "")
            text = f"📌 {n} commit(s) em `{branch}` de [{repo_name.split('/')[-1]}]({repo_url})"
            if msg:
                text += f" — <sub>_{msg}_</sub>"
        elif etype == "CreateEvent":
            ref_type = payload.get("ref_type", "")
            if ref_type == "repository":
                text = f"🆕 Criou o repositório [{repo_name.split('/')[-1]}]({repo_url})"
            elif ref_type == "branch":
                text = f"🌿 Criou branch `{payload.get('ref')}` em [{repo_name.split('/')[-1]}]({repo_url})"
            else:
                continue
        elif etype == "PullRequestEvent":
            action = payload.get("action", "")
            pr = payload.get("pull_request", {})
            pr_url = pr.get("html_url", repo_url)
            title = (pr.get("title") or "").strip()
            if len(title) > 70:
                title = title[:67] + "..."
            emoji = "✅" if action == "closed" and pr.get("merged") else "🔀" if action == "closed" else "📬"
            action_pt = {"opened": "abriu", "closed": "fechou", "reopened": "reabriu"}.get(action, action)
            text = f"{emoji} {action_pt} PR em [{repo_name.split('/')[-1]}]({repo_url}) — <sub>_{title}_</sub>"
        elif etype == "IssuesEvent":
            action = payload.get("action", "")
            issue = payload.get("issue", {})
            title = (issue.get("title") or "").strip()
            if len(title) > 70:
                title = title[:67] + "..."
            text = f"🐛 {action} issue em [{repo_name.split('/')[-1]}]({repo_url}) — <sub>_{title}_</sub>"
        elif etype == "WatchEvent":
            text = f"⭐ Deu uma estrela em [{repo_name.split('/')[-1]}]({repo_url})"
        elif etype == "ForkEvent":
            text = f"🍴 Fez fork de [{repo_name.split('/')[-1]}]({repo_url})"
        else:
            continue
        events.append({"text": text, "created": created})
        if len(events) >= MAX_ACTIVITY:
            break
    return events


def build_activity_chunk(events):
    if not events:
        return "> Nenhuma atividade pública recente nos últimos dias.\n"
    lines = []
    for ev in events:
        when = fmt_dt_relative(ev["created"])
        lines.append(f"- {ev['text']}\n  <br>&nbsp;&nbsp;<sub>🕒 {when}</sub>")
    return "\n".join(lines) + "\n"


def build_monthly_graph_svg():
    """Constroi grafico de barras SVG inline com contrib dos ultimos 30 dias."""
    today = datetime.date.today()
    start = today - datetime.timedelta(days=DAYS_BACK - 1)

    events = gh_api(
        f"/users/{GITHUB_USERNAME}/events/public?per_page=100"
    ) or []

    buckets = {}
    for i in range(DAYS_BACK):
        d = start + datetime.timedelta(days=i)
        buckets[d.isoformat()] = 0

    for ev in events:
        try:
            dt = datetime.datetime.fromisoformat(ev["created_at"].replace("Z", "+00:00")).date()
        except Exception:
            continue
        key = dt.isoformat()
        if key in buckets:
            contributions = 1
            if ev.get("type") == "PushEvent":
                contributions = len(ev.get("payload", {}).get("commits", [1]))
            buckets[key] = buckets.get(key, 0) + contributions

    days = sorted(buckets.keys())
    values = [buckets[d] for d in days]
    total = sum(values)
    max_v = max(values) if values else 1
    if max_v == 0:
        max_v = 1

    width = 720
    height = 120
    padding_l = 36
    padding_r = 10
    padding_t = 18
    padding_b = 28
    chart_w = width - padding_l - padding_r
    chart_h = height - padding_t - padding_b
    n = len(days)
    bar_width = (chart_w / n) * 0.75
    gap = (chart_w / n) * 0.25

    bars_svg = []
    for i, v in enumerate(values):
        x = padding_l + i * (chart_w / n) + gap / 2
        bar_h = 0 if max_v == 0 else (v / max_v) * chart_h
        y = padding_t + (chart_h - bar_h)
        color_start = "#56a3ff"
        color_end = "#2f74c0"
        fill = color_end if v > 0 else "#334155"
        if v > 0:
            ratio = math.sqrt(v / max_v)
            r_ratio = int(int(color_start[1:3], 16) * (1 - ratio) + int(color_end[1:3], 16) * ratio)
            g_ratio = int(int(color_start[3:5], 16) * (1 - ratio) + int(color_end[3:5], 16) * ratio)
            b_ratio = int(int(color_start[5:7], 16) * (1 - ratio) + int(color_end[5:7], 16) * ratio)
            fill = f"#{r_ratio:02x}{g_ratio:02x}{b_ratio:02x}"
        title_day = datetime.date.fromisoformat(days[i]).strftime("%d/%m")
        bars_svg.append(
            f'<rect x="{x:.2f}" y="{y:.2f}" width="{bar_width:.2f}" height="{bar_h:.2f}" rx="2" '
            f'fill="{fill}" opacity="0.9"><title>{title_day}: {v}</title></rect>'
        )

    max_label = f"{max_v}"
    svg = f'''<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {width} {height}" width="100%" role="img" aria-label="Atividade dos últimos {DAYS_BACK} dias">
  <rect width="100%" height="100%" fill="transparent"/>
  <text x="{padding_l}" y="{padding_t - 6}" font-family="Segoe UI, sans-serif" font-size="11" fill="#94a3b8">Contribuições (últimos {DAYS_BACK} dias) · Total: <tspan font-weight="bold" fill="#2f74c0">{total}</tspan></text>
  <line x1="{padding_l}" y1="{padding_t + chart_h}" x2="{width - padding_r}" y2="{padding_t + chart_h}" stroke="#334155" stroke-width="1"/>
  <text x="{padding_l - 4}" y="{padding_t + chart_h + 1}" font-size="9" fill="#64748b" text-anchor="end">0</text>
  <text x="{padding_l - 4}" y="{padding_t + 4}" font-size="9" fill="#64748b" text-anchor="end">{max_label}</text>
  {''.join(bars_svg)}
  <text x="{padding_l}" y="{height - 8}" font-size="9" fill="#64748b">{start.strftime("%b %d")}</text>
  <text x="{width - padding_r}" y="{height - 8}" font-size="9" fill="#64748b" text-anchor="end">{today.strftime("%b %d")}</text>
</svg>'''
    return svg


def main():
    if not README_PATH.exists():
        print(f"[ERRO] README nao encontrado em {README_PATH}", file=sys.stderr)
        return 1

    content = README_PATH.read_text(encoding="utf-8")

    repos = fetch_repos()
    content = replace_chunk(content, "LATEST_PROJECTS", build_repos_chunk(repos))

    activity = fetch_recent_activity()
    content = replace_chunk(content, "RECENT_ACTIVITY", build_activity_chunk(activity))

    graph_svg = build_monthly_graph_svg()
    content = replace_chunk(content, "MONTHLY_ACTIVITY_GRAPH", f"<div align=\"center\">\n{graph_svg}\n</div>\n")

    README_PATH.write_text(content, encoding="utf-8")
    print("[OK] README atualizado com sucesso!")
    return 0


if __name__ == "__main__":
    sys.exit(main())
