"""Builds the daily email from docs/data.json and sends it (Gmail SMTP with an app password, set as GitHub secrets).
Usage: python -m tracker.email_digest"""
import os, json, smtplib, ssl
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

ROOT = os.path.join(os.path.dirname(__file__), "..")
COL = {"red": "#ef5b52", "amber": "#e6ad3c", "green": "#3fbf86", "grey": "#6f819c"}

def build(d, app_url):
    r = d["run"]
    nmv = sum(1 for i in d.get("movers", []) if i["published"] == r["time"][:10])
    subject = f"Tech Tracker {r['time'][:10]}: {nmv} mover{'s' if nmv != 1 else ''}, {r['red']} red, {r['amber']} amber"
    failed = [s for s in d["sources"] if s["status"] == "failed"]
    mv = [i for i in d.get("movers", []) if i["published"] == r["time"][:10]]
    mrows = "".join([
        f'<tr><td style="padding:8px;border-left:5px solid {COL[i["colour"]]};background:#151e2d">'
        f'<div style="color:#f2f5fa;font-weight:700">{i["title"]}</div>'
        f'<div style="color:#9fb0c8;font-size:13px">{i["reason"]} [{i["tag"]}]</div></td></tr>' for i in mv)
    rows = "".join(
        f'<tr><td style="padding:8px;border-left:5px solid {COL[i["colour"]]};background:#0f1622">'
        f'<div style="color:#f2f5fa;font-weight:600">{i["title"]}</div>'
        f'<div style="color:#9fb0c8;font-size:13px">{i["reason"]} [{i["tag"]}] '
        f'{("<a style=color:#7fd3ff href=" + i["url"] + ">open</a>") if i["url"] else ""}</div></td></tr>'
        for i in d["today"] if i["colour"] in ("red", "amber")][:25])
    greens = sum(1 for i in d["today"] if i["colour"] == "green")
    warn = ("<p style='color:#ef5b52'>Sources that failed today: " + ", ".join(s["name"] for s in failed) + "</p>") if failed else ""
    body = f"""<div style="background:#070b12;padding:18px;font-family:Arial,sans-serif;color:#f2f5fa">
<h2 style="margin:0 0 6px">Technology Tracker</h2>
<p style="color:#9fb0c8;margin:0 0 12px">{r['new']} new today. Movers first, then red and amber; {greens} green items in the app.</p>{warn}
{('<h3 style="margin:8px 0 4px">Movers: candidates to price</h3><table style="width:100%;border-collapse:separate;border-spacing:0 6px">' + mrows + '</table>') if mrows else '<p style="color:#9fb0c8">No movers today.</p>'}
<table style="width:100%;border-collapse:separate;border-spacing:0 6px">{rows or '<tr><td style="color:#9fb0c8">Nothing red or amber today.</td></tr>'}</table>
<p><a style="color:#070b12;background:#f2f5fa;padding:10px 14px;border-radius:999px;text-decoration:none;font-weight:700" href="{app_url}">Open the app</a></p></div>"""
    return subject, body

def send():
    d = json.load(open(os.path.join(ROOT, "docs", "data.json")))
    app_url = os.environ.get("APP_URL", "")
    subject, body = build(d, app_url)
    user, pw, to = os.environ.get("GMAIL_USER"), os.environ.get("GMAIL_APP_PASSWORD"), os.environ.get("EMAIL_TO")
    if not (user and pw and to):
        print("Email not sent: GMAIL_USER, GMAIL_APP_PASSWORD and EMAIL_TO secrets are not all set."); return False
    m = MIMEMultipart("alternative"); m["Subject"] = subject; m["From"] = user; m["To"] = to
    m.attach(MIMEText(body, "html"))
    with smtplib.SMTP_SSL("smtp.gmail.com", 465, context=ssl.create_default_context()) as s:
        s.login(user, pw); s.sendmail(user, to.split(","), m.as_string())
    print("Email sent:", subject); return True

if __name__ == "__main__":
    send()
