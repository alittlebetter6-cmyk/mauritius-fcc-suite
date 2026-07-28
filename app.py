"""
Mauritius FCC Suite — Streamlit application.

Run:  streamlit run app.py
"""
from __future__ import annotations

import csv
import datetime as dt
import io
import json

import streamlit as st

from fcc_suite import ai_engine, legislation, matching, news, risk, sanctions, store

st.set_page_config(page_title="Mauritius FCC Suite", page_icon="⚖️", layout="wide")
store.init_db()

st.markdown("""
<style>
:root { --ink:#0f2540; --accent:#0a6c74; }
.block-container { padding-top: 1.2rem; max-width: 1200px; }
h1, h2, h3 { color: var(--ink); letter-spacing:-.01em; }
.stTabs [data-baseweb="tab"] { font-weight:600; }
.badge { display:inline-block; padding:2px 10px; border-radius:999px;
  font-size:.72rem; font-weight:700; letter-spacing:.03em; }
.b-strong{background:#7a1020;color:#fff}.b-probable{background:#b4531f;color:#fff}
.b-possible{background:#8a6d0f;color:#fff}.b-clear{background:#14603a;color:#fff}
.b-high{background:#7a1020;color:#fff}.b-medium{background:#8a6d0f;color:#fff}
.b-low{background:#14603a;color:#fff}.b-prohibited{background:#440a12;color:#fff}
.b-true_match{background:#7a1020;color:#fff}.b-false_positive{background:#14603a;color:#fff}
.b-needs_review{background:#8a6d0f;color:#fff}.b-unreviewed{background:#5a6b7b;color:#fff}
.small{color:#5a6b7b;font-size:.82rem}
.card{border:1px solid #e2e8ee;border-radius:12px;padding:14px 16px;margin-bottom:10px;background:#fff}
</style>
""", unsafe_allow_html=True)


def badge(text: str, cls: str) -> str:
    return f'<span class="badge b-{str(cls).lower()}">{text}</span>'


# ---- sidebar: identity + system status ------------------------------------
with st.sidebar:
    st.markdown("### Operator")
    officer = st.text_input("Officer / MLRO name", key="officer",
                            placeholder="e.g. M. Prudence")
    if not officer:
        st.caption("⚠ Enter your name to record decisions to the audit log.")
    st.divider()
    s = store.stats()
    st.markdown("### System")
    st.metric("Open cases", s["open_cases"])
    st.metric("Screenings logged", s["screenings"])
    st.metric("Audit events", s["audit_events"])
    ok, bad = store.verify_audit_chain()
    if ok:
        st.success("Audit chain intact 🔒")
    else:
        st.error(f"Audit chain broken at #{bad} — investigate tampering.")
    st.divider()
    st.markdown(f"**AI analyst:** {'🟢 online' if ai_engine.is_available() else '⚪ offline'}")

ai_on = ai_engine.is_available()

st.title("⚖️ Mauritius FCC Suite")
st.markdown('<div class="small">AML / CFT / CPF compliance intelligence for '
            'FSC · FCC · FIU · Bank of Mauritius regulated entities — '
            'grounded in the Mauritian framework (FIAMLA 2002 → AMLA 2026).</div>',
            unsafe_allow_html=True)

tabs = st.tabs(["🏠 Dashboard", "🔎 Sanctions Screening", "📊 Risk Assessment",
                "🗂️ Cases & Audit", "📚 Legislation", "🤖 AI Analyst", "📰 News"])


def _open_case_picker(key: str):
    """Return (case_id or None). Lets user pick an open case or create one inline."""
    cases = store.list_cases()
    labels = ["— select case —"] + [f"#{c['id']} · {c['ref']} · {c['subject']} [{c['status']}]"
                                    for c in cases]
    idx = st.selectbox("Attach to case", range(len(labels)),
                       format_func=lambda i: labels[i], key=f"pick_{key}")
    if idx == 0:
        with st.expander("➕ Create a new case"):
            ref = st.text_input("Case reference", key=f"nref_{key}",
                                value=f"CASE-{dt.date.today():%Y%m%d}")
            subj = st.text_input("Subject", key=f"nsubj_{key}")
            stype = st.selectbox("Type", ["individual", "entity"], key=f"ntype_{key}")
            if st.button("Create case", key=f"ncreate_{key}"):
                if not officer:
                    st.error("Enter your officer name in the sidebar first.")
                elif not (ref and subj):
                    st.error("Reference and subject required.")
                else:
                    try:
                        cid = store.create_case(ref, subj, stype, officer)
                        st.session_state[f"pickedcase_{key}"] = cid
                        st.success(f"Case #{cid} created — now save below.")
                    except Exception as e:
                        st.error(f"Could not create case: {e}")
        return st.session_state.get(f"pickedcase_{key}")
    return cases[idx - 1]["id"]


