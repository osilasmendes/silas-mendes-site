from pathlib import Path
import re
import sys

ROOT = Path.cwd()
APP = ROOT / "app.py"
TEMPLATES = ROOT / "templates"

if not APP.exists() or not TEMPLATES.is_dir():
    print("ERRO: execute este arquivo dentro da pasta principal do site, onde existem app.py e templates.")
    sys.exit(1)

app = APP.read_text(encoding="utf-8")

if "from datetime import datetime, timezone" not in app:
    app = app.replace(
        "from datetime import datetime\n",
        "from datetime import datetime, timezone\n",
        1,
    )

if "from zoneinfo import ZoneInfo" not in app:
    marker = "from uuid import uuid4\n"
    app = app.replace(marker, marker + "from zoneinfo import ZoneInfo\n", 1)

timezone_code = '''
\nLOCAL_TIMEZONE = ZoneInfo("America/Recife")


def to_local_datetime(value):
    """Converte datetime salvo em UTC para o horário de Recife apenas na exibição."""
    if value is None:
        return None

    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    else:
        value = value.astimezone(timezone.utc)

    return value.astimezone(LOCAL_TIMEZONE)


@app.template_filter("local_datetime")
def local_datetime_filter(value, fmt="%d/%m/%Y às %H:%M"):
    local_value = to_local_datetime(value)
    if local_value is None:
        return "—"
    return local_value.strftime(fmt)
'''

if '@app.template_filter("local_datetime")' not in app:
    marker = "\ndef parse_brl_to_cents(value):"
    if marker not in app:
        print("ERRO: ponto de inserção não encontrado no app.py.")
        sys.exit(1)
    app = app.replace(marker, timezone_code + marker, 1)

APP.write_text(app, encoding="utf-8")

pattern = re.compile(
    r"\{\{\s*(.*?)\.strftime\(\s*(['\"])(.*?)\2\s*\)\s*\}\}",
    re.DOTALL,
)

changed_files = []
replacements = 0

for html in sorted(TEMPLATES.glob("*.html")):
    original = html.read_text(encoding="utf-8")

    def repl(match):
        global replacements
        expr = match.group(1).strip()
        quote = match.group(2)
        fmt = match.group(3)
        replacements += 1
        return "{{ " + expr + "|local_datetime(" + quote + fmt + quote + ") }}"

    updated = pattern.sub(repl, original)
    if updated != original:
        html.write_text(updated, encoding="utf-8")
        changed_files.append(html.name)

compile(APP.read_text(encoding="utf-8"), str(APP), "exec")

remaining = []
for html in sorted(TEMPLATES.glob("*.html")):
    text = html.read_text(encoding="utf-8")
    if ".strftime(" in text:
        remaining.append(html.name)

print("")
print("CORREÇÃO DE HORÁRIO CONCLUÍDA.")
print(f"Templates alterados: {len(changed_files)}")
print(f"Datas convertidas para horário de Recife: {replacements}")
if changed_files:
    print("Arquivos alterados:")
    for name in changed_files:
        print(" -", name)

if remaining:
    print("")
    print("ATENÇÃO: ainda existe .strftime em:")
    for name in remaining:
        print(" -", name)
    print("Me envie um print antes de fazer o push.")
else:
    print("")
    print("Verificação OK: todas as datas exibidas por strftime agora usam America/Recife.")
    print("")
    print("Próximos comandos:")
    print("git add app.py templates")
    print('git commit -m "Corrige horario para Recife"')
    print("git push")
