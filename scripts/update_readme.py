#!/usr/bin/env python3
"""
Atualiza automaticamente o README.md do perfil com:
- Ultimos repositorios (projetos) criados/atualizados
- Atividade recente (commits, PRs, issues) SEM REPETICAO
- Grafico de atividade mensal renderizado como emoji/markdown (sem SVG quebrado)

Usa a API oficial do GitHub via urllib.
Marcadores HTML comentados no README definem onde cada bloco vai.
"""

import os
import re
import sys
import json
import datetime
from pathlib import Path
from urllib.request import Request, urlopen
from urllib.error import URLError, HTTPError

GITHUB_USERNAME = "AlbertiLuigy"
README_PATH = Path(__file__).resolve().parent.parent / "README.md"
MAX_REPOS = 4
MAX_ACTIVITY = 5
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


def pretty_repo_name(name):
    """Torna nomes de repositorio mais legiveis."""
    pretty = name.replace("-", " ").replace("_", " ")
    pretty = re.sub(r"\s+", " ", pretty).strip()
    words = pretty.split()
    if len(words) > 1 and words[0].lower() in {"projeto", "app", "demo", "api", "sistema"}:
        pretty = " ".join(words[1:])
    if not pretty:
        return name
    return pretty.title()


def fetch_repos():
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
        display = pretty_repo_name(name)
        url = r["html_url"]
        desc = (r.get("description") or "").strip()
        lang = r.get("language") or ""
        stars = r.get("stargazers_count", 0) or 0
        forks = r.get("forks_count", 0) or 0
        updated = fmt_dt_relative(r.get("pushed_at"))
        if not lang:
            lang_text = ""
        else:
            lang_text = f"**`{lang}`**"
        meta = []
        if lang_text:
            meta.append(lang_text)
        if stars:
            meta.append(f"⭐ {stars}")
        if forks:
            meta.append(f"🍴 {forks}")
        meta.append(f"🕒 {updated}")
        meta_str = " · ".join(meta)
        if desc:
            lines.append(f"- **[{display}]({url})** — {desc}\n  <br> <sub>{meta_str}</sub>")
        else:
            lines.append(f"- **[{display}]({url})**\n  <br> <sub>{meta_str}</sub>")
    return "\n".join(lines) + "\n"


def fetch_recent_activity():
    """
    Retorna eventos recentes AGRUPADOS para nao repetir.
    - Push events: agrupa por (repo, branch), soma commits, usa msg mais recente.
    - Demais eventos: mantem 1 por tipo/repo.
    """
    data = gh_api(f"/users/{GITHUB_USERNAME}/events/public?per_page=30")
    if not data:
        return []

    pushes = {}  # chave (repo, branch) -> {total_commits, msg, created, url}
    other_events = []  # lista de (text, created)

    for ev in data:
        etype = ev.get("type", "")
        repo_name = ev.get("repo", {}).get("name", "")
        short_repo = repo_name.split("/")[-1] if repo_name else ""
        repo_url = f"https://github.com/{repo_name}"
        created = ev.get("created_at")
        payload = ev.get("payload", {})
        display_repo = pretty_repo_name(short_repo) if short_repo else repo_name

        if etype == "PushEvent":
            branch = (payload.get("ref") or "").replace("refs/heads/", "")
            key = (repo_name, branch)
            commits_list = payload.get("commits", []) or []
            n = payload.get("distinct_size") or payload.get("size") or len(commits_list) or 1
            msg = ""
            if commits_list:
                first = commits_list[-1] if len(commits_list) > 1 else commits_list[0]
                msg = (first.get("message") or "").splitlines()[0].strip()
            if len(msg) > 72:
                msg = msg[:69] + "..."
            # Pula eventos push que so tem SHA (sem mensagem real)
            if msg and len(msg) == 40 and re.fullmatch(r"[0-9a-f]+", msg):
                msg = ""
            if key not in pushes:
                pushes[key] = {"total": 0, "msg": msg or "", "created": created, "url": repo_url, "display": display_repo, "branch": branch}
            pushes[key]["total"] += n
            if msg and not pushes[key]["msg"]:
                pushes[key]["msg"] = msg
            # Sempre atualiza created pra ficar o mais recente
            pushes[key]["created"] = created

        elif etype == "CreateEvent":
            ref_type = payload.get("ref_type", "")
            if ref_type == "repository":
                text = f"🆕 Criou o repositório **[{display_repo}]({repo_url})**"
            elif ref_type == "branch":
                ref = payload.get("ref") or ""
                # Ignora branches padrao criadas automaticamente (main/master)
                if ref in {"main", "master"}:
                    continue
                text = f"🌿 Criou branch `{ref}` em **[{display_repo}]({repo_url})**"
            else:
                continue
            other_events.append({"text": text, "created": created, "sort_key": (repo_name, "create")})

        elif etype == "PullRequestEvent":
            action = payload.get("action", "")
            pr = payload.get("pull_request", {})
            title = (pr.get("title") or "").strip()
            if len(title) > 72:
                title = title[:69] + "..."
            if action == "closed" and pr.get("merged"):
                emoji, action_pt = "✅", "fez merge de PR"
            elif action == "closed":
                emoji, action_pt = "🔀", "fechou PR"
            elif action == "opened":
                emoji, action_pt = "📬", "abriu PR"
            elif action == "reopened":
                emoji, action_pt = "🔓", "reabriu PR"
            else:
                emoji, action_pt = "🔀", action
            t = f"{emoji} {action_pt} em **[{display_repo}]({repo_url})**"
            if title:
                t += f" — _{title}_"
            other_events.append({"text": t, "created": created, "sort_key": (repo_name, "pr", action)})

        elif etype == "IssuesEvent":
            action = payload.get("action", "")
            issue = payload.get("issue", {})
            title = (issue.get("title") or "").strip()
            if len(title) > 72:
                title = title[:69] + "..."
            t = f"🐛 {action} issue em **[{display_repo}]({repo_url})**"
            if title:
                t += f" — _{title}_"
            other_events.append({"text": t, "created": created, "sort_key": (repo_name, "issue", action)})

        elif etype == "WatchEvent":
            t = f"⭐ Deu uma estrela em **[{display_repo}]({repo_url})**"
            other_events.append({"text": t, "created": created, "sort_key": (repo_name, "star")})

        elif etype == "ForkEvent":
            t = f"🍴 Forkou **[{display_repo}]({repo_url})**"
            other_events.append({"text": t, "created": created, "sort_key": (repo_name, "fork")})

    # Converte pushes agrupados em eventos do tipo lista
    push_events = []
    for (repo_name, branch), info in pushes.items():
        total = info["total"]
        branch_part = f" na branch `{branch}`" if branch and branch != "main" and branch != "master" else ""
        text = f"📌 {total} commit(s){branch_part} em **[{info['display']}]({info['url']})**"
        if info["msg"]:
            text += f" — _{info['msg']}_"
        push_events.append({"text": text, "created": info["created"]})

    # Unifica todos eventos, ordena por data decrescente, remove duplicados textuais
    all_ev = []
    seen_texts = set()
    for ev in push_events + other_events:
        if ev["text"] in seen_texts:
            continue
        seen_texts.add(ev["text"])
        all_ev.append(ev)

    all_ev.sort(key=lambda e: e["created"], reverse=True)
    return all_ev[:MAX_ACTIVITY]