# ============================ DASHBOARD ====================================
with tabs[0]:
    st.subheader("Compliance command centre")
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Acts in library", len(legislation.acts()))
    c2.metric("Sanctions sources", len(sanctions.SOURCES) + 1)
    c3.metric("Open cases", s["open_cases"])
    c4.metric("Framework", "AMLA 2026")
    st.markdown("""
This suite gives a Mauritian compliance officer one place to **screen**, **risk-rate**,
**research the law**, and **record every decision** for FSC/FIU inspection.

- **Sanctions screening** — UN / OFAC / UK **plus the Mauritius domestic (NSSec) list**, fuzzy-matched.
- **Risk assessment** — transparent ML / TF / CPF model, EDD flagging.
- **Cases & audit** — every screening and adjudication is recorded to a **tamper-evident, hash-chained** log.
- **Legislation library** — curated map to the right Act/section.
- **AI analyst** — red flags, typologies, STR drafting, grounded in local law.
""")
    st.info("Decision-support only — not legal advice. A human MLRO adjudicates every "
            "match, EDD decision and STR. Verify legislation against the Gazette.", icon="ℹ️")

# ============================ SANCTIONS =====================================
with tabs[1]:
    st.subheader("Sanctions & watchlist screening")
    colq, colo = st.columns([0.6, 0.4])
    with colq:
        name = st.text_input("Name to screen", placeholder="e.g. Ivan Volkov / Alpha Trading Ltd")
        is_entity = st.checkbox("This is an entity / company", value=False)
        threshold = st.slider("Match sensitivity (lower = more recall)", 60, 95, 80)
    with colo:
        picks = st.multiselect("Global lists", list(sanctions.SOURCES.keys()),
                               default=list(sanctions.SOURCES.keys()))
        st.caption("The Mauritius domestic (NSSec) list is **always** included — "
                   "screening it is a legal duty (UN Sanctions Act 2019 s.25).")
        force = st.checkbox("Force refresh from source", value=False)

    @st.cache_data(ttl=6 * 3600, show_spinner=False)
    def _load_lists(codes_key: tuple):
        recs, stats_ = sanctions.load_all(list(codes_key) or None)
        return recs, [vars(x) for x in stats_]

    if st.button("Screen", type="primary"):
        with st.spinner("Loading sanctions lists (first run can take a minute)…"):
            if force:
                _load_lists.clear()
                records, statuses = sanctions.load_all(picks or None, force=True)
                statuses = [vars(x) for x in statuses]
            else:
                records, statuses = _load_lists(tuple(picks or []))
            res = matching.screen_name(name, records, is_entity=is_entity,
                                       threshold=float(threshold)) if name.strip() else None
        st.session_state["screen_statuses"] = statuses
        if res is not None:
            st.session_state["screen_result"] = {
                "query": res.query, "band": res.band, "top": res.top_score,
                "is_entity": is_entity,
                "hits": [{"matched_name": h.matched_name, "score": h.score,
                          "field": h.matched_field, "record": h.record} for h in res.hits]}
        else:
            st.session_state.pop("screen_result", None)

    statuses = st.session_state.get("screen_statuses")
    if statuses:
        cols = st.columns(len(statuses))
        for col, stt in zip(cols, statuses):
            if stt["code"] == "MU-NSS" and stt["count"] == 0:
                tag = "not set up"
            else:
                tag = {"live": "🟢 live", "cache": "🟡 cached", "demo": "⚪ demo"}.get(stt["source"], stt["source"])
            col.metric(stt["code"], f"{stt['count']:,}", tag)
        global_demo = all(s["source"] == "demo" for s in statuses if s["code"] not in ("MU-NSS",))
        if global_demo:
            st.error("⚠️ **Sample data only** — the live UN/OFAC/UK lists could not be "
                     "fetched, so this screening ran against a tiny built-in demo set. "
                     "Results are NOT a real sanctions check. Tick **Force refresh** and "
                     "screen again to retry the live download.")
        elif any(s["source"] == "cache" for s in statuses):
            st.caption("Some lists served from local cache (refreshed at most every 24h). "
                       "Tick Force refresh for the very latest designations.")

    sr = st.session_state.get("screen_result")
    if sr:
        if not sr["hits"]:
            st.success(f"No matches at/above threshold. {badge('CLEAR','clear')}", icon="✅")
        else:
            st.markdown(f"**Result:** {badge(sr['band'], sr['band'])} · top score {sr['top']}",
                        unsafe_allow_html=True)
            for h in sr["hits"]:
                r = h["record"]
                b = matching.ScreenResult("x", [matching.ScreenHit(sr["query"], h["matched_name"],
                     h["score"], r, h["field"])]).band
                prov = ""
                if r.get("list") == "MU-NSS":
                    prov = (f'<br><span class="small">NSSec notice: {r.get("notice_ref","—")} · '
                            f'Gazette: {r.get("gazette_date","—")}</span>')
                st.markdown(
                    f'<div class="card">{badge(str(round(h["score"])),b)} '
                    f'&nbsp;<b>{h["matched_name"]}</b> <span class="small">({h["field"]})</span><br>'
                    f'<span class="small">Type: {r.get("type","?")} · List: {r.get("list","?")} · '
                    f'Programme: {r.get("program","—")} · Country: {r.get("country","—")}</span>{prov}</div>',
                    unsafe_allow_html=True)
            st.warning("Automated match is a lead, not a determination. On a confirmed true "
                       "match: freeze without delay and report to the NSSec (UN Sanctions Act 2019).")

        st.markdown("##### Record this screening")
        adj = st.radio("Adjudication", store.ADJUDICATIONS, horizontal=True,
                       format_func=lambda a: a.replace("_", " ").title(), key="adj_screen")
        rationale = st.text_area("Rationale (recorded to audit log)", key="rat_screen",
                                 placeholder="Basis for the decision, identifiers checked, next steps…")
        case_id = _open_case_picker("screen")
        if st.button("💾 Save screening to case", type="primary"):
            if not officer:
                st.error("Enter your officer name in the sidebar first.")
            elif not case_id:
                st.error("Select or create a case.")
            else:
                store.add_screening(case_id, officer, sr["query"], sr["is_entity"],
                                    sr["band"], sr["top"], sr["hits"], adj, rationale)
                st.success(f"Screening recorded to case #{case_id} and audit log.")

    with st.expander("🇲🇺 Manage Mauritius domestic (NSSec) list"):
        meta = sanctions.mu_nss_meta()
        st.caption(meta.get("obligation", ""))
        cur = sanctions.load_mu_nss()
        st.write(f"Current domestic designations: **{len(cur)}**")
        dn = st.text_input("Name", key="nss_name")
        dc1, dc2, dc3 = st.columns(3)
        dtype = dc1.selectbox("Type", ["individual", "entity"], key="nss_type")
        dprog = dc2.text_input("Programme", value="DOMESTIC-TF", key="nss_prog")
        dctry = dc3.text_input("Country (ISO-2)", value="MU", key="nss_ctry")
        dref = dc1.text_input("NSSec notice ref", key="nss_ref")
        dgaz = dc2.text_input("Gazette date", key="nss_gaz", placeholder="YYYY-MM-DD")
        ddate = dc3.text_input("Designation date", key="nss_date", placeholder="YYYY-MM-DD")
        if st.button("Add domestic designation"):
            if not officer:
                st.error("Enter your officer name in the sidebar first.")
            elif not dn.strip():
                st.error("Name required.")
            else:
                sanctions.add_mu_nss_entry({"name": dn, "type": dtype, "program": dprog,
                    "country": dctry, "notice_ref": dref, "gazette_date": dgaz,
                    "designation_date": ddate, "added_by": officer})
                store.log_event(officer, "nss.designate", "mu_nss", dn,
                                {"notice_ref": dref, "gazette_date": dgaz})
                st.success(f"Added '{dn}' to the Mauritius domestic list.")

