"""Build the Patrol documentation PDF (draft).

DRAFT STYLE: a stand-in layout until docstyle.py (the shared module from the
Cloud Architecture Platform write-up) is attached; the final version swaps
these helpers for docstyle.py so all project PDFs match.

Usage: python3 build_doc.py <redacted_screenshot_dir> <output.pdf>
"""
import sys
from pathlib import Path
from reportlab.lib.pagesizes import LETTER
from reportlab.lib.units import inch
from reportlab.lib import colors
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.enums import TA_CENTER
from reportlab.platypus import (BaseDocTemplate, PageTemplate, Frame, Paragraph, Spacer,
                                Table, TableStyle, Image, KeepTogether, PageBreak,
                                ListFlowable, ListItem, NextPageTemplate)
from reportlab.graphics.shapes import Drawing, Rect, String, Line, Polygon
from PIL import Image as PILImage

SHOTS = Path(sys.argv[1]); OUT = sys.argv[2]

NAVY = colors.HexColor("#14213d"); TEAL = colors.HexColor("#0f7c80")
INK = colors.HexColor("#22252b"); MUTED = colors.HexColor("#5b6270")
RULE = colors.HexColor("#d5d9e0"); ZEBRA = colors.HexColor("#f4f6f9")
BUG_BG, BUG_BAR = colors.HexColor("#fdf0ef"), colors.HexColor("#c0392b")
SUR_BG, SUR_BAR = colors.HexColor("#fff7e6"), colors.HexColor("#d68910")
LES_BG, LES_BAR = colors.HexColor("#eef7f1"), colors.HexColor("#1e8449")
DRAFT_BG, DRAFT_BAR = colors.HexColor("#eef3fb"), colors.HexColor("#2e5c9a")

W, H = LETTER; M = 0.85 * inch; TW = W - 2 * M

base = dict(fontName="Helvetica", fontSize=10, leading=14.2, textColor=INK)
S = {
    "body": ParagraphStyle("body", spaceAfter=6, **base),
    "h1": ParagraphStyle("h1", fontName="Helvetica-Bold", fontSize=19, leading=23,
                         textColor=NAVY, spaceBefore=4, spaceAfter=4),
    "h2": ParagraphStyle("h2", fontName="Helvetica-Bold", fontSize=13, leading=17,
                         textColor=TEAL, spaceBefore=12, spaceAfter=4),
    "h3": ParagraphStyle("h3", fontName="Helvetica-Bold", fontSize=10.5, leading=14,
                         textColor=NAVY, spaceBefore=8, spaceAfter=2),
    "cell": ParagraphStyle("cell", fontName="Helvetica", fontSize=8.6, leading=11.2, textColor=INK),
    "cellh": ParagraphStyle("cellh", fontName="Helvetica-Bold", fontSize=8.6, leading=11.2,
                            textColor=colors.white),
    "cap": ParagraphStyle("cap", fontName="Helvetica-Oblique", fontSize=8.4, leading=11,
                          textColor=MUTED, alignment=TA_CENTER, spaceBefore=3, spaceAfter=10),
    "call": ParagraphStyle("call", fontName="Helvetica", fontSize=9.2, leading=12.8, textColor=INK),
    "kicker": ParagraphStyle("kicker", fontName="Helvetica-Bold", fontSize=8.5, leading=11,
                             textColor=TEAL, spaceAfter=0),
}

def code(t): return f'<font name="Courier" size="8.8">{t}</font>'
def P(t, s="body"): return Paragraph(t, S[s])

def bullets(items):
    return ListFlowable([ListItem(P(i), leftIndent=12) for i in items],
                        bulletType="bullet", start="•", leftIndent=12, bulletFontSize=8,
                        spaceAfter=4)

def table(rows, widths, header=True):
    data = [[Paragraph(c, S["cellh" if (header and r == 0) else "cell"]) for c in row]
            for r, row in enumerate(rows)]
    t = Table(data, colWidths=[w * TW for w in widths], repeatRows=1 if header else 0, spaceBefore=2, spaceAfter=10)
    st = [("VALIGN", (0, 0), (-1, -1), "TOP"),
          ("LINEBELOW", (0, 0), (-1, -1), 0.4, RULE),
          ("TOPPADDING", (0, 0), (-1, -1), 4), ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
          ("LEFTPADDING", (0, 0), (-1, -1), 5), ("RIGHTPADDING", (0, 0), (-1, -1), 5)]
    if header: st.append(("BACKGROUND", (0, 0), (-1, 0), NAVY))
    for r in range(1 if header else 0, len(rows)):
        if r % 2 == 0: st.append(("BACKGROUND", (0, r), (-1, r), ZEBRA))
    t.setStyle(TableStyle(st)); return t

def callout(kind, title, text):
    bg, bar = {"bug": (BUG_BG, BUG_BAR), "surprise": (SUR_BG, SUR_BAR),
               "lesson": (LES_BG, LES_BAR), "draft": (DRAFT_BG, DRAFT_BAR)}[kind]
    label = {"bug": "BUG", "surprise": "SURPRISE", "lesson": "LESSON", "draft": "DRAFT NOTE"}[kind]
    body = [Paragraph(f'<font color="{bar.hexval()}"><b>{label}</b></font>  <b>{title}</b>', S["call"]),
            Spacer(1, 2)]
    body += [Paragraph(x, S["call"]) for x in (text if isinstance(text, list) else [text])]
    t = Table([[body]], colWidths=[TW])
    t.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, -1), bg),
                           ("LINEBEFORE", (0, 0), (0, -1), 3, bar),
                           ("LEFTPADDING", (0, 0), (-1, -1), 10), ("RIGHTPADDING", (0, 0), (-1, -1), 10),
                           ("TOPPADDING", (0, 0), (-1, -1), 7), ("BOTTOMPADDING", (0, 0), (-1, -1), 7)]))
    return KeepTogether([Spacer(1, 4), t, Spacer(1, 6)])