def build_activity_chunk(events):
    if not events:
        return "> Nenhuma atividade pública recente nos últimos dias.\n"
    lines = []
    for ev in events:
        when = fmt_dt_relative(ev["created"])
        lines.append(f"- {ev['text']}\n  <br> <sub>🕒 {when}</sub>")
    return "\n".join(lines) + "\n"


def build_monthly_activity_bars():
    """
    Retorna o grafico de atividade mensal como BARRAS DE EMOJI (Unicode BLOCK chars).
    Isso garante renderizacao PERFEITA no GitHub, sem SVG quebrado nem tags cruas.
    """
    today = datetime.date.today()
    start = today - datetime.timedelta(days=DAYS_BACK - 1)

    events = gh_api(f"/users/{GITHUB_USERNAME}/events/public?per_page=100") or []

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
            c = 1
            if ev.get("type") == "PushEvent":
                payload = ev.get("payload", {}) or {}
                c = payload.get("distinct_size") or payload.get("size") or len(payload.get("commits", [1])) or 1
            buckets[key] = buckets.get(key, 0) + c

    days_order = sorted(buckets.keys())
    values = [buckets[d] for d in days_order]
    total = sum(values)
    max_v = max(values) if values else 1
    if max_v == 0:
        max_v = 1

    # Blocos unicode (1/8 steps): vazio, 1/8, 2/8 ... 8/8
    blocks = [" ", "▁", "▂", "▃", "▄", "▅", "▆", "▇", "█"]
    bar = ""
    for v in values:
        if v == 0:
            bar += blocks[0]
        else:
            ratio = v / max_v  # 0..1
            idx = max(1, min(8, int(round(ratio * 8))))
            bar += blocks[idx]

    stats_line = f"**📊 {total} contribuições** nos últimos {DAYS_BACK} dias · Pico diário: **{max_v}**"
    date_line = f"<sub>De **{start.strftime('%d/%b')}** até **{today.strftime('%d/%b')}**</sub>"

    return (
        f"{stats_line}\n\n"
        f"<div align=\"center\">\n\n"
        f"```\n{bar}\n```\n\n"
        f"</div>\n\n"
        f"{date_line}\n"
    )


def main():
    if not README_PATH.exists():
        print(f"[ERRO] README nao encontrado em {README_PATH}", file=sys.stderr)
        return 1

    content = README_PATH.read_text(encoding="utf-8")

    repos = fetch_repos()
    content = replace_chunk(content, "LATEST_PROJECTS", build_repos_chunk(repos))

    activity = fetch_recent_activity()
    content = replace_chunk(content, "RECENT_ACTIVITY", build_activity_chunk(activity))

    content = replace_chunk(content, "MONTHLY_ACTIVITY_GRAPH", build_monthly_activity_bars())

    README_PATH.write_text(content, encoding="utf-8")
    print("[OK] README atualizado com sucesso!")
    return 0


if __name__ == "__main__":
    sys.exit(main())