# ============================ RISK ==========================================
with tabs[2]:
    st.subheader("Client / entity risk assessment")
    st.caption("Transparent weighted model aligned to the FSC AML/CFT Handbook and the "
               "AMLA 2026 three-pillar (ML/TF/CPF) approach.")
    a, b, c = st.columns(3)
    with a:
        country = st.text_input("Country (ISO-2)", value="MU", max_chars=2, key="risk_country").upper()
        is_pep = st.checkbox("PEP exposure", key="risk_pep")
        complex_structure = st.checkbox("Complex / opaque ownership")
    with b:
        sband = st.selectbox("Sanctions screening outcome", ["CLEAR", "POSSIBLE", "PROBABLE", "STRONG"], key="risk_sband")
        product = st.selectbox("Product risk", ["standard", "high"])
        cash = st.checkbox("Cash-intensive business")
    with c:
        nonface = st.checkbox("Non-face-to-face onboarding")
        media = st.checkbox("Adverse media")

    if st.button("Assess risk", type="primary"):
        rr = risk.assess(country=country, is_pep=is_pep, sanctions_band=sband,
                         product_risk=product, delivery_nonface=nonface,
                         adverse_media=media, cash_intensive=cash,
                         complex_structure=complex_structure)
        st.session_state["risk_result"] = {"score": rr.score, "band": rr.band,
            "pillars": rr.pillar_flags,
            "factors": [{"label": f.label, "points": f.points, "rationale": f.rationale}
                        for f in rr.factors]}

    rk = st.session_state.get("risk_result")
    if rk:
        m1, m2, m3 = st.columns([0.3, 0.3, 0.4])
        m1.metric("Risk score", f"{rk['score']}/100")
        m2.markdown("**Rating**  \n" + badge(rk["band"], rk["band"]), unsafe_allow_html=True)
        m3.markdown("**Pillars engaged**  \n" +
                    (" ".join(badge(p, "possible") for p in rk["pillars"]) or "—"),
                    unsafe_allow_html=True)
        if rk["band"] in ("HIGH", "PROHIBITED"):
            st.error("**Enhanced Due Diligence required** — senior sign-off and documented rationale.")
        st.markdown("#### Contributing factors")
        for f in sorted(rk["factors"], key=lambda x: x["points"], reverse=True):
            st.markdown(f'<div class="card"><b>{f["label"]}</b> '
                        f'<span class="small">(+{round(f["points"],1)})</span><br>'
                        f'<span class="small">{f["rationale"]}</span></div>', unsafe_allow_html=True)
        if "CPF" in rk["pillars"]:
            st.info("CPF pillar engaged — AMLA 2026 requires a distinct proliferation-financing "
                    "risk assessment and dedicated screening.", icon="⚠️")

        st.markdown("##### Record this assessment")
        note = st.text_input("Note", key="risk_note")
        case_id = _open_case_picker("risk")
        if st.button("💾 Save assessment to case", type="primary"):
            if not officer:
                st.error("Enter your officer name in the sidebar first.")
            elif not case_id:
                st.error("Select or create a case.")
            else:
                store.add_risk(case_id, officer, rk["score"], rk["band"],
                               rk["pillars"], rk["factors"], note)
                st.success(f"Risk assessment recorded to case #{case_id} and audit log.")