def bug(title, symptom, cause, fix):
    return callout("bug", title, [f"<b>Symptom:</b> {symptom}", f"<b>Cause:</b> {cause}",
                                  f"<b>Fix:</b> {fix}"])

FIG = [0]
def shot(name, caption, max_h=3.6 * inch, width=1.0):
    p = SHOTS / name
    w, h = PILImage.open(p).size
    dw = TW * width; dh = dw * h / w
    if dh > max_h: dh = max_h; dw = dh * w / h
    FIG[0] += 1
    img = Image(str(p), width=dw, height=dh)
    frame = Table([[img]], colWidths=[dw + 2])
    frame.setStyle(TableStyle([("BOX", (0, 0), (-1, -1), 0.6, RULE),
                               ("LEFTPADDING", (0, 0), (-1, -1), 1), ("RIGHTPADDING", (0, 0), (-1, -1), 1),
                               ("TOPPADDING", (0, 0), (-1, -1), 1), ("BOTTOMPADDING", (0, 0), (-1, -1), 1)]))
    return KeepTogether([Spacer(1, 4), frame, P(f"Figure {FIG[0]}. {caption}", "cap")])

# ---------------------------------------------------------------- diagrams
def box(d, x, y, w, h, title, sub=None, fill=colors.white, stroke=NAVY, dashed=False, tcol=NAVY):
    r = Rect(x, y, w, h, rx=5, ry=5, fillColor=fill, strokeColor=stroke, strokeWidth=1.1)
    if dashed: r.strokeDashArray = [3, 2]
    d.add(r)
    ty = y + h / 2 + (3 if sub else -3)
    d.add(String(x + w / 2, ty, title, fontName="Helvetica-Bold", fontSize=8.2, fillColor=tcol,
                 textAnchor="middle"))
    if sub:
        d.add(String(x + w / 2, ty - 10, sub, fontName="Helvetica", fontSize=6.8, fillColor=MUTED,
                     textAnchor="middle"))

def arrow(d, x1, y1, x2, y2, label=None, dashed=False, col=INK, lx=0, ly=4):
    ln = Line(x1, y1, x2, y2, strokeColor=col, strokeWidth=1)
    if dashed: ln.strokeDashArray = [3, 2]
    d.add(ln)
    import math
    a = math.atan2(y2 - y1, x2 - x1); s = 5
    d.add(Polygon([x2, y2, x2 - s * math.cos(a - .4), y2 - s * math.sin(a - .4),
                   x2 - s * math.cos(a + .4), y2 - s * math.sin(a + .4)],
                  fillColor=col, strokeColor=col))
    if label:
        d.add(String((x1 + x2) / 2 + lx, (y1 + y2) / 2 + ly, label, fontName="Helvetica",
                     fontSize=6.6, fillColor=MUTED, textAnchor="middle"))

def architecture():
    d = Drawing(TW, 330)
    PURPLE = colors.HexColor("#6c5aa8")
    d.add(Rect(0, 0, TW, 318, rx=6, ry=6, fillColor=colors.HexColor("#fbfcfd"), strokeColor=RULE))
    d.add(String(8, 306, "AWS account: Patrol (us-east-1)", fontName="Helvetica-Bold", fontSize=8,
                 fillColor=MUTED))
    # temporary layer: VPC + cluster
    d.add(Rect(10, 70, 270, 228, rx=6, ry=6, fillColor=colors.HexColor("#eef7f7"),
               strokeColor=TEAL, strokeWidth=1))
    d.add(String(18, 286, "VPC  |  TEMPORARY LAYER (built + destroyed each session)",
                 fontName="Helvetica-Bold", fontSize=6.8, fillColor=TEAL))
    d.add(Rect(22, 82, 246, 190, rx=5, ry=5, fillColor=colors.white, strokeColor=NAVY))
    d.add(String(30, 260, "EKS cluster \"patrol\" (1.36): 2 nodes in 2 zones",
                 fontName="Helvetica-Bold", fontSize=6.8, fillColor=NAVY))
    # left column: platform pieces
    box(d, 32, 204, 126, 36, "Argo CD", "Phase 3 (next)", dashed=True)
    box(d, 32, 156, 126, 36, "AWS LB Controller", "Phase 3 (next)", dashed=True)
    box(d, 32, 96, 126, 48, "System add-ons", "VPC CNI, Pod Identity, CoreDNS", fill=ZEBRA)
    # right column: Patrol services
    box(d, 172, 204, 86, 36, "scheduler", "CronJob, every min", dashed=True)
    box(d, 172, 150, 86, 36, "worker", "Deployment", dashed=True)
    box(d, 172, 96, 86, 36, "status page", "Deployment", dashed=True)
    # permanent layer
    px = 360; bw = TW - px - 14
    d.add(Rect(px - 10, 70, bw + 20, 228, rx=6, ry=6, fillColor=colors.HexColor("#f6f4fb"),
               strokeColor=PURPLE))
    d.add(String(px - 2, 286, "PERMANENT LAYER (kept)", fontName="Helvetica-Bold", fontSize=6.8,
                 fillColor=PURPLE))
    box(d, px, 232, bw, 36, "SQS queue", "+ dead-letter queue")
    box(d, px, 186, bw, 36, "DynamoDB table", "results, TTL")
    box(d, px, 140, bw, 36, "ECR x3", "immutable tags")
    box(d, px, 94, bw, 36, "IAM roles x4", "+ Slack secret")
    # data flow
    arrow(d, 258, 228, px, 256, "send", ly=6)
    arrow(d, px, 244, 258, 176, "receive", lx=6, ly=8)
    arrow(d, 258, 164, px, 200, "PutItem", lx=-4, ly=-12)
    arrow(d, 258, 118, px, 194, "Query", lx=-6, ly=-16)
    # outside
    box(d, 32, 20, 110, 32, "Visitor (internet)")
    box(d, 172, 20, 86, 32, "ALB", "Phase 3 (next)", dashed=True)
    arrow(d, 142, 36, 172, 36, dashed=True)
    arrow(d, 215, 52, 215, 96, dashed=True)
    box(d, px, 20, bw, 32, "Slack #patrol-alerts", "Phase 5", dashed=True)
    d.add(Line(TW - 150, 7, TW - 132, 7, strokeColor=INK))
    d.add(String(TW - 128, 4, "built", fontName="Helvetica", fontSize=6.6, fillColor=MUTED))
    ln = Line(TW - 100, 7, TW - 82, 7, strokeColor=INK); ln.strokeDashArray = [3, 2]; d.add(ln)
    d.add(String(TW - 78, 4, "next phases", fontName="Helvetica", fontSize=6.6, fillColor=MUTED))
    return d

