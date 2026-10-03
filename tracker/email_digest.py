"""Builds the daily email from docs/data.json and sends it (Gmail SMTP with an app password, set as GitHub secrets).
Usage: python -m tracker.email_digest"""
import os, json, smtplib, ssl
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

ROOT = os.path.join(os.path.dirname(__file__), "..")
COL = {"red": "#ef5b52", "amber": "#e6ad3c", "green": "#3fbf86", "grey": "#6f819c"}


def row(i, bg, weight):
    link = ('<a style="color:#7fd3ff" href="' + i["url"] + '">open</a>') if i.get("url") else ""
    return ('<tr><td style="padding:8px;border-left:5px solid ' + COL.get(i["colour"], COL["grey"]) + ';background:' + bg + '">'
            '<div style="color:#f2f5fa;font-weight:' + weight + '">' + i["title"] + '</div>'
            '<div style="color:#9fb0c8;font-size:13px">' + (i.get("reason") or "") + ' [' + i.get("tag", "") + '] ' + link + '</div></td></tr>')


def build(d, app_url):
    r = d["run"]
    today = r["time"][:10]
    mv = [i for i in d.get("movers", []) if i.get("published") == today]
    subject = f"Tech Tracker {today}: {len(mv)} mover{'s' if len(mv) != 1 else ''}, {r['red']} red, {r['amber']} amber"
    failed = [s for s in d["sources"] if s["status"] == "failed"]
    mrows = "".join(row(i, "#151e2d", "700") for i in mv)
    alerts = [i for i in d["today"] if i["colour"] in ("red", "amber")][:25]
    rows = "".join(row(i, "#0f1622", "600") for i in alerts)
    greens = sum(1 for i in d["today"] if i["colour"] == "green")
    warn = ("<p style='color:#ef5b52'>Sources that failed today: " + ", ".join(s["name"] for s in failed) + "</p>") if failed else ""
    movers_block = ('<h3 style="margin:8px 0 4px">Movers: candidates to price</h3>'
                    '<table style="width:100%;border-collapse:separate;border-spacing:0 6px">' + mrows + '</table>') if mrows else '<p style="color:#9fb0c8">No movers today.</p>'
    alerts_block = rows or '<tr><td style="color:#9fb0c8">Nothing red or amber today.</td></tr>'
    body = ('<div style="background:#070b12;padding:18px;font-family:Arial,sans-serif;color:#f2f5fa">'
            '<h2 style="margin:0 0 6px">Technology Tracker</h2>'
            '<p style="color:#9fb0c8;margin:0 0 12px">' + str(r["new"]) + ' new today. Movers first, then red and amber; ' + str(greens) + ' green items in the app.</p>'
            + warn + movers_block +
            '<table style="width:100%;border-collapse:separate;border-spacing:0 6px">' + alerts_block + '</table>'
            '<p><a style="color:#070b12;background:#f2f5fa;padding:10px 14px;border-radius:999px;text-decoration:none;font-weight:700" href="' + app_url + '">Open the app</a></p></div>')
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
        s.login(user, pw); s.sendmail(user, [to], m.as_string())
    print("Email sent:", subject); return True


if __name__ == "__main__":
    send()