# ============================ CASES & AUDIT =================================
with tabs[3]:
    st.subheader("Cases & audit trail")
    left, right = st.columns([0.42, 0.58])

    with left:
        st.markdown("#### Cases")
        with st.expander("➕ New case", expanded=False):
            r = st.text_input("Reference", value=f"CASE-{dt.date.today():%Y%m%d}", key="c_ref")
            sub = st.text_input("Subject", key="c_sub")
            typ = st.selectbox("Type", ["individual", "entity"], key="c_typ")
            nts = st.text_area("Notes", key="c_nts")
            if st.button("Create", key="c_create"):
                if not officer:
                    st.error("Enter your officer name in the sidebar first.")
                elif not (r and sub):
                    st.error("Reference and subject required.")
                else:
                    try:
                        store.create_case(r, sub, typ, officer, nts)
                        st.success("Case created.")
                    except Exception as e:
                        st.error(f"{e}")
        fstatus = st.selectbox("Filter", ["all"] + store.CASE_STATUSES)
        cases = store.list_cases(None if fstatus == "all" else fstatus)
        sel = None
        for cs in cases:
            if st.button(f"#{cs['id']} · {cs['ref']} — {cs['subject']}  [{cs['status']}]",
                         key=f"open_{cs['id']}", use_container_width=True):
                st.session_state["sel_case"] = cs["id"]
        if not cases:
            st.caption("No cases yet.")

    with right:
        sel = st.session_state.get("sel_case")
        if not sel:
            st.info("Select a case on the left to view its file.")
        else:
            case = store.get_case(sel)
            if case:
                st.markdown(f"### {case['ref']} — {case['subject']}")
                st.markdown(f"<span class='small'>Type: {case['subject_type']} · "
                            f"Created by {case['created_by']} · "
                            f"{dt.datetime.fromtimestamp(case['created_ts']):%Y-%m-%d %H:%M}</span>",
                            unsafe_allow_html=True)
                ncol1, ncol2 = st.columns([0.5, 0.5])
                newst = ncol1.selectbox("Status", store.CASE_STATUSES,
                                        index=store.CASE_STATUSES.index(case["status"]), key="setst")
                if ncol2.button("Update status"):
                    if officer:
                        store.update_case_status(sel, newst, officer)
                        st.success("Status updated.")
                    else:
                        st.error("Enter your officer name first.")
                if case["notes"]:
                    st.caption(case["notes"])

                st.markdown("#### Screenings")
                scr = store.get_case_screenings(sel)
                if not scr:
                    st.caption("None recorded.")
                for x in scr:
                    st.markdown(
                        f'<div class="card">{badge(x["band"], x["band"])} '
                        f'{badge(x["adjudication"], x["adjudication"])} '
                        f'&nbsp;<b>{x["query"]}</b> <span class="small">score {x["top_score"]} · '
                        f'{dt.datetime.fromtimestamp(x["ts"]):%Y-%m-%d %H:%M} · {x["actor"]}</span>'
                        f'{"<br><span class=\"small\">"+x["rationale"]+"</span>" if x["rationale"] else ""}</div>',
                        unsafe_allow_html=True)

                st.markdown("#### Risk assessments")
                rks = store.get_case_risks(sel)
                if not rks:
                    st.caption("None recorded.")
                for x in rks:
                    st.markdown(
                        f'<div class="card">{badge(x["band"], x["band"])} '
                        f'&nbsp;score <b>{x["score"]}</b> · pillars {x["pillars"] or "—"} '
                        f'<span class="small">· {dt.datetime.fromtimestamp(x["ts"]):%Y-%m-%d %H:%M} · '
                        f'{x["actor"]}</span></div>', unsafe_allow_html=True)

                st.markdown("#### Audit trail")
                for e in store.get_audit_trail(sel):
                    st.markdown(f'<div class="small">{dt.datetime.fromtimestamp(e["ts"]):%Y-%m-%d %H:%M} · '
                                f'<b>{e["action"]}</b> · {e["actor"]} · '
                                f'<code>{e["row_hash"][:10]}…</code></div>', unsafe_allow_html=True)

                st.download_button("⬇ Export case file (JSON)",
                    data=json.dumps(store.export_case(sel), indent=2, default=str),
                    file_name=f"{case['ref']}_casefile.json", mime="application/json")

    st.divider()
    st.markdown("#### Global audit log")
    okc, badc = store.verify_audit_chain()
    st.markdown(("🔒 **Chain intact** — no evidence of tampering."
                 if okc else f"🚨 **Chain broken at event #{badc}** — records altered.") )
    trail = store.get_audit_trail(None, limit=1000)
    if trail:
        buf = io.StringIO()
        w = csv.DictWriter(buf, fieldnames=["ts", "actor", "action", "entity_type",
                                            "entity_id", "details", "row_hash"])
        w.writeheader()
        for e in trail:
            w.writerow({k: e[k] for k in w.fieldnames})
        st.download_button("⬇ Export full audit log (CSV)", data=buf.getvalue(),
                           file_name="audit_log.csv", mime="text/csv")