def layers():
    d = Drawing(TW, 150)
    hw = TW / 2 - 8
    box(d, 0, 10, hw, 130, "", fill=colors.HexColor("#f6f4fb"), stroke=colors.HexColor("#6c5aa8"))
    box(d, hw + 16, 10, hw, 130, "", fill=colors.HexColor("#eef7f7"), stroke=TEAL)
    def lines(x, title, col, items):
        d.add(String(x + 10, 124, title, fontName="Helvetica-Bold", fontSize=8.4, fillColor=col))
        for i, t in enumerate(items):
            d.add(String(x + 14, 106 - i * 13, "• " + t, fontName="Helvetica", fontSize=7.6,
                         fillColor=INK))
    lines(0, "PERMANENT: pennies per month, never torn down", colors.HexColor("#6c5aa8"),
          ["State bucket + $30 budget alerts (bootstrap)", "ECR repositories x3",
           "SQS queue + dead-letter queue", "DynamoDB results table", "Slack webhook secret",
           "IAM roles for pods (Phase 3)", "Terraform: infra/bootstrap, infra/permanent"])
    lines(hw + 16, "TEMPORARY: billed by the hour, rebuilt per session", TEAL,
          ["VPC, subnets, NAT gateway", "EKS control plane + 2 nodes",
           "Add-ons (CNI, Pod Identity agent, CoreDNS)", "Pod Identity links + gateway endpoints",
           "up.sh: 14m 34s  |  down.sh: 12m 52s", "Terraform: infra/cluster",
           "Cost: about $0.25/hour while up"])
    return d

# ---------------------------------------------------------------- page furniture
def on_page(c, doc):
    c.saveState()
    c.setStrokeColor(RULE); c.setLineWidth(0.5)
    c.line(M, H - M + 18, W - M, H - M + 18)
    c.setFont("Helvetica", 7.8); c.setFillColor(MUTED)
    c.drawString(M, H - M + 24, "Patrol: Kubernetes uptime monitor on Amazon EKS")
    c.drawRightString(W - M, H - M + 24, "DRAFT 1  |  Phases 0 to 3 (in progress)")
    c.line(M, M - 18, W - M, M - 18)
    c.drawString(M, M - 30, "Steven Waldron")
    c.drawRightString(W - M, M - 30, f"{doc.page}")
    c.restoreState()

def on_cover(c, doc):
    c.saveState()
    c.setFillColor(NAVY); c.rect(0, H - 4.3 * inch, W, 4.3 * inch, fill=1, stroke=0)
    c.setFillColor(TEAL); c.rect(0, H - 4.3 * inch, W, 5, fill=1, stroke=0)
    c.setFillColor(colors.white)
    c.setFont("Helvetica", 10); c.drawString(M, H - 1.2 * inch, "PORTFOLIO PROJECT DOCUMENTATION")
    c.setFont("Helvetica-Bold", 40); c.drawString(M, H - 2.0 * inch, "Patrol")
    c.setFont("Helvetica", 15)
    c.drawString(M, H - 2.45 * inch, "A Kubernetes uptime monitor on Amazon EKS,")
    c.drawString(M, H - 2.72 * inch, "built with Terraform, rebuilt from scratch every session")
    c.setFont("Helvetica", 9.5); c.setFillColor(colors.HexColor("#c9d3e3"))
    c.drawString(M, H - 3.5 * inch, "Steven Waldron  |  AWS Solutions Architect Associate + Professional")
    c.drawString(M, H - 3.72 * inch, "github.com/stevenwaldron/patrol")
    c.setFillColor(MUTED); c.setFont("Helvetica", 8.5)
    c.drawString(M, 0.8 * inch, "Draft 1, October 25, 2026. Covers Phases 0 to 2 and Phase 3 so far.")
    c.restoreState()

# ---------------------------------------------------------------- content
story = [NextPageTemplate("body"), Spacer(1, 3.9 * inch)]