# ============================ LEGISLATION ===================================
with tabs[4]:
    st.subheader("Mauritian AML/CFT/CPF legislation library")
    q = st.text_input("Search the framework",
                      placeholder="e.g. STR reporting, beneficial ownership, CPF, s.18, penalties")
    hits = legislation.search(q) if q.strip() else [legislation.LegHit(a, 0, "") for a in legislation.acts()]
    for h in hits:
        a = h.act
        with st.expander(f"{a['short']} — {a['title']}", expanded=bool(q.strip())):
            st.markdown(f"<span class='small'>Status: {a['status']} · "
                        f"Regulator: {', '.join(a.get('regulator', []))}</span>", unsafe_allow_html=True)
            st.write(a.get("summary", ""))
            for s2 in a.get("key_sections", []) or []:
                st.markdown(f"- **{s2['ref']}** — {s2['topic']}: {s2['note']}")
            for k in a.get("key_changes", []) or []:
                st.markdown(f"- {k}")
            for o in a.get("obligations", []) or []:
                st.markdown(f"- {o}")
            if a.get("practitioner_notes"):
                st.info(a["practitioner_notes"], icon="🧭")
    st.caption(legislation.meta()["disclaimer"])

# ============================ AI ANALYST ====================================
with tabs[5]:
    st.subheader("AI analyst")
    if not ai_on:
        st.warning("AI analyst is offline. Set `ANTHROPIC_API_KEY` and `pip install anthropic`. "
                   "Screening, risk, cases and legislation all work without it.")
    task = st.selectbox("Task", ["Red-flag review", "Typology detection",
                                 "Draft STR narrative", "Ask about a provision"])
    default = {
        "Red-flag review": "New GBC client incorporated last month; UBO is a foreign PEP; "
                           "funds routed through three intermediary jurisdictions; invoices "
                           "lack commercial rationale.",
        "Typology detection": "Series of round-figure inbound transfers just under the "
                             "reporting threshold, immediately withdrawn in cash.",
        "Draft STR narrative": "Client XYZ Ltd received USD 480,000 across 6 transfers in 8 "
                              "days from an unrelated shell in a high-risk jurisdiction, then "
                              "wired it out to a fourth party.",
        "Ask about a provision": "What are my obligations when I get a probable UN sanctions "
                                "match on a client in Mauritius?",
    }[task]
    text = st.text_area("Input", value=default, height=140)
    if st.button("Run analysis", type="primary", disabled=not ai_on):
        with st.spinner("Analysing…"):
            fn = {"Red-flag review": ai_engine.analyse_red_flags,
                  "Typology detection": ai_engine.detect_typology,
                  "Draft STR narrative": ai_engine.draft_str_narrative,
                  "Ask about a provision": ai_engine.explain_provision}[task]
            out = fn(text)
        if out.ok:
            st.markdown(out.text)
            st.caption(f"Model: {out.model} · decision-support only; MLRO must adjudicate.")
        else:
            st.error(out.error)

# ============================ NEWS ==========================================
with tabs[6]:
    st.subheader("Regulatory & financial-crime news")
    st.caption("FATF · OFAC · UN plus keyword-filtered local coverage. Edit feeds in fcc_suite/news.py.")
    if st.button("Refresh feed", type="primary"):
        with st.spinner("Fetching…"):
            items, errs = news.fetch_news()
        if errs:
            with st.expander(f"{len(errs)} feed warning(s)"):
                for e in errs:
                    st.caption(e)
        for it in items:
            scope = "🌍" if it.scope == "international" else "🇲🇺"
            link = f'· <a href="{it.link}">open</a>' if it.link else ""
            st.markdown(f'<div class="card">{scope} <b>{it.title}</b><br>'
                        f'<span class="small">{it.source} · {it.date_str}</span> {link}'
                        f'<br><span class="small">{it.summary}</span></div>', unsafe_allow_html=True)
        if not items and not errs:
            st.warning("No items retrieved.")

st.markdown("---")
st.caption(f"Mauritius FCC Suite · {dt.date.today().year} · decision-support tool — not legal "
           "advice. Verify all legislation against the official Gazette.")