story += [P("At a glance", "h2"),
          table([["", ""],
                 ["What it is", "An uptime monitor: every minute it checks a list of websites "
                  "(my CloudForger platform, my 3D chess game, and a deliberately broken test site) "
                  "and shows the results on a public status page."],
                 ["Why I built it", "My earlier projects are serverless. Patrol fills the gap with "
                  "Kubernetes on Amazon EKS, plus the tools that come with running it in practice: "
                  "Helm, Argo CD, Prometheus, Grafana and Alertmanager."],
                 ["How it runs", "Three small Python services in containers: a <b>scheduler</b> that issues "
                  "patrol orders to an SQS queue, <b>workers</b> that carry them out and save results "
                  "to DynamoDB, and a <b>status page</b> that reads them back."],
                 ["Cost design", "Two layers: cheap permanent pieces stay up; the expensive cluster is "
                  "built by one script and destroyed by another every session. $30/month budget with alerts."],
                 ["Status", "Phases 0 to 2 done and verified. Phase 3 (the app inside the cluster) under way."]],
                [0.2, 0.8], header=False)]
story.append(callout("draft", "About this draft",
    "This draft is built from my running build log and screenshots up to Phase 3, step 2. "
    "Layout is a placeholder: the final version uses the same PDF style as my "
    "Cloud Architecture Platform write-up. Screenshots are redacted (emails, IAM unique IDs, "
    "Slack webhook details)."))

story += [PageBreak(), P("1. Architecture", "h1"),
          P("The picture below shows where everything lives. Solid boxes are built and verified; dashed "
            "boxes are the next phases. The services and queue already work end to end on my Mac and in "
            "containers against real AWS; Phase 3 moves them into the cluster."),
          architecture(), P("Figure: Patrol architecture (target state for Phase 3, with later phases dashed).", "cap"),
          P("How a check flows", "h3"),
          bullets(["Every minute, the <b>scheduler</b> posts one order per site to the SQS queue, then exits.",
                   "A <b>worker</b> long-polls the queue, checks the site over HTTPS, and writes the result "
                   "(up/down, status code, latency) to DynamoDB. Only then does it delete the order.",
                   "If saving fails, the order is left on the queue; SQS hands it out again, and after 3 "
                   "failed tries moves it to a dead-letter queue so one bad message can't jam the workers.",
                   "The <b>status page</b> queries DynamoDB and shows the latest state and 24-hour history."]),
          P("The two layers", "h3"),
          P("An EKS cluster costs money every hour it exists, so the design splits the project into a "
            "permanent layer that is almost free and a temporary layer that I build when I work and "
            "destroy when I stop. Nothing the app needs to remember lives in the temporary layer."),
          layers(), P("Figure: what survives a teardown and what gets rebuilt.", "cap")]

story += [PageBreak(), P("2. Planning decisions", "h1"),
          P("I decided every major choice before writing code, including what I turned down. "
            "These are the decisions an interviewer is most likely to ask about."),
          table([["Decision", "Chosen", "Rejected, and why"],
                 ["Platform", "Amazon EKS", "ECS: simpler and cheaper for this app, but Kubernetes is the skill gap in my portfolio"],
                 ["Workload", "Fresh uptime monitor", "Porting chess (needs a WebSocket redesign); a leaderboard (too thin); an online shop (reads as a tutorial)"],
                 ["Queue", "SQS", "Redis in the cluster: one more thing to run, lost at every teardown"],
                 ["Results", "DynamoDB", "A database in the cluster: hard to run well, wiped at every teardown"],
                 ["Cost control", "Permanent + temporary layers, $30 budget", "Always-on cluster: about $180/month"],
                 ["Nodes", "Managed node group (Karpenter later)", "Fargate: no node-level monitoring, no Pod Identity. Auto Mode: hides the skills I want to show"],
                 ["Node type", "2 x t3.medium, x86", "ARM/Graviton: cheaper, but my Intel Mac builds x86 images; revisit with multi-arch CI builds"],
                 ["Ownership", "Terraform owns AWS; Argo CD owns the cluster", "Both tools managing the same objects"],
                 ["Image tags", "Git commit ID, immutable", "\"latest\": can't tell what's running or roll back reliably"],
                 ["Pod AWS access", "EKS Pod Identity, one role per service", "IRSA: trust tied to a per-cluster OIDC provider, broken by every rebuild. Node role: every pod gets every permission"],
                 ["Cluster access", "EKS access entries; CI gets none", "aws-auth ConfigMap: one bad edit locks everyone out"],
                 ["Secrets", "Secrets Manager + External Secrets Operator", "Secrets in Git or Terraform state"],
                 ["Monitoring", "kube-prometheus-stack, SLO + error budget", "Managed Prometheus/Grafana: the point is to show running it myself"],
                 ["Alerts", "Slack", "Email"],
                 ["Image scanning", "Trivy in CI, fail on serious findings", "None (a gap I named in both earlier CI/CD write-ups)"],
                 ["Policy", "Kyverno, audit first, then enforce", "No admission policy"],
                 ["Worker scaling", "KEDA on SQS queue depth", "CPU-based autoscaling: checkers wait on the network, so CPU stays low while the queue grows"],
                 ["Language", "Python", "Node.js"]],
                [0.17, 0.30, 0.53]),
          P("Build plan", "h3"),
          table([["Phase", "What it delivers", "Status"],
                 ["0", "Account, budget, state bucket, registries, queue, table, Slack alert path", "Done"],
                 ["1", "Three Python services, 29 tests, three container images", "Done"],
                 ["2", "VPC + EKS cluster from Terraform, one-command up/down scripts", "Done"],
                 ["3", "Pod Identity, Helm chart, Argo CD, load balancer, then HTTPS", "In progress"],
                 ["4", "GitHub Actions CI/CD with OIDC and Trivy scanning", "Planned"],
                 ["5", "Prometheus, Grafana, Alertmanager to Slack, SLO", "Planned"],
                 ["6-8", "Kyverno policies, KEDA autoscaling, failure drills", "Planned"],
                 ["9-10", "Karpenter (optional), final write-up", "Planned"]],
                [0.1, 0.72, 0.18])]

# ---------------------------------------------------------------- Phase 0
story += [PageBreak(), P("3. Phase 0: the foundation", "h1"),
          P("Before any application code, I set up the account, the guard rails and the permanent AWS "
            "pieces the app depends on."),
          P("Step 1. A dedicated account, created with my own tool", "h2"),
          P("I created the Patrol member account with <b>aws-organizations-toolkit v2</b>, a CLI tool I "
            "built in an earlier project. One command (" + code("org-account-create") + ") creates the "
            "account, moves it into the PROJECTS organizational unit and verifies the move, behind a typed "
            "confirmation. I then checked the result with " + code("org-inspect") + ". Rejected: doing it "
            "in the console or with raw CLI calls (several manual steps and no built-in verification)."),
          shot("01-org-inspect-tree.png", "org-inspect showing Patrol in the PROJECTS OU next to my other project accounts."),
          shot("02-org-account-create.png", "Creating the account with my own toolkit."),
          P("I reach the account through " + code("OrganizationAccountAccessRole") + " using a "
            + code("patrol") + " CLI profile, so I never create long-lived keys inside it."),
          shot("03-patrol-profile-access.png", "Confirming the patrol profile lands in account 419701112481.", max_h=1.8 * inch),
          P("Step 2. State bucket and budget (bootstrap)", "h2"),
          bullets(["<b>Budget in Terraform</b>, not clicked in the console, so it is recorded and rebuildable. "
                   "$30/month with emails at 50%, 80% and 100% of actual spend and 100% of forecast.",
                   "<b>Bootstrap keeps local state</b> and runs once: the bucket it creates can't hold its own state.",
                   "<b>S3-native state locking</b> (" + code("use_lockfile") + ") for later layers. Rejected a "
                   "DynamoDB lock table: no longer needed on Terraform 1.10+.",
                   "<b>Built-in account check</b>: Terraform refuses to run unless the caller is account "
                   "419701112481. Rejected relying on remembering the profile, which failed once on my chess project."]),
          callout("lesson", "A budget is a smoke alarm, not a sprinkler",
                  "AWS budgets warn; they don't stop spending, and billing data lags by hours. "
                  "The real protection is the teardown script run at the end of every session."),
          shot("04-bootstrap-plan.png", "Bootstrap plan: 8 resources to add."),
          shot("05-bootstrap-apply.png", "Bootstrap applied."),
          shot("06-budget-overview.png", "The $30 budget in the Billing console."),
          shot("07-budget-alerts.png", "Its four alert thresholds."),
          bug("Out of disk space during terraform init",
              "the first " + code("terraform init") + " failed with \"no space left on device\" while installing the AWS provider.",
              "the Mac's disk was 98% full (5 GB free), and every Terraform folder downloads its own copy of the "
              "multi-hundred-MB provider.",
              "a shared provider cache in " + code("~/.terraformrc") + " so one copy serves every project, then a "
              "cleanup that freed space to 26 GB."),
          callout("surprise", "A \"destroy-only\" copy still described live resources",
                  ["While cleaning up, an old copy of my chess project still held a state file describing "
                   "<b>live</b> resources. I counted resources before deleting anything, then proved it was an "
                   "outdated duplicate by matching its <b>lineage</b> (same state history) and <b>serial</b> "
                   "(9 versus the live 11).",
                   "Lesson: never delete a folder with state in it without checking which account it points at "
                   "and whether a newer copy exists."]),
          P("Step 3. The permanent layer", "h2"),
          table([["Resource", "Key choices"],
                 ["ECR x3", "Tags immutable, so a commit-ID tag always means one build. Scan on push. "
                  "Lifecycle: untagged images gone after 1 day, newest 20 kept."],
                 ["SQS queue + DLQ", "15-minute retention (orders are issued every minute, so old ones are useless), "
                  "60 s visibility timeout, 20 s long polling, dead-letter after 3 failed deliveries."],
                 ["DynamoDB table", "Key site_id + checked_at, on-demand billing, TTL on expires_at, deletion protection on."],
                 ["Slack secret", "Terraform creates it empty; the value goes in by CLI so it never appears in code, "
                  "plans or state. (A lesson carried over from a leaked-plan finding on CloudForger.)"]],
                [0.2, 0.8]),
          P("The list of sites lives in Git, not in DynamoDB, so adding a site is a reviewed commit. "
            "No AWS profile is hardcoded; commands use an inline " + code("AWS_PROFILE=patrol") +
            " prefix, so the same code runs from CI later."),
          shot("08-permanent-plan.png", "Permanent layer plan."),
          shot("09-permanent-apply.png", "Permanent layer applied."),
          shot("10-ecr-repositories.png", "The three ECR repositories."),
          P("Step 4. The Slack alert path", "h2"),
          P("I created a Slack workspace (\"Patrol Ops\"), a #patrol-alerts channel and a Slack app with an "
            "incoming webhook. Before storing anything, I confirmed the secret Terraform created was "
            "empty, proving no value had passed through Terraform. I stored the webhook through a hidden "
            "prompt (" + code("read -s") + ") so it never appeared on screen or in shell history. The test "
            "then read the URL back <b>from Secrets Manager</b> and posted to Slack, proving the stored "
            "value works, not just the copy on my clipboard."),
          bug("Empty secret value",
              code("put-secret-value") + " rejected an empty SecretString (length 0).",
              "the shell variable holding the webhook didn't exist in the terminal session where the store command ran.",
              "read, validate (must start with " + code("https://hooks.slack.com/services/") + ") and store in a "
              "single command that prints only the length."),
          shot("11-slack-test-alert.png", "Test alert delivered to #patrol-alerts using the value stored in Secrets Manager.", max_h=2.6 * inch)]

# ---------------------------------------------------------------- Phase 1
story += [PageBreak(), P("4. Phase 1: the three services", "h1"),
          P("One Python package (" + code("patrol") + ") with three entry points: scheduler, worker and "
            "status page, sharing config, logging, the site checker and the storage code. Rejected three "
            "separate codebases (duplicated code that drifts apart)."),
          P("Design decisions in the code", "h2"),
          table([["Decision", "Why"],
                 ["GET, not HEAD, for checks", "Some servers answer HEAD with 405, which would be a false \"down\"."],
                 ["A down site is a successful check", "Only internal failures (like failing to save) count as errors."],
                 ["Delete the order only after saving", "On save failure SQS redelivers it, then dead-letters it after 3 tries."],
                 ["Skip orders older than 2 minutes", "A stale check would report the past as the present."],
                 ["Readiness does not check DynamoDB", "Otherwise a DynamoDB blip marks every copy not-ready at once: a small hiccup becomes a full outage."],
                 ["No /metrics on the scheduler", "A one-second job can't be scraped. Its failure shows up downstream as \"last checked\" age growing. Rejected Pushgateway (another component to run)."],
                 ["Site metrics come from the status page", "With several workers, each worker's \"last result\" would disagree; DynamoDB is the single source of truth."],
                 ["Bounded metric labels", "Route templates, not raw paths, and probes excluded: series stay bounded and the SLO is about real visitors."],
                 ["Worker handles SIGTERM", "Kubernetes stops pods with SIGTERM; the worker finishes its batch and exits cleanly."],
                 ["Broken test site uses .invalid", "Reserved by RFC 2606: guaranteed never to resolve."]],
                [0.33, 0.67]),
          P("Testing", "h2"),
          P("29 tests run against <b>moto</b> (fake SQS and DynamoDB) and a fake HTTP layer. To prove the "
            "tests check real behaviour, I deliberately broke two rules (\"leave the message on save failure\" "
            "and \"skip stale orders\"); the tests caught both."),
          shot("12-tests-on-mac.png", "29 tests passing on my Mac (Python 3.13).", max_h=2.4 * inch),
          bug("pip couldn't find boto3",
              code("pip install") + " couldn't find boto3 1.43.115, and pip reported version 19.2.3.",
              code("python3") + " on the Mac was Python 3.8, which newer boto3 releases no longer support.",
              "build the virtual environment from Python 3.13 explicitly (matching the container image); the run "
              "script now refuses anything older than 3.11 with a clear message."),
          bug("\"1 ms\" latency for a site that never answered (caught in review)",
              "the status page showed 1 ms latency for the unreachable test site.",
              "latency was recorded as time-to-failure even when no response arrived.",
              "only show latency when the site actually answered, with a new test for it."),
          P("Live run against real AWS", "h2"),
          P("The scheduler issued 3 orders; the worker found CloudForger up (304 ms), chess up (294 ms) and "
            "the broken test site down (DNS failure); the status page showed all three correctly."),
          shot("13-worker-live-log.png", "Worker's structured JSON logs during the live run."),
          shot("14-local-status-page.png", "The status page running locally against real DynamoDB."),
          shot("15-worker-metrics.png", "Worker metrics in Prometheus format, ready for Phase 5.", max_h=3.0 * inch),
          callout("surprise", "Doubled metric series",
                  "The Prometheus client library emits an extra " + code("*_created") + " series for every counter, "
                  "doubling series count for no benefit here. Turned off with " + code("PROMETHEUS_DISABLE_CREATED_SERIES=True") + "."),
          P("Container images", "h2"),
          bullets(["<b>One Dockerfile, three targets</b> sharing cached dependency and base stages. Rejected three Dockerfiles that would drift apart.",
                   "<b>Base image from ECR Public</b> (AWS's mirror of the official Python image). Rejected Docker Hub: anonymous pull limits that shared CI runners hit.",
                   "<b>Non-root numeric user 10001.</b> Kubernetes can only verify " + code("runAsNonRoot") + " when the user is numeric. The code is root-owned, so the app can read it but not change it.",
                   "<b>.dockerignore as an allow-list</b>: only the package, requirements and sites list can enter an image, so state files or secrets never can.",
                   "<b>Short-lived credentials</b> for local container runs (about 1 hour), exported inside a subshell. " + code("~/.aws") + " is never mounted into a container. This mirrors how Pod Identity will supply credentials.",
                   "Built for " + code("linux/amd64") + " to match the x86 nodes."]),
          bug("A script that only breaks on macOS",
              "the container run script passed a syntax check but would crash on the Mac.",
              "macOS ships bash 3.2, where expanding an empty array under " + code("set -u") + " is an \"unbound variable\" error.",
              code('${ports[@]+"${ports[@]}"}') + ", which expands safely when the array is empty."),
          shot("16-docker-disk-limit.png", "Docker Desktop disk capped at 32 GB before building.", max_h=2.6 * inch),
          shot("17-images-built.png", "Three images built from the real base, 176 MB each.", max_h=2.4 * inch),
          shot("18-worker-container-log.png", "The worker running in a container against real AWS."),
          callout("surprise", "Visible proof the code moved into Linux",
                  "In the logs, the worker's name changed from my Mac's hostname to the container ID, and the DNS "
                  "error switched from macOS wording to Linux wording. In the cluster the same field will show the pod name."),
          shot("19-status-page-from-container.png", "The status page served from its container.")]

# ---------------------------------------------------------------- Phase 2
story += [PageBreak(), P("5. Phase 2: the cluster", "h1"),
          P("The temporary layer: a VPC, an EKS cluster and two worker nodes, all from Terraform, built by "
            + code("up.sh") + " and destroyed by " + code("down.sh") + "."),
          P("Network", "h2"),
          P("I reused the VPC module from my earlier Terraform project and extended it for EKS, with three "
            "backward-compatible changes reviewed line by line against the original:"),
          bullets(["<b>Subnet tags</b>: EKS needs " + code("kubernetes.io/role/elb") + " tags, or the load balancer controller silently finds no subnets.",
                   "<b>Optional database route table</b>, with a Terraform " + code("moved") + " block so the original project's state keeps its table instead of replacing it.",
                   "<b>/20 private subnets</b> (about 4,000 addresses) instead of /24. Pods take real VPC addresses in contiguous blocks, and small subnets fragment and fail with " + code("InsufficientCidrBlocks") + " even when addresses are free."]),
          callout("lesson", "Trade-off: one NAT gateway",
                  "A single NAT gateway saves about $32/month, but if its zone fails, outbound traffic stops for "
                  "both zones. Fine for a lab, not for production, and the Phase 3 gateway endpoints remove "
                  "DynamoDB and S3 from that risk."),
          P("Cluster", "h2"),
          table([["Decision", "Why"],
                 ["Community EKS module, pinned to 21.29.0", "Exact version, not a range. I cloned its source and checked every input against it; the check caught an old v20 input name in a deliberate test."],
                 ["Kubernetes 1.36 (one behind newest)", "Helm charts and add-ons usually lag a new minor version by weeks. Upgrading later is a planned exercise."],
                 ["upgrade_policy = STANDARD", "AWS defaults to extended support, which silently bills 6x the hourly rate once standard support ends."],
                 ["API endpoint open only to my IP (/32)", "up.sh detects my current IP. The common tutorial value 0.0.0.0/0 is blocked by a validation rule."],
                 ["Access entries; creator admin off", "An explicit admin entry for my access role, so a future CI apply can never make CI a cluster admin."],
                 ["IRSA off", "Pod Identity instead, so no per-cluster OIDC provider to rebuild."],
                 ["No customer-managed KMS key", "EKS already encrypts Kubernetes data with an AWS-owned key by default. A CMK adds cost and a new key per rebuild."],
                 ["Prefix delegation, set before nodes join", "Raises max pods on a t3.medium from 17 to 110. The CNI setting must exist before nodes join or they keep the old limit."],
                 ["IMDSv2 required, hop limit 1", "Pods can't reach the node's own AWS credentials."],
                 ["Account guard as a postcondition", "Rejected depends_on on modules, which defers every lookup inside them to apply time and produces noisy, sometimes phantom diffs."]],
                [0.36, 0.64]),
          P("One-command build and teardown", "h2"),
          P(code("down.sh") + " runs in a deliberate order: delete Kubernetes-made load balancers first, wait "
            "until AWS confirms they are gone, then " + code("terraform destroy") + ". Load balancers created "
            "by Kubernetes are invisible to Terraform; destroying first would orphan them, still billing and "
            "blocking VPC deletion. If they never disappear, the script exits <b>without</b> destroying, which I "
            "tested with stand-in commands."),
          bug("A cleanup line that could abort the cleanup (caught before delivery)",
              "a check like " + code('[[ -n "$ns" ]] &amp;&amp; kubectl ...') + " inside a piped loop.",
              "on an empty line it returns non-zero, and with " + code("set -e") + " plus " + code("pipefail") +
              " that would stop " + code("down.sh") + " halfway through.",
              "an explicit " + code("if") + "; ShellCheck clean on every script."),
          shot("20-cluster-plan.png", "First cluster plan: 50 resources to add."),
          shot("21-cluster-up.png", "Cluster built in 14 minutes 34 seconds, both nodes Ready."),
          shot("22-eks-console.png", "EKS console: Active, version 1.36, standard support until August 2027, no issues."),
          callout("surprise", "An OIDC URL with IRSA turned off",
                  "The console still shows an OpenID Connect provider URL. Every EKS cluster has an OIDC "
                  "<b>issuer</b> (it signs ServiceAccount tokens); IRSA additionally registers an IAM <b>identity "
                  "provider</b> pointing at it. Turning IRSA off skips only that registration: the issuer exists, "
                  "the IAM trust doesn't."),
          shot("23-pods-110-and-system-pods.png", "Each node allows 110 pods (not 17); all 8 system pods Running."),
          P("My first check command used " + code("head -4") + ", which cut off after node 1's lines. "
            "I replaced it with a one-line-per-node query, which showed both nodes at 110 pods, one in "
            "us-east-1a and one in us-east-1b, each in its planned subnet."),
          bug("Leftovers that weren't there",
              code("down.sh") + " reported a NAT gateway and two subnets still existing after a clean destroy.",
              "the tagging index it queried lags deletions by minutes, and deleted NAT gateways stay visible "
              "(state \"deleted\") for about an hour. I confirmed directly with each service before changing anything.",
              code("down.sh") + " now asks each service directly (EKS, EC2, NAT, Elastic IPs, VPC, load balancers) "
              "and prints an ok/LEFTOVER checklist."),
          callout("lesson", "Ask the service, not the index",
                  "An index or cache answering \"what exists?\" is not the source of truth. For anything that bills, ask the service itself."),
          shot("24-cluster-down-and-verification.png", "Teardown: 50 destroyed in 12 minutes 52 seconds, then direct checks proving the reported leftovers were already gone."),
          P("<b>Phase 2 result:</b> the full cycle (build, verify, destroy) took about 27 minutes of cluster time, with nothing left behind.")]

# ---------------------------------------------------------------- Phase 3
story += [PageBreak(), P("6. Phase 3 (in progress): Patrol in the cluster", "h1"),
          P("Approved plan", "h2"),
          table([["Decision", "Chosen", "Rejected, and why"],
                 ["Repository", "Public GitHub repo; nothing secret committed", "Private repo: Argo CD would need a stored deploy key, and the code is portfolio material"],
                 ["HTTPS", "HTTP first, then delegate patrol.cloudforger.app to Route 53, ACM certificate, ExternalDNS", "Hand-made DNS record: breaks on every rebuild because the load balancer address changes"],
                 ["Argo CD install", "up.sh installs it with a pinned Helm chart; one root app installs the rest", "Terraform's Helm provider: blurs \"Terraform owns AWS, Argo CD owns the cluster\""],
                 ["First images", "Pushed from my Mac, tagged by commit", "Waiting for CI (Phase 4 automates exactly this)"],
                 ["Private AWS access", "Free gateway endpoints for DynamoDB and S3", "NAT only: a NAT failure would break checks, the status page and image pulls"],
                 ["Pod roles", "IAM roles permanent; Pod Identity links rebuilt with the cluster", "Roles in the cluster layer: recreated every session for no reason"]],
                [0.17, 0.38, 0.45]),
          P("Design rule from my identity lessons: <b>the pod is the security boundary</b>. Anything that needs "
            "different AWS permissions gets its own pod and ServiceAccount."),
          P("Step 1. Repository", "h2"),
          P("Before the first commit, a safety check flagged " + code("terraform.tfvars.example") + ". Rather than "
            "assume either way, I checked its staged content: only a placeholder email. 54 files were staged, "
            "including the Terraform lock files and none of the state, real tfvars, " + code(".terraform/") +
            " or " + code(".venv/") + " folders."),
          shot("25-first-push.png", "First push to GitHub (commit a8577cf).", max_h=2.6 * inch),
          shot("26-github-repo.png", "The public repository."),
          P("Step 2. Pod Identity roles and gateway endpoints (built, not yet applied)", "h2"),
          table([["Workload", "Allowed actions", "Scope"],
                 ["scheduler", "sqs:SendMessage", "the checks queue only"],
                 ["worker", "sqs:ReceiveMessage, sqs:DeleteMessage, dynamodb:PutItem", "the checks queue and table only"],
                 ["status-page", "dynamodb:Query", "the checks table only"],
                 ["LB controller", "Official AWS policy for controller v2.17.1", "pinned file, replaced on upgrade"]],
                [0.18, 0.5, 0.32]),
          bullets(["<b>Locked trust policies.</b> Each role trusts the EKS Pod Identity service <b>and</b> requires the "
                   "exact namespace and ServiceAccount (session tags Pod Identity adds). Even a mistaken link can't "
                   "hand a role to the wrong pod. Rejected the bare trust policy, which lets any link use any role.",
                   "<b>One source of truth.</b> The cluster layer reads the role list from the permanent layer's "
                   "state to create the links, so the pairing can't drift between layers.",
                   "<b>Managed policies</b>, not inline: the controller's policy is large, and managed policies are visible in the console.",
                   "<b>Gateway endpoints</b> left at the default policy: each pod's role already decides permissions, and a "
                   "strict S3 endpoint policy would also have to allow ECR's own storage buckets."]),
          callout("draft", "Still to come in this section",
                  ["Screenshots 27 to 29 (plan, apply, trust policy), then: the image push script, the Patrol Helm chart, "
                   "Argo CD, the AWS Load Balancer Controller, the status page live behind a load balancer, and HTTPS."]),
          P("7. Bugs and lessons so far", "h1"),
          table([["Symptom", "Cause", "Fix"],
                 ["terraform init: no space left", "Disk 98% full; a provider copy per folder", "Shared plugin cache; freed to 26 GB"],
                 ["Empty Slack secret rejected", "Variable lost between terminal sessions", "Read, validate and store in one command"],
                 ["pip can't find boto3", "python3 was 3.8", "Venv from Python 3.13; version guard"],
                 ["\"1 ms\" for a dead site", "Time-to-failure shown as latency", "Show latency only with a response"],
                 ["Script crash on macOS only", "bash 3.2 empty array + set -u", "Safe expansion form"],
                 ["Cleanup could abort itself", "&amp;&amp; in a loop under set -e/pipefail", "Explicit if"],
                 ["Phantom leftovers after destroy", "Tagging index lags deletions", "Ask each service directly"]],
                [0.32, 0.36, 0.32])]

doc = BaseDocTemplate(OUT, pagesize=LETTER, leftMargin=M, rightMargin=M, topMargin=M, bottomMargin=M,
                      title="Patrol: Kubernetes uptime monitor on Amazon EKS (Draft 1)",
                      author="Steven Waldron")
frame = Frame(M, M, TW, H - 2 * M, id="f")
doc.addPageTemplates([PageTemplate("cover", [frame], onPage=on_cover),
                      PageTemplate("body", [frame], onPage=on_page)])
doc.build(story)
print("built", OUT)
